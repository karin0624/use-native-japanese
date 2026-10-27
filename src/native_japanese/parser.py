"""GiNZAの公開文節関係を、原文位置と同じ解析契約の照会へ接続する。"""

import re
from collections.abc import Iterable, Iterator
from importlib.metadata import PackageNotFoundError, version

import ginza
import spacy
from spacy.language import Language
from spacy.symbols import acl, amod, iobj, nsubj, obj, obl
from spacy.tokens import Doc, Span, Token

from .models import PARSER_VERSIONS, Analysis, Endpoint, Query, Relation

_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z_][A-Za-z0-9_]*(?:[.-][A-Za-z0-9_]+)*|[0-9][A-Za-z0-9_]*)"
)
_ARGUMENT_RELATIONS = {nsubj, obj, iobj, obl}
_MODIFIER_RELATIONS = {acl, amod}


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


def _comparison_tokens(anchor: Token) -> tuple[Token, ...]:
    """所属文節の機能側だけを除き、NO_HEADを含む残りの原語を保持する。"""
    span = ginza.bunsetu_span(anchor)
    if span.doc is not anchor.doc or not span.start <= anchor.i < span.end:
        raise ValueError("解析された文節に、その文節の中心となる語が含まれていません。")
    excluded = {
        token.i
        for token in span
        if ginza.bunsetu_position_type(token) in {"FUNC", "SYN_HEAD"}
        or token.dep_ in {"aux", "case", "cop"}
    }
    for index in tuple(excluded):
        # UDのflatなfixed機能表現だけを、公開された直接の依存辺から読む。
        for token in span.doc[index].children:
            if span.start <= token.i < span.end and token.dep_ == "fixed":
                excluded.add(token.i)
    return tuple(
        token
        for token in span
        if token.i not in excluded and not token.is_space and not token.is_punct
    )


def _endpoint(tokens: tuple[Token, ...], text: str) -> Endpoint:
    if not tokens:
        raise ValueError("解析された語句に、比較する語が含まれていません。")
    span = tokens[0].doc[tokens[0].i : tokens[-1].i + 1]
    start, end = span.start_char, span.end_char
    if not 0 <= start < end <= len(text) or text[start:end] != span.text:
        raise ValueError("解析された語句が、原文の対応する位置の文字列と一致しません。")
    # 表示は原文の包絡に一度対応させ、比較lemmaには選択tokenだけを使う。
    return Endpoint(span.text, ((start, end),))


def adapt_doc(doc: Doc, text: str) -> Analysis:
    """公開文節の非機能側と元依存を読む。欠けた語や関係は補完しない。"""
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

    relations = []
    for head in ginza.bunsetu_head_tokens(doc):
        # callback元Tokenを保持し、返却phraseのrootと同一視しない。
        for dependency, dependent in ginza.sub_phrases(lambda token: token)(head):
            original_head = dependent.head
            # UDが明示する名詞項・名詞修飾の役割と方向だけを読む。
            if dependent.dep in _ARGUMENT_RELATIONS:
                predicate, noun, noun_role = original_head, dependent, "dependent"
            elif dependent.dep in _MODIFIER_RELATIONS:
                predicate, noun, noun_role = dependent, original_head, "head"
            else:
                continue

            if predicate.is_space or predicate.is_punct or noun.is_space or noun.is_punct:
                continue

            # 詳細品詞が明示する機能的カテゴリも、語彙端点へ読み替えない。
            if "助動詞語幹" in noun.tag_.split("-"):
                continue

            predicate_tokens = _comparison_tokens(predicate)
            noun_tokens = _comparison_tokens(noun)
            # 元端点が機能側なら、その文節の別の語へ置き換えて関係を作らない。
            if predicate not in predicate_tokens or noun not in noun_tokens:
                continue
            relations.append(
                Relation(
                    _endpoint(predicate_tokens, text),
                    _endpoint(noun_tokens, text),
                    (
                        Query(
                            tuple(token.lemma_ for token in predicate_tokens),
                            predicate.pos_,
                            dependency,
                            tuple(token.lemma_ for token in noun_tokens),
                            noun.pos_,
                            noun_role,
                        ),
                    ),
                )
            )
    return Analysis(text, tuple(relations))


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

    def _require_nlp(self):
        if self._nlp is None:
            raise RuntimeError("解析器は終了しています。")
        return self._nlp

    def analyze(self, text: str) -> Analysis:
        return adapt_doc(self._require_nlp()(text), text)

    def analyze_many(self, texts: Iterable[str]) -> Iterator[Analysis]:
        """公開nlp.pipeで順序と原文を保ち、参照本文を逐次処理する。"""
        pipeline = self._require_nlp()
        for doc, text in pipeline.pipe(((text, text) for text in texts), as_tuples=True):
            yield adapt_doc(doc, text)

    def close(self):
        """所有するモデル参照を解放する。"""
        self._nlp = None
