"""原解析の不変snapshotと、元IDを参照する比較用の型。位置は原文の文字添字。"""

import hashlib
from dataclasses import dataclass, field
from functools import cached_property

NORMALIZATION_IDENTITY = "ginza-original-graph-occurrence-v11"
# UDの標準UPOS。端点の役割と元の品詞を分け、両方の交換・参照検証で共用する。
UD_POS_TAGS = frozenset(
    "ADJ ADP ADV AUX CCONJ DET INTJ NOUN NUM PART PRON PROPN PUNCT SCONJ SYM VERB X".split()
)
PARSER_VERSIONS = {
    "ginza": "5.3.0",
    "ja-ginza": "5.3.0",
    "spacy": "3.8.16",
    "SudachiPy": "0.6.11",
    "SudachiDict-core": "20260723",
}


@dataclass(frozen=True)
class PredicateNode:
    lemma: str
    xpos: str
    inflection: tuple[str, ...]
    head: int | None
    dependency: str | None


@dataclass(frozen=True)
class PredicateGraph:
    root: int
    nodes: tuple[PredicateNode, ...]


@dataclass(frozen=True)
class Query:
    predicate: PredicateGraph
    attachment: int | None = None
    dependency: str | None = None
    noun: tuple[str, ...] = ()
    noun_role: str | None = None


@dataclass(frozen=True)
class GraphView:
    root: int
    nodes: tuple[int, ...]
    edges: tuple[tuple[int, int, str], ...]


@dataclass(frozen=True)
class PredicateEvidence:
    kind: str
    selected_token: int
    token: int
    head: int
    dependency: str
    upos: str
    xpos: str


@dataclass(frozen=True)
class PredicateOccurrence:
    graph: GraphView
    query: Query
    evidence: tuple[PredicateEvidence, ...]


@dataclass(frozen=True)
class OccurrenceView:
    document_id: str
    predicate: GraphView
    predicate_attachment: int
    noun: GraphView
    target_edge: tuple[int, int, str]
    noun_feature_nodes: tuple[int, ...]


@dataclass(frozen=True)
class Endpoint:
    text: str
    ranges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Relation:
    predicate: Endpoint
    argument: Endpoint | None
    queries: tuple[Query, ...]
    view: OccurrenceView | None = None


@dataclass(frozen=True)
class Analysis:
    text: str
    snapshot: bytes = field(repr=False)

    @cached_property
    def document_id(self) -> str:
        return hashlib.sha256(self.snapshot).hexdigest()

    def restore_doc(self):
        """公開直列化から独立したDocを返す。呼出し側の変更はsnapshotへ影響しない。"""
        from spacy.tokens import Doc

        doc = Doc(_snapshot_vocab()).from_bytes(self.snapshot)
        if doc.text != self.text:
            raise ValueError("原解析snapshotと原文が一致しません。")
        return doc

    @cached_property
    def comparison(self):
        from .comparison import comparison_views

        return comparison_views(self.restore_doc(), self.document_id)

    @property
    def relations(self) -> tuple[Relation, ...]:
        return self.comparison.relations

    @property
    def predicates(self) -> tuple[PredicateOccurrence, ...]:
        return self.comparison.predicates


@dataclass(frozen=True)
class Comparison:
    predicates: tuple[PredicateOccurrence, ...]
    relations: tuple[Relation, ...]


def _snapshot_vocab():
    import spacy

    # Lexemeの可変属性を返却Doc同士で共有せず、日本語の既定属性も維持する。
    return spacy.blank("ja").vocab
