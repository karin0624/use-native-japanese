"""解析の公開関係と正規化照会をつなぐ小さな共有型。位置は原文の文字添字。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    predicate: str
    predicate_type: str
    case: str
    noun: str


@dataclass(frozen=True)
class Endpoint:
    text: str
    ranges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Relation:
    predicate: Endpoint
    argument: Endpoint | None
    queries: tuple[Query, ...]
    unavailable_reasons: tuple[str, ...] = ()
    representations: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Unavailable:
    ranges: tuple[tuple[int, int], ...]
    reason: str


@dataclass(frozen=True)
class Analysis:
    text: str
    relations: tuple[Relation, ...]
    unavailable: tuple[Unavailable, ...]
