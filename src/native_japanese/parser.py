"""KWJAのネイティブ解析と、正規化前の原文への位置対応を保持する。"""

import gc
import logging
import os
from collections.abc import Iterable, Iterator
from importlib.metadata import PackageNotFoundError, version
from threading import Lock
from unicodedata import combining, normalize

from rhoknp import Document

from .models import PARSER_VERSIONS, Analysis


def _normalized_source(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    # KWJAが公開する文字正規化の表を使い、独自の日本語補正は加えない。
    from kwja.utils.constants import TRANSLATION_TABLE

    # 正規化で結合する隣接文字だけを一単位にする。同じ文字の遠隔対応を作らない。
    units = []
    for index, char in enumerate(text):
        if units and (
            combining(char)
            or normalize("NFKC", units[-1][0] + char)
            != normalize("NFKC", units[-1][0]) + normalize("NFKC", char)
        ):
            value, start, _ = units.pop()
            units.append((value + char, start, index + 1))
        else:
            units.append((char, index, index + 1))
    normalized_parts, spans = [], []
    for value, start, end in units:
        part = normalize("NFKC", value)
        normalized_parts.append(part)
        spans.extend((start, end) for _ in part)
    normalized = "".join(normalized_parts)
    if normalized != normalize("NFKC", text):
        raise ValueError("Unicode正規化の文字範囲を原文へ対応付けられません。")
    chars, offsets = [], []
    for char, span in zip(normalized, spans, strict=True):
        translated = char.translate(TRANSLATION_TABLE)
        for value in translated:
            chars.append(value)
            offsets.append(span)
    return "".join(chars), tuple(offsets)


def document_offsets(document: Document, text: str) -> tuple[tuple[int, int], ...]:
    """KWJA公式正規化後の文字列を照合し、形態素位置を原文へ対応付ける。"""
    source, mapping = _normalized_source(text)
    offsets, cursor = [], 0
    for morpheme in document.morphemes:
        selected = []
        for char in morpheme.text:
            if not char.isspace():
                while cursor < len(source) and source[cursor].isspace():
                    cursor += 1
            if cursor >= len(source) or source[cursor] != char:
                raise ValueError("KWJAの形態素と公式正規化後の原文が一致しません。")
            selected.append(mapping[cursor])
            cursor += 1
        if not selected:
            raise ValueError("KWJAの形態素に原文位置を対応付けられません。")
        offsets.append((selected[0][0], selected[-1][1]))
    if any(not char.isspace() for char in source[cursor:]):
        raise ValueError("原文末尾の非空白文字がKWJAの解析結果にありません。")
    return tuple(offsets)


def validate_doc(document: Document, text: str, offsets=None):
    expected = document_offsets(document, text)
    if offsets is not None and offsets != expected:
        raise ValueError("形態素の原文位置が公式正規化との対応に一致しません。")


def adapt_doc(document: Document, text: str) -> Analysis:
    """公開KNP形式の全情報と原文を独立して保存する。"""
    return Analysis(text, document.to_knp().encode("utf-8"), document_offsets(document, text))


def _word_window(document, tokenizer, default_window, encoder_window, special_token_count):
    """公式loaderと同じ符号化長・追加token数で、文を切らずに入力枠を選ぶ。"""
    lengths = (
        len(
            tokenizer(
                [m.text for m in sentence.morphemes],
                is_split_into_words=True,
                padding=False,
                truncation=False,
            ).input_ids
        )
        + special_token_count
        for sentence in document.sentences
    )
    required = max(lengths, default=0)
    if required <= default_window:
        return default_window
    if required <= encoder_window:
        return encoder_window
    raise ValueError(
        f"文の解析に必要な{required} tokensがKWJA encoderの上限{encoder_window}を超えています。"
    )


class _KwjaPipeline:
    """公式processorを保持し、入力を変更せずに文単位の解析枠を選ぶ。"""

    def __init__(self):
        self._lock = Lock()
        self._char = self._word = None
        self._tokenizer = None
        # 公式CLIと同じ推論初期化。PyPI配布に含まれない訓練用metricsは使わない。
        os.environ["KWJA_CLI_MODE"] = "1"
        from hydra.utils import instantiate
        from kwja.cli.cli import CLIProcessor, normalize_text
        from kwja.cli.config import CLIConfig, Device, ModelSize
        from kwja.utils.logging_util import CONSOLE

        self._normalize_text = normalize_text
        CONSOLE.quiet = True
        logging.getLogger("kwja_cli").setLevel(logging.WARNING)
        config = CLIConfig(model_size=ModelSize.BASE, device=Device.CPU, num_workers=0)
        try:
            self._char = CLIProcessor(config, ["char"])
            self._word = CLIProcessor(config, ["word"])
            self._char.load_all_modules()
            self._word.load_all_modules()
            word_module = self._word.processors[0].module
            self._word_config = word_module.hparams.datamodule.predict
            metadata = instantiate(self._word_config, juman_file=None)
            self._tokenizer = metadata.tokenizer
            self._special_token_count = len(metadata.special_tokens)
            self._default_window = metadata.max_seq_length
            self._encoder_window = word_module.encoder.config.max_position_embeddings
        except BaseException:
            self.close()
            raise

    def run(self, text):
        with self._lock:
            if self._char is None or self._word is None:
                raise RuntimeError("解析器は終了しています。")
            self._char.refresh()
            self._word.refresh()
            # 公開texts設定へ渡し、EODを含む本文も区切りprotocolへ解釈しない。
            self._char.processors[0].module.hparams.datamodule.predict.texts = [
                self._normalize_text(text)
            ]
            self._char.run([Document.from_raw_text(text)], interactive=True)
            document = Document.from_jumanpp(
                self._char.processors[0].destination.read_text(encoding="utf-8")
            )
            validate_doc(document, text)
            self._word_config.max_seq_length = _word_window(
                document,
                self._tokenizer,
                self._default_window,
                self._encoder_window,
                self._special_token_count,
            )
            return Document.from_knp(self._word.run([document], interactive=True))

    def close(self):
        with self._lock:
            pipelines = (self._char, self._word)
            self._char = self._word = self._tokenizer = None
            for pipeline in pipelines:
                if pipeline is not None:
                    pipeline.refresh()
                    for processor in pipeline.processors:
                        processor.delete_module_and_trainer()
            # Lightningのmodel/trainer間の循環参照を回収し、次の解析器へ保持しない。
            gc.collect()


class Parser:
    """固定KWJAの公式APIから、原文とネイティブ解析を取得する。"""

    def __init__(self):
        for package, expected in PARSER_VERSIONS.items():
            try:
                installed = version(package)
            except PackageNotFoundError as error:
                raise RuntimeError(f"解析に必要な{package}=={expected}がありません。") from error
            if installed != expected:
                raise RuntimeError(
                    f"解析に必要なバージョンと異なります: {package}=={installed}（必要: {expected}）。"
                )
        self._closed = False
        self._pipeline = _KwjaPipeline()

    def analyze(self, text: str) -> Analysis:
        if self._closed:
            raise RuntimeError("解析器は終了しています。")
        if not text.strip():
            return adapt_doc(Document.from_knp(""), text)
        if self._pipeline is None:
            self._pipeline = _KwjaPipeline()
        try:
            return adapt_doc(self._pipeline.run(text), text)
        except BaseException:
            # 公式writerが保持する途中状態を、次の原文へ持ち越さない。
            pipeline, self._pipeline = self._pipeline, None
            pipeline.close()
            raise

    def analyze_many(self, texts: Iterable[str]) -> Iterator[Analysis]:
        """モデルを再利用し、原文と順序を保って文書を逐次解析する。"""
        for text in texts:
            yield self.analyze(text)

    def close(self):
        """所有する公式processorとモデルを解放する。"""
        self._closed = True
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            pipeline.close()
