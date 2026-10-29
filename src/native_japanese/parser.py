"""KWJAのネイティブ解析と、正規化前の原文への位置対応を保持する。"""

import sys
from collections.abc import Iterable, Iterator
from importlib.metadata import PackageNotFoundError, version
from unicodedata import combining, normalize

from rhoknp import KWJA, Document

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


class Parser:
    """固定モデルの常駐KWJAを公式rhoknp APIから利用する。"""

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
        self._kwja = KWJA(
            executable=sys.executable,
            options=[
                "-E",
                "-P",
                "-X",
                "utf8",
                "-m",
                "kwja.cli.cli",
                "--model-size",
                "base",
                "--device",
                "cpu",
                "--tasks",
                "char,word",
                "--quiet",
            ],
            skip_sanity_check=True,
        )
        if not self._kwja.is_available():
            raise RuntimeError("KWJAを起動できません。")

    def analyze(self, text: str) -> Analysis:
        if self._kwja is None:
            raise RuntimeError("解析器は終了しています。")
        if any(line.strip() == Document.EOD for line in text.splitlines()):
            raise ValueError("原文にKWJAの入力終端を表すEOD単独行があります。")
        if not text.strip():
            return adapt_doc(Document.from_knp(""), text)
        return adapt_doc(self._kwja.apply(text, timeout=600), text)

    def analyze_many(self, texts: Iterable[str]) -> Iterator[Analysis]:
        """モデルを再利用し、原文と順序を保って文書を逐次解析する。"""
        for text in texts:
            yield self.analyze(text)

    def close(self):
        """所有するrhoknpプロセッサを解放する。"""
        self._kwja = None
