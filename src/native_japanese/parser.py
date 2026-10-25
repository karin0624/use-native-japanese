"""GiNZAの公開文節関係を、原文位置と同じ解析契約の照会へ接続する。"""

from collections.abc import Iterable, Iterator
from importlib.metadata import PackageNotFoundError, version

import ginza
import spacy
from spacy.tokens import Doc, Span

from .models import PARSER_VERSIONS, Analysis, Endpoint, Query, Relation


def _endpoint(span: Span, text: str) -> Endpoint:
    start, end = span.start_char, span.end_char
    if not 0 <= start < end <= len(text) or text[start:end] != span.text:
        raise ValueError("解析された語句が、原文の対応する位置の文字列と一致しません。")
    return Endpoint(span.text, ((start, end),))


def adapt_doc(doc: Doc, text: str) -> Analysis:
    """公開token・主辞句・依存を読む。欠けた語や関係は補完しない。"""
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
            if original_head.pos_ in {"VERB", "ADJ"} and dependent.pos_ in {"NOUN", "PROPN"}:
                predicate, noun, noun_role = original_head, dependent, "dependent"
            elif original_head.pos_ in {"NOUN", "PROPN"} and dependent.pos_ in {"VERB", "ADJ"}:
                predicate, noun, noun_role = dependent, original_head, "head"
            else:
                continue

            predicate_phrase = ginza.bunsetu_phrase_span(predicate, use_token_head=True)
            noun_phrase = ginza.bunsetu_phrase_span(noun, use_token_head=True)
            predicate_endpoint = _endpoint(predicate_phrase, text)
            noun_endpoint = _endpoint(noun_phrase, text)
            if any(
                not phrase.start <= token.i < phrase.end
                for token, phrase in ((predicate, predicate_phrase), (noun, noun_phrase))
            ):
                raise ValueError(
                    "解析された語句に、その語句の中心となる語が含まれていません。"
                )
            relations.append(
                Relation(
                    predicate_endpoint,
                    noun_endpoint,
                    (
                        Query(
                            tuple(token.lemma_ for token in predicate_phrase),
                            predicate.pos_,
                            dependency,
                            tuple(token.lemma_ for token in noun_phrase),
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
