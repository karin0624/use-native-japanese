"""解析の公開関係と正規化照会をつなぐ小さな共有型。位置は原文の文字添字。"""

from dataclasses import dataclass

NORMALIZATION_IDENTITY = "ginza-bunsetu-lemma-no-ner-identifier-span-v5"
PARSER_VERSIONS = {
    "ginza": "5.3.0+nativejapanese.1",
    "ja-ginza": "5.3.0",
    "spacy": "3.8.16",
    "SudachiPy": "0.6.11",
    "SudachiDict-core": "20260723",
}


@dataclass(frozen=True)
class Query:
    predicate: tuple[str, ...]
    predicate_type: str
    dependency: str
    noun: tuple[str, ...]
    noun_type: str
    noun_role: str


@dataclass(frozen=True)
class Endpoint:
    text: str
    ranges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Relation:
    predicate: Endpoint
    argument: Endpoint | None
    queries: tuple[Query, ...]


@dataclass(frozen=True)
class Analysis:
    text: str
    relations: tuple[Relation, ...]
