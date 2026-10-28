"""原解析の不変snapshotと、元IDを参照する比較用の型。位置は原文の文字添字。"""

import hashlib
from dataclasses import dataclass, field
from functools import cached_property

from .vocabulary import VocabularySnapshot, restore_vocabulary

NORMALIZATION_IDENTITY = "ginza-predicate-role-occurrence-v12"
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
    related_predicate: PredicateGraph | None = None
    related_attachment: int | None = None


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
    marker_units: tuple[GraphView, ...] = ()


@dataclass(frozen=True)
class OccurrenceView:
    document_id: str
    predicate: GraphView
    predicate_attachment: int
    noun: GraphView
    target_edge: tuple[int, int, str]
    noun_feature_nodes: tuple[int, ...]
    noun_case_nodes: tuple[int, ...] = ()
    noun_mark_nodes: tuple[int, ...] = ()
    noun_predicate_roots: tuple[int, ...] = ()
    related_predicate: GraphView | None = None


@dataclass(frozen=True)
class RelationTarget:
    target_edge: tuple[int, int, str]
    predicate_attachment: int
    noun_root: int
    noun_role: str
    dependency: str
    scope: str


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
    predicate_root: int | None = None


@dataclass(frozen=True)
class Analysis:
    text: str
    snapshot: bytes = field(repr=False)
    vocabulary: VocabularySnapshot = field(repr=False)

    @cached_property
    def document_id(self) -> str:
        return hashlib.sha256(
            hashlib.sha256(self.snapshot).digest()
            + bytes.fromhex(self.vocabulary.resources.resource_id)
            + hashlib.sha256(self.vocabulary.to_bytes()).digest()
        ).hexdigest()

    def restore_doc(self):
        """公開直列化から独立したDocを返す。呼出し側の変更はsnapshotへ影響しない。"""
        from spacy.tokens import Doc

        doc = Doc(restore_vocabulary(self.vocabulary)).from_bytes(self.snapshot)
        if doc.text != self.text:
            raise ValueError("原解析snapshotと原文が一致しません。")
        return doc

    @cached_property
    def usage_structure(self):
        from .comparison import UsageStructure

        return UsageStructure(self)

    @cached_property
    def comparison(self):
        return self.usage_structure.comparison

    def validate_comparison(self):
        self.comparison.validate(self.usage_structure.document)

    @property
    def relations(self) -> tuple[Relation, ...]:
        self.comparison.validate()
        return self.comparison.relations

    @property
    def predicates(self) -> tuple[PredicateOccurrence, ...]:
        return self.comparison.predicates


@dataclass(frozen=True)
class Comparison:
    predicates: tuple[PredicateOccurrence, ...]
    relations: tuple[Relation, ...]
    roles: tuple[PredicateEvidence, ...] = ()
    targets: tuple[RelationTarget, ...] = ()

    def validate(self, doc=None):
        """認識済みの役割・対象と照会の対応を、支持判定より先に確認する。"""
        predicates = {p.graph.root: p for p in self.predicates}
        selected = {i for p in self.predicates for i in p.graph.nodes}
        if (
            len(predicates) != len(self.predicates)
            or set(self.roles) != {role for p in self.predicates for role in p.evidence}
            or any(role.selected_token not in selected for role in self.roles)
        ):
            raise ValueError("認識した述語の役割に対応する構成がありません。")
        targets = {t.target_edge: t for t in self.targets if t.scope == "relation"}
        covered, observed = set(), []
        for relation in self.relations:
            if not relation.queries:
                raise ValueError("語の関係に照会条件がありません。解析結果を確認してください。")
            if relation.view is None:
                predicate = predicates.get(relation.predicate_root)
                if predicate is None or relation.queries != (predicate.query,):
                    raise ValueError("述語単独の照会と元の出現が一致しません。")
                covered.add(predicate.graph.root)
                continue
            view = relation.view
            predicate, target = predicates.get(view.predicate.root), targets.get(view.target_edge)
            if predicate is None or predicate.graph != view.predicate or target is None:
                raise ValueError("関係の述語に対応する出現がありません。")
            if (
                view.predicate_attachment != target.predicate_attachment
                or view.noun.root != target.noun_root
                or relation.predicate_root != predicate.graph.root
                or any(
                    q.predicate != predicate.query.predicate
                    or q.attachment != predicate.graph.nodes.index(target.predicate_attachment)
                    or q.dependency != target.dependency
                    or q.noun_role != target.noun_role
                    for q in relation.queries
                )
            ):
                raise ValueError("関係の照会と元の対象が一致しません。")
            if view.related_predicate is None:
                if any(
                    not q.noun
                    or q.related_predicate is not None
                    or q.related_attachment is not None
                    for q in relation.queries
                ):
                    raise ValueError("名詞表現の照会と元の対象が一致しません。")
            else:
                other = predicates.get(view.related_predicate.root)
                if (
                    other is None
                    or other.graph != view.related_predicate
                    or view.noun.root not in other.graph.nodes
                    or view.noun_feature_nodes
                    or any(
                        q.noun
                        or q.related_predicate != other.query.predicate
                        or q.related_attachment != other.graph.nodes.index(view.noun.root)
                        for q in relation.queries
                    )
                ):
                    raise ValueError("述語同士の照会と元の第二述語が一致しません。")
            if doc is not None:
                noun = tuple(
                    doc[i].lemma_
                    for i in view.noun_feature_nodes
                    if not doc[i].is_space and not doc[i].is_punct
                )
                if any(q.noun != noun for q in relation.queries):
                    raise ValueError("名詞の照会と元の語彙特徴が一致しません。")
            covered.add(predicate.graph.root)
            observed.append(view.target_edge)
        expected = [t.target_edge for t in self.targets if t.scope == "relation"]
        if sorted(observed) != sorted(expected) or covered != set(predicates):
            raise ValueError("認識した述語または関係に対応する照会がありません。")
