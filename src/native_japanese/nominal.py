"""他の原語と構造を固定した実在名詞を、静的cosineの上位3へ対応付ける。

登録・subword生成は意味理解や正用を保証しない。直接支持cは変更せず、
引用本文の取得も行わない。必要な表示時だけ候補のlemmasで索引へ戻る。
"""

from __future__ import annotations

import hashlib
import heapq
import json
from dataclasses import asdict, dataclass
from importlib.metadata import version
from itertools import chain
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .structure import FragmentShape
from .usage_index import UsageCandidate, UsageIndex

MODEL_SHA256 = "f1435852226301053f2cae0196b61ab291826d5c4a2235215ff9410f728815d7"
MODEL_BYTES = 21_944_687
DEPENDENCIES = (
    ("compress-fasttext", "0.1.5"),
    ("gensim", "4.4.0"),
    ("numpy", "2.3.5"),
    ("scipy", "1.17.0"),
    ("scikit-learn", "1.8.0"),
)


@dataclass(frozen=True, slots=True)
class NominalFingerprint:
    """固定配布とruntimeの来歴。圧縮前の厳密snapshotは配布元にも記録がない。"""

    model_sha256: str
    versions: tuple[tuple[str, str], ...]
    vector_size: int
    model_id: str = "https://zenodo.org/records/4905385/files/fasttext-ja-mini"
    source: str = "Liebl Bernhard, 2021-06-07, v1.0; fastText crawl-vectors日本語圧縮版"
    upstream_snapshot: str = "圧縮前artifactのhash・厳密snapshotは未確認"
    license: str = "元系列と配布説明はCC BY-SA 3.0; record metadataはCC BY 4.0で不一致"
    method: str = "fixed-lemma-static-cosine-top3-v1"

    @property
    def digest(self) -> str:
        """モデル・依存版・計算契約を含むSHA-256。"""
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class VectorState:
    """registeredは圧縮後の語彙への登録であり、元モデルの既知語判定ではない。"""

    status: str
    registered: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class NominalCandidate:
    """同じ条件の実在候補。lemmasは必要時にcitation()へ渡す無加工のkey。"""

    noun: str
    lemmas: tuple[str, ...]
    cosine: float
    count: int
    articles: int
    vector_status: str


@dataclass(frozen=True, slots=True)
class NominalEvidence:
    """candidate_countは全列挙時だけ確定。上限時の最大値は部分集合の最大値。

    入力vector不能時は候補を走査せず、candidate_count=None、evaluated=0とする。
    valid/unavailable件数は評価した候補に限り、未処理候補のcoverageを表さない。
    """

    query_vector: VectorState
    top_candidates: tuple[NominalCandidate, ...]
    status: str
    candidate_count: int | None
    evaluated_candidate_count: int
    valid_vector_count: int
    unavailable_vector_count: int
    truncated: bool
    fingerprint: NominalFingerprint

    @property
    def max_cosine(self) -> float | None:
        """取得不能を0へ変換しない。truncated時には全候補の最大値ではない。"""
        return self.top_candidates[0].cosine if self.top_candidates else None


def _load_fixed_model(model_path: str | Path):
    """信頼済み固定hashのローカル配布だけ公開loadへ渡す。自動取得はしない。"""
    # 入力が途中で差し替わっても、検証した同じbytesだけをload対象にする。
    with Path(model_path).open("rb") as stream:
        payload = stream.read(MODEL_BYTES + 1)
    if len(payload) != MODEL_BYTES:
        raise ValueError("名詞モデルは信頼済みの固定配布ファイルではありません")
    if hashlib.sha256(payload).hexdigest() != MODEL_SHA256:
        raise ValueError("名詞モデルのSHA-256が信頼済み固定配布と一致しません")
    versions = tuple((name, version(name)) for name, _ in DEPENDENCIES)
    if versions != DEPENDENCIES:
        raise ValueError("名詞モデルの依存版が固定runtimeと一致しません")
    from compress_fasttext.models import CompressedFastTextKeyedVectors

    # 公開loadはfile-like非対応。privateな一時snapshotでOS固有のfdパスを避ける。
    # 固定配布は単一ファイルであり、入力側の任意pickle・sidecarを渡さない。
    with TemporaryDirectory(prefix="native-japanese-nominal-") as directory:
        snapshot = Path(directory) / "fasttext-ja-mini"
        snapshot.write_bytes(payload)
        del payload
        model = CompressedFastTextKeyedVectors.load(str(snapshot))
    if model.vector_size != 300:
        raise ValueError("固定名詞モデルの次元数が一致しません")
    return model, NominalFingerprint(MODEL_SHA256, versions, int(model.vector_size))


class NominalGeneralizer:
    """一度loadした圧縮モデルを使う。vector cacheは持たず、batchと上位3だけ保持。"""

    def __init__(self, model_path: str | Path) -> None:
        self._model, self.fingerprint = _load_fixed_model(model_path)

    def _vector(self, noun: str) -> tuple[VectorState, np.ndarray | None]:
        """元のlemmaをそのまま公開APIへ渡し、無効vectorを順位から除外する。"""
        registered = noun in self._model.key_to_index
        if not noun:
            return VectorState("unavailable", registered, "空文字列"), None
        try:
            vector = np.asarray(self._model.get_vector(noun), dtype=np.float64)
        except (KeyError, ValueError, TypeError) as error:
            return VectorState("unavailable", registered, f"取得失敗: {type(error).__name__}"), None
        if vector.shape != (self.fingerprint.vector_size,):
            return VectorState("unavailable", registered, "次元不一致"), None
        if not np.isfinite(vector).all():
            return VectorState("unavailable", registered, "非有限vector"), None
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm == 0:
            return VectorState("unavailable", registered, "非有限またはゼロのノルム"), None
        status = "compressed_registered" if registered else "subword_generated"
        return VectorState(status, registered), vector / norm

    def generalize(
        self,
        index: UsageIndex,
        shape: FragmentShape,
        lemmas: tuple[str, ...],
        *,
        target_slot: int,
        max_candidates: int | None = None,
        batch_size: int = 256,
    ) -> NominalEvidence:
        """呼出側が選んだNOUN/PROPN slotのみ一般化。他の全原語・構造は固定する。

        無上限が既定。上限指定時は残り1候補の存在だけ確認して打切りを明示する。
        件数は評価対象の異なる候補語数で、原名詞自身は含まない。
        cosine同点は記事数の降順、無加工lemmaの文字列順とする。
        入力vector不能時は候補を走査しない。
        """
        if len(lemmas) != len(shape.upos) or any(not isinstance(s, str) for s in lemmas):
            raise ValueError("lemma列はshapeと同じ長さの文字列tupleが必要です")
        if type(target_slot) is not int or not 0 <= target_slot < len(lemmas):
            raise ValueError("target_slotがshapeの範囲外です")
        if shape.upos[target_slot] not in ("NOUN", "PROPN"):
            raise ValueError("名詞一般化はUPOS NOUN/PROPNのslotだけを受け付けます")
        if max_candidates is not None and (type(max_candidates) is not int or max_candidates < 0):
            raise ValueError("max_candidatesはNoneまたは0以上の整数で指定してください")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_sizeは1以上の整数で指定してください")
        state, query = self._vector(lemmas[target_slot])
        if query is None:
            return NominalEvidence(
                state, (), "query_vector_unavailable", None, 0, 0, 0, False, self.fingerprint
            )

        top: list[NominalCandidate] = []
        batch: list[tuple[UsageCandidate, VectorState]] = []
        vectors: list[np.ndarray] = []
        evaluated = valid = unavailable = 0
        truncated = False

        def flush() -> None:
            if not batch:
                return
            scores = np.clip(np.stack(vectors) @ query, -1.0, 1.0)
            # スコア計算はbatch化し、batch間では上位3だけを保持する。
            nearest = heapq.nsmallest(
                3,
                (
                    NominalCandidate(
                        item.lemma, item.lemmas, float(score), item.count, item.articles, vs.status
                    )
                    for (item, vs), score in zip(batch, scores, strict=True)
                ),
                key=lambda item: (-item.cosine, -item.articles, item.noun),
            )
            top[:] = heapq.nsmallest(
                3, chain(top, nearest), key=lambda item: (-item.cosine, -item.articles, item.noun)
            )
            batch.clear()
            vectors.clear()

        for candidate in index.candidates(shape, lemmas, target_slot=target_slot):
            if candidate.lemma == lemmas[target_slot]:
                continue
            if max_candidates is not None and evaluated >= max_candidates:
                truncated = True
                break
            evaluated += 1
            candidate_state, vector = self._vector(candidate.lemma)
            if vector is None:
                unavailable += 1
                continue
            valid += 1
            batch.append((candidate, candidate_state))
            vectors.append(vector)
            if len(batch) == batch_size:
                flush()
        flush()
        if truncated:
            status = "truncated"
        elif not evaluated:
            status = "no_candidates"
        elif not valid:
            status = "candidate_vectors_unavailable"
        else:
            status = "available"
        return NominalEvidence(
            state,
            tuple(top),
            status,
            None if truncated else evaluated,
            evaluated,
            valid,
            unavailable,
            truncated,
            self.fingerprint,
        )
