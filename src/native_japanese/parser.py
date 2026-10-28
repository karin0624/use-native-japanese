"""固定GiNZAパイプラインの原文と全解析を、そのまま保持する。"""

import re
from collections.abc import Iterable, Iterator
from importlib.metadata import PackageNotFoundError, version

import spacy
from spacy.language import Language
from spacy.tokens import Doc, Span
from spacy.util import minibatch

from .models import PARSER_VERSIONS, Analysis
from .vocabulary import (
    VocabularyRecorder,
    VocabularyResources,
    capture_vocabulary_resources,
    snapshot_vocabulary,
)

_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z_][A-Za-z0-9_]*(?:[.-][A-Za-z0-9_]+)*|[0-9][A-Za-z0-9_]*)"
)


def _identifier_spans(doc: Doc) -> tuple[Span, ...]:
    # 連続英数字wordと既存の英字開始識別子を保ち、数字間の記号結合は増やさない。
    # 原文の部分tokenは拡張せず、一tokenの公開lemmaも保持する。
    spans = []
    for match in _IDENTIFIER_PATTERN.finditer(doc.text):
        span = doc.char_span(*match.span(), alignment_mode="strict")
        if span is not None and len(span) > 1:
            spans.append(span)
    return tuple(spans)


@Language.component("native_japanese_identifier_merge", retokenizes=True)
def _merge_identifiers(doc: Doc) -> Doc:
    # 解析する語境界を先に決め、比較lemmaも同じtokenから読む。
    with doc.retokenize() as retokenizer:
        for span in _identifier_spans(doc):
            retokenizer.merge(span, attrs={"LEMMA": span.text})
    return doc


def validate_doc(doc: Doc, text: str):
    """原文と公開Tokenの全位置を照合する。語や辺を補完・除去しない。"""
    if doc.text != text or "".join(token.text_with_ws for token in doc) != text:
        raise ValueError("解析結果が原文全文を保持していません。")
    cursor = 0
    for token in doc:
        end = token.idx + len(token)
        if (
            not cursor <= token.idx < end <= len(text)
            or text[token.idx : end] != token.text
            or any(not character.isspace() for character in text[cursor : token.idx])
        ):
            raise ValueError(
                "解析された語の文字列や位置が原文と一致しないか、空白以外の文字が抜けています。"
            )
        cursor = end
    if any(not character.isspace() for character in text[cursor:]):
        raise ValueError("原文末尾の非空白文字が解析結果にありません。")


def adapt_doc(
    doc: Doc,
    text: str,
    *,
    vocabulary_resources: VocabularyResources | None = None,
    vocabulary_recorder: VocabularyRecorder | None = None,
) -> Analysis:
    """元Docを変更せず、全情報の不変snapshotを保存する。"""
    validate_doc(doc, text)
    return Analysis(
        text,
        doc.to_bytes(),
        snapshot_vocabulary(doc, vocabulary_resources, recorder=vocabulary_recorder),
    )


class Parser:
    """固定版の軽量モデルをNERなしで一度読み込み、呼出し間で再利用する。"""

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
        self._nlp = spacy.load("ja_ginza", exclude=["ner"])
        # C分割の補助データを元の処理で消費し、結合前の添字を後段へ残さない。
        self._nlp.remove_pipe("compound_splitter")
        self._nlp.add_pipe("compound_splitter", config={"split_mode": "C"}, first=True)
        self._nlp.add_pipe("native_japanese_identifier_merge", after="compound_splitter")
        self._vocabulary_resources = capture_vocabulary_resources(self._nlp.vocab)
        self._vocabulary_recorder = VocabularyRecorder(self._vocabulary_resources)

    def _require_nlp(self):
        if self._nlp is None:
            raise RuntimeError("解析器は終了しています。")
        return self._nlp

    def analyze(self, text: str) -> Analysis:
        return adapt_doc(
            self._require_nlp()(text),
            text,
            vocabulary_resources=self._vocabulary_resources,
            vocabulary_recorder=self._vocabulary_recorder,
        )

    def analyze_many(self, texts: Iterable[str]) -> Iterator[Analysis]:
        """公開nlp.pipeで順序と原文を保ち、参照本文を逐次処理する。"""
        pipeline = self._require_nlp()
        parsed = pipeline.pipe(((text, text) for text in texts), as_tuples=True, batch_size=32)
        for batch in minibatch(parsed, size=32):
            docs, originals = zip(*batch, strict=True)
            for doc, text in batch:
                validate_doc(doc, text)
            snapshots = tuple(doc.to_bytes() for doc in docs)
            vocabularies = self._vocabulary_recorder.capture_many(docs)
            for text, snapshot, vocabulary in zip(originals, snapshots, vocabularies, strict=True):
                yield Analysis(text, snapshot, vocabulary)

    def close(self):
        """所有するモデル参照を解放する。"""
        self._nlp = None
        self._vocabulary_resources = None
        self._vocabulary_recorder = None
