"""入力と参照に共通の、原文位置付き凍結BERT表現。正誤・語義は判定しない。"""

import copy
import hashlib
import importlib.metadata
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
from spacy_alignments import get_alignments
from transformers import AutoModel, AutoTokenizer
from transformers.utils.hub import get_checkpoint_shard_files

from native_japanese.structure import Document

MODEL_ID = "cl-tohoku/bert-base-japanese-v3"
MODEL_REVISION = "65243d6e5629b969c77309f217bd7b1a79d43c7e"


@dataclass(frozen=True, slots=True)
class EncoderFingerprint:
    """設定と実ファイルの同一性。local sourceの上流revision一致を認証するものではない。"""

    model_id: str
    revision: str
    tokenizer_class: str
    artifacts: tuple[tuple[str, str], ...]
    versions: tuple[tuple[str, str], ...]
    window_length: int
    overlap: int
    alignment: str = "spacy-alignments-wordpiece-unknown-surfaces-codepoint-exclusive-v2"
    pooling: str = "complete-token-max-min-context-earliest-window-subword-mean-v1"

    @property
    def digest(self) -> str:
        """来歴と処理契約を固定したSHA-256。"""
        from dataclasses import asdict

        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EncodedDocument:
    """行はraw token ID順。無効行はNaNで、理由を正用へ読み替えない。

    token_windowsは採用window番号（無効=-1）。window_spansはそのwindowの
    有効に整列したsubwordが覆う原文codepoint範囲で、整列不能のみならNone。
    window外の文脈が必要かどうかはこの表現だけでは決まらない。
    """

    source_id: str
    offsets: tuple[tuple[int, int], ...]
    vectors: np.ndarray
    valid: np.ndarray
    reasons: tuple[str | None, ...]
    token_windows: np.ndarray
    window_spans: tuple[tuple[int, int] | None, ...]
    fingerprint: EncoderFingerprint


@dataclass(slots=True)
class _Prepared:
    document: Document
    ids: list[int]
    mapping: list[tuple[int, ...]]
    reasons: list[str | None]
    offsets: list[tuple[int, int] | None]
    windows: list[tuple[int, int]]
    selected: list[int]
    window_tokens: list[list[int]]


def _align(document: Document, ids: list[int], tokenizer, word_tokenizer=None):
    """既存文字整列を使い、独自正規化や文字列探索による補修をしない。

    1subwordが複数raw tokenにまたがると、文字対応が一意でもraw tokenごとの
    独立した位置表現ではないためshared_subwordとして全該当位置を保留する。
    """
    # 既存tokenize()にadded/special処理を委ね、UNKへ失われる前の単語を保持する。
    if word_tokenizer is None:
        word_tokenizer = copy.copy(tokenizer)
        word_tokenizer.do_subword_tokenize = False
    pieces, surfaces = [], []
    added_tokens = tokenizer.added_tokens_encoder
    for word in word_tokenizer.tokenize(document.text):
        group = [word] if word in added_tokens else tokenizer.subword_tokenizer.tokenize(word)
        pieces.extend(group)
        # WordPieceの[UNK]は単語全体の失敗を表す。位置だけを保ち、表現は無効にする。
        surfaces.extend([word] if group == [tokenizer.unk_token] else group)
    if tokenizer.convert_tokens_to_ids(pieces) != ids:
        return (
            [() for _ in document.tokens],
            ["tokenization_mismatch" for _ in document.tokens],
            [None for _ in ids],
        )
    special_ids = set(tokenizer.all_special_ids)
    char_to_piece, piece_to_char = get_alignments(list(document.text), surfaces)
    offsets, piece_reasons = [], []
    for index, (tid, chars) in enumerate(zip(ids, piece_to_char, strict=True)):
        chars = sorted(set(chars))
        reason = None
        if tid == tokenizer.unk_token_id:
            reason = "unknown"
        elif tid in special_ids:
            reason = "special"
        elif not chars:
            reason = "empty"
        elif chars != list(range(chars[0], chars[-1] + 1)):
            reason = "noncontiguous"
        elif any(char_to_piece[c] != [index] for c in chars):
            reason = "ambiguous"
        offsets.append(None if reason else (chars[0], chars[-1] + 1))
        piece_reasons.append(reason)

    mapping, reasons = [], []
    for token in document.tokens:
        chars = range(token.start, token.end)
        selected = tuple(sorted({p for c in chars for p in char_to_piece[c]}))
        reason = None
        if not selected:
            reason = "unmapped"
        elif any(len(char_to_piece[c]) != 1 for c in chars):
            reason = "incomplete_or_ambiguous"
        elif any(piece_reasons[p] for p in selected):
            reason = next(piece_reasons[p] for p in selected if piece_reasons[p])
        elif selected != tuple(range(selected[0], selected[-1] + 1)):
            reason = "noncontiguous"
        elif any(offsets[p][0] < token.start or offsets[p][1] > token.end for p in selected):
            reason = "shared_subword"
        mapping.append(selected)
        reasons.append(reason)
    return mapping, reasons, offsets


class FrozenBertEncoder:
    """一度loadした本体を常駐させ、encodeでは異なる原文のwindowもbatch化する。

    生の原文をtokenizerへ渡す。整列はtokenizerの正規化を再実装せず、不明な
    対応を保留する。全tokenの同時forwardであり、mask位置の反復は行わない。
    """

    def __init__(self, model, tokenizer, fingerprint: EncoderFingerprint):
        self.model = model.cpu().float().eval().requires_grad_(False)
        self.tokenizer = tokenizer
        if not (
            getattr(tokenizer, "do_word_tokenize", False)
            and getattr(tokenizer, "do_subword_tokenize", False)
            and getattr(tokenizer, "subword_tokenizer_type", None) == "wordpiece"
        ):
            raise ValueError("単語分割とWordPieceを有効にしたBertJapaneseTokenizerが必要です")
        self._word_tokenizer = copy.copy(tokenizer)
        self._word_tokenizer.do_subword_tokenize = False
        self.fingerprint = fingerprint
        self.capacity = fingerprint.window_length - tokenizer.num_special_tokens_to_add(pair=False)
        if not 0 < self.capacity or not 0 <= fingerprint.overlap < self.capacity:
            raise ValueError("windowには内容を含め、overlapは内容長未満の0以上にしてください")
        if fingerprint.window_length > model.config.max_position_embeddings:
            raise ValueError("window_lengthがモデルの位置数上限を超えています")
        if tokenizer.padding_side != "right":
            raise ValueError("位置対応のため右paddingのtokenizerを指定してください")

    @classmethod
    def load(
        cls,
        *,
        cpu_threads: int,
        local_model_path: str | Path | None = None,
        window_length: int = 512,
        overlap: int = 64,
    ) -> "FrozenBertEncoder":
        """固定revisionをローカル限定で読む。初回の読込・hash費用はencodeと別。

        local_model_path指定時は実ファイルhashを保存する。上流の固定revisionと
        同じファイルを準備する責任は呼出側にあり、自動downloadはしない。
        CPU thread数はprocess共有設定なので呼出側が明示する。
        """
        if type(cpu_threads) is not int or cpu_threads < 1:
            raise ValueError("cpu_threadsは1以上の整数を明示してください")
        if type(window_length) is not int or type(overlap) is not int:
            raise ValueError("window_lengthとoverlapは整数で指定してください")
        torch.set_num_threads(cpu_threads)
        source = str(local_model_path) if local_model_path is not None else MODEL_ID
        options = dict(revision=MODEL_REVISION, local_files_only=True, trust_remote_code=False)
        tokenizer = AutoTokenizer.from_pretrained(source, use_fast=False, **options)
        model = AutoModel.from_pretrained(
            source,
            dtype=torch.float32,
            weights_only=True,
            attn_implementation="sdpa",
            add_pooling_layer=False,
            **options,
        )
        # cacheと明示localの双方で、実際に使うsnapshotのファイルを固定する。
        if local_model_path is None:
            from huggingface_hub import snapshot_download

            directory = Path(
                snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_files_only=True)
            )
        else:
            directory = Path(local_model_path)
        paths = {
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix in {".json", ".txt", ".bin", ".safetensors", ".model"}
        }
        explicit_weights = getattr(model.config, "transformers_weights", None)
        if explicit_weights:
            paths.add(directory / explicit_weights)
        # directory symlinkをrglobが辿らなくても、HF本体と同じ解決で全shardをhashする。
        for index in tuple(paths):
            if index.name.endswith((".safetensors.index.json", ".bin.index.json")):
                shards, _ = get_checkpoint_shard_files(
                    str(directory), str(index), local_files_only=True
                )
                paths.update(Path(shard) for shard in shards)
        artifacts = []
        for path in sorted(paths):
            with path.open("rb") as stream:
                artifacts.append(
                    (
                        Path(os.path.relpath(path, directory)).as_posix(),
                        hashlib.file_digest(stream, "sha256").hexdigest(),
                    )
                )
        versions = tuple(
            (name, importlib.metadata.version(name))
            for name in (
                "torch",
                "transformers",
                "fugashi",
                "unidic-lite",
                "spacy-alignments",
                "numpy",
            )
        )
        fingerprint = EncoderFingerprint(
            MODEL_ID,
            MODEL_REVISION,
            type(tokenizer).__name__,
            tuple(artifacts),
            versions,
            window_length,
            overlap,
        )
        return cls(model, tokenizer, fingerprint)

    def _prepare(self, document: Document) -> _Prepared:
        ids = self.tokenizer(
            document.text,
            add_special_tokens=False,
            truncation=False,
            return_attention_mask=False,
            return_token_type_ids=False,
            verbose=False,
        )["input_ids"]
        mapping, reasons, offsets = _align(document, ids, self.tokenizer, self._word_tokenizer)
        step = self.capacity - self.fingerprint.overlap
        windows = []
        for start in range(0, len(ids), step):
            end = min(start + self.capacity, len(ids))
            windows.append((start, end))
            if end == len(ids):
                break
        selected = [-1] * len(document.tokens)
        for i, (positions, reason) in enumerate(zip(mapping, reasons, strict=True)):
            if reason:
                continue
            # token全体を含み得るwindowだけを調べ、長文で全windowを毎回走査しない。
            earliest = max(0, (positions[-1] + 1 - self.capacity + step - 1) // step)
            latest = min(len(windows) - 1, positions[0] // step)
            candidates = [
                (min(positions[0] - windows[w][0], windows[w][1] - 1 - positions[-1]), -w)
                for w in range(earliest, latest + 1)
                if positions[-1] < windows[w][1]
            ]
            if candidates:
                selected[i] = -max(candidates)[1]
            else:
                reasons[i] = "cross_window"
        window_tokens = [[] for _ in windows]
        for token_id, window_id in enumerate(selected):
            if window_id >= 0:
                window_tokens[window_id].append(token_id)
        return _Prepared(document, ids, mapping, reasons, offsets, windows, selected, window_tokens)

    def encode(
        self, documents: Sequence[Document], *, batch_size: int = 8
    ) -> tuple[EncodedDocument, ...]:
        """全subword列を重複windowで覆い、token全体を含む一window内の平均を返す。

        左右余白の最小値が最大のwindowを採用し、同点なら先頭を選ぶ。raw tokenが
        windowをまたぎ全体を含むwindowがなければcross_windowとして保留する。
        長文の全体文脈を保持したという意味ではない。呼出単位のdoc数は呼出側が制限する。
        """
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_sizeは1以上の整数で指定してください")
        prepared = [self._prepare(document) for document in documents]
        vectors = [
            np.full(
                (len(p.document.tokens), self.model.config.hidden_size), np.nan, dtype=np.float32
            )
            for p in prepared
        ]
        jobs = [(d, w) for d, p in enumerate(prepared) for w in range(len(p.windows))]
        with torch.inference_mode():
            for first in range(0, len(jobs), batch_size):
                batch_jobs = jobs[first : first + batch_size]
                features = []
                positions = []
                for d, w in batch_jobs:
                    p = prepared[d]
                    start, end = p.windows[w]
                    feature = self.tokenizer.prepare_for_model(
                        p.ids[start:end],
                        add_special_tokens=True,
                        truncation=False,
                        return_special_tokens_mask=True,
                        return_attention_mask=True,
                    )
                    positions.append(
                        [
                            i
                            for i, special in enumerate(feature.pop("special_tokens_mask"))
                            if not special
                        ]
                    )
                    features.append(feature)
                padded = self.tokenizer.pad(features, padding=True, return_tensors="pt")
                inputs = {
                    k: v
                    for k, v in padded.items()
                    if k in {"input_ids", "attention_mask", "token_type_ids"}
                }
                hidden = self.model(**inputs).last_hidden_state
                for row, (d, w) in enumerate(batch_jobs):
                    p = prepared[d]
                    start, _ = p.windows[w]
                    for token_id in p.window_tokens[w]:
                        indices = [positions[row][i - start] for i in p.mapping[token_id]]
                        vectors[d][token_id] = (
                            hidden[row, indices].mean(dim=0).float().cpu().numpy()
                        )
        output = []
        for p, matrix in zip(prepared, vectors, strict=True):
            spans = []
            for start, end in p.windows:
                known = [offset for offset in p.offsets[start:end] if offset is not None]
                spans.append((known[0][0], known[-1][1]) if known else None)
            output.append(
                EncodedDocument(
                    p.document.source_id,
                    tuple((t.start, t.end) for t in p.document.tokens),
                    matrix,
                    np.asarray([reason is None for reason in p.reasons], dtype=np.bool_),
                    tuple(p.reasons),
                    np.asarray(p.selected, dtype=np.int32),
                    tuple(spans),
                    self.fingerprint,
                )
            )
        return tuple(output)
