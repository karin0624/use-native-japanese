"""KWJAの基本句・形態素・述語項構造を直接参照する比較view。"""

from dataclasses import dataclass
from functools import cached_property

from rhoknp import BasePhrase, Document
from rhoknp.cohesion.argument import EndophoraArgument

from .models import (
    Analysis,
    Comparison,
    Endpoint,
    GraphView,
    OccurrenceView,
    PredicateEvidence,
    PredicateGraph,
    PredicateNode,
    PredicateOccurrence,
    Query,
    Relation,
    RelationTarget,
    lexical_identity,
)


@dataclass(frozen=True)
class UsageStructure:
    analysis: Analysis

    @cached_property
    def document(self) -> Document:
        return self.analysis.restore_doc()

    @cached_property
    def comparison(self) -> Comparison:
        return comparison_views(self.document, self.analysis)


def _lexical_morpheme(morpheme) -> bool:
    bp = morpheme.base_phrase
    return morpheme.pos not in {"助詞", "特殊"} or (
        morpheme == bp.head and any(name in bp.features for name in ("用言", "体言", "準体言"))
    )


def _lexical_nodes(bp: BasePhrase) -> tuple[int, ...]:
    return tuple(m.global_index for m in bp.morphemes if _lexical_morpheme(m))


def _graph(bp: BasePhrase) -> GraphView:
    # 同一文節で元係り先へ接続する接頭辞基本句は、公開品詞による語形成範囲である。
    members, pending = {bp.global_index: bp}, [bp]
    while pending:
        current = pending.pop()
        for child in current.children:
            if child.phrase == bp.phrase and child.head.pos == "接頭辞":
                if child.global_index in members:
                    raise ValueError("語形成の基本句依存が循環しています。")
                members[child.global_index] = child
                pending.append(child)
    nodes = tuple(
        sorted(
            m.global_index
            for member in members.values()
            for m in member.morphemes
            if m.pos != "特殊" or m == member.head
        )
    )
    edges = tuple(
        sorted(
            (
                member.parent.head.global_index,
                member.head.global_index,
                "基本句:" + member.dep_type.value,
            )
            for member in members.values()
            if member != bp and member.dep_type is not None
        )
    )
    return GraphView(bp.head.global_index, nodes, edges)


def _canonical(document: Document, graph: GraphView) -> PredicateGraph:
    local = {token: index for index, token in enumerate(graph.nodes)}
    edges = {child: (local[head], dependency) for head, child, dependency in graph.edges}
    return PredicateGraph(
        local[graph.root],
        tuple(
            PredicateNode(
                lexical_identity(m),
                f"{m.pos}-{m.subpos}",
                (m.conjtype, m.conjform),
                edges[i][0] if i in edges else None,
                edges[i][1] if i in edges else None,
            )
            for i in graph.nodes
            for m in (document.morphemes[i],)
        ),
    )


def _subtree(bp: BasePhrase) -> tuple[BasePhrase, ...]:
    selected, pending = [], [bp]
    while pending:
        current = pending.pop()
        if current in selected:
            raise ValueError("基本句の係り受けが循環しています。")
        selected.append(current)
        pending.extend(current.children)
    return tuple(sorted(selected, key=lambda item: item.global_index))


def _endpoint(analysis: Analysis, nodes: tuple[int, ...]) -> Endpoint:
    spans = []
    for i in sorted(nodes):
        start, end = analysis.offsets[i]
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    if not spans:
        raise ValueError("比較範囲に原文位置がありません。")
    return Endpoint(analysis.text[spans[0][0] : spans[-1][1]], tuple(spans))


def comparison_views(document: Document, analysis: Analysis) -> Comparison:
    """明示されたPASと基本句依存を、その解析単位・格のまま照会する。"""
    predicates = []
    by_bp = {}
    for bp in document.base_phrases:
        if "用言" not in bp.features:
            continue
        graph = _graph(bp)
        if graph.root not in graph.nodes:
            raise ValueError("用言基本句の主辞が比較範囲にありません。")
        evidence = PredicateEvidence(
            "用言",
            graph.root,
            graph.root,
            bp.parent.head.global_index if bp.parent is not None else graph.root,
            bp.dep_type.value if bp.dep_type is not None else "",
            bp.head.pos,
            bp.head.subpos,
        )
        item = PredicateOccurrence(graph, Query(_canonical(document, graph)), (evidence,))
        predicates.append(item)
        by_bp[bp.global_index] = item

    relations, targets, covered = [], [], set()
    pas_pairs = set()

    def add_relation(p_bp, n_bp, dependency, role, edge, *, nominal=False):
        predicate = by_bp[p_bp.global_index]
        graph = predicate.graph
        if n_bp.head.global_index in graph.nodes:
            targets.append(
                RelationTarget(
                    edge,
                    p_bp.head.global_index,
                    n_bp.head.global_index,
                    role,
                    dependency,
                    "composition",
                )
            )
            return
        other = by_bp.get(n_bp.global_index)
        # KWJAの体言/準体言素性に基づき、名詞化した用言の外側を名詞表現として扱う。
        predicative = (
            other is not None
            and not nominal
            and not any(name in n_bp.features for name in ("体言", "準体言"))
        )
        branches = _subtree(n_bp)
        all_nodes = tuple(m.global_index for bp in branches for m in bp.morphemes)
        edges = tuple(
            (bp.parent.head.global_index, bp.head.global_index, "基本句:" + bp.dep_type.value)
            for bp in branches
            if bp.parent in branches and bp.dep_type is not None
        )
        excluded = (
            {m.global_index for bp in _subtree(p_bp) for m in bp.morphemes}
            if p_bp in branches
            else set()
        )
        case_nodes = tuple(
            i
            for i in all_nodes
            if document.morphemes[i].pos == "助詞" and not _lexical_morpheme(document.morphemes[i])
        )
        features = (
            ()
            if predicative
            else tuple(
                i
                for i in all_nodes
                if i not in excluded
                and i not in case_nodes
                and _lexical_morpheme(document.morphemes[i])
            )
        )
        if not features and not predicative:
            raise ValueError("名詞表現の比較範囲に語彙がありません。")
        query = Query(
            predicate.query.predicate,
            graph.nodes.index(p_bp.head.global_index),
            dependency if predicative else "N-P",
            tuple(lexical_identity(document.morphemes[i]) for i in features),
            role if predicative else "noun",
            other.query.predicate if predicative else None,
            other.graph.nodes.index(n_bp.head.global_index) if predicative else None,
        )
        target = RelationTarget(
            edge, p_bp.head.global_index, n_bp.head.global_index, role, dependency, "relation"
        )
        view = OccurrenceView(
            analysis.document_id,
            graph,
            p_bp.head.global_index,
            GraphView(n_bp.head.global_index, all_nodes, edges),
            edge,
            features,
            case_nodes,
            (),
            tuple(
                item.graph.root for item in predicates if set(item.graph.nodes) <= set(all_nodes)
            ),
            other.graph if predicative else None,
        )
        targets.append(target)
        relations.append(
            Relation(
                _endpoint(analysis, graph.nodes),
                _endpoint(analysis, all_nodes),
                (query,),
                view,
                graph.root,
            )
        )
        covered.add(graph.root)

    for bp in document.base_phrases:
        if bp.global_index not in by_bp:
            continue
        for case, arguments in bp.pas.get_all_arguments(relax=False, include_optional=True).items():
            for argument in arguments:
                # 外界参照には原文位置がない。全情報はKNPへ残し、位置付き照会は行わない。
                if not isinstance(argument, EndophoraArgument):
                    continue
                noun = argument.base_phrase
                if not _lexical_nodes(noun):
                    continue
                dependency = "PAS:" + case
                edge = (bp.head.global_index, noun.head.global_index, dependency)
                add_relation(bp, noun, dependency, argument.type.value, edge)
                pas_pairs.add(frozenset((bp.global_index, noun.global_index)))

    for child in document.base_phrases:
        parent = child.parent
        if parent is None or child.dep_type is None:
            continue
        if frozenset((child.global_index, parent.global_index)) in pas_pairs:
            continue
        child_p, parent_p = by_bp.get(child.global_index), by_bp.get(parent.global_index)
        dependency = "基本句:" + child.dep_type.value
        edge = (parent.head.global_index, child.head.global_index, dependency)
        if parent_p is not None and (
            child_p is not None or any(name in child.features for name in ("体言", "準体言"))
        ):
            add_relation(parent, child, dependency, "dependent", edge)
        elif child_p is not None and any(name in parent.features for name in ("体言", "準体言")):
            add_relation(child, parent, dependency, "head", edge, nominal=True)

    for predicate in predicates:
        if predicate.graph.root not in covered:
            relations.append(
                Relation(
                    _endpoint(analysis, predicate.graph.nodes),
                    None,
                    (predicate.query,),
                    predicate_root=predicate.graph.root,
                )
            )
    result = Comparison(
        tuple(predicates),
        tuple(relations),
        tuple(e for p in predicates for e in p.evidence),
        tuple(targets),
    )
    result.validate(document)
    return result
