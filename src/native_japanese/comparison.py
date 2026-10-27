"""元依存木を変更せず、述語構成と評価辺の参照範囲を静的に定める。"""

import ginza
from spacy.tokens import Doc, Token

from .models import (
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
)

_CONSTITUENT = frozenset({"aux", "cop", "mark", "fixed", "compound", "nummod"})
_PREDICATIVE = frozenset({"VERB", "AUX", "ADJ"})
_SAME_BUNSETU = frozenset({"advcl", "acl", "amod"})
_ARGUMENT = frozenset({"nsubj", "obj", "iobj", "obl"})
_MODIFIER = frozenset({"acl", "amod"})
_CLAUSAL = frozenset({"ROOT", "ccomp", "xcomp", "advcl", "acl"})
_XPOS_PREDICATIVE = frozenset({"動詞", "形容詞", "形状詞"})


def _base(token: Token) -> str:
    return token.dep_.split(":", 1)[0]


def _graph(doc: Doc, indices: tuple[int, ...], root: int) -> GraphView:
    selected = frozenset(indices)
    return GraphView(
        root,
        indices,
        tuple(
            (doc[i].head.i, i, doc[i].dep_)
            for i in indices
            if doc[i].head.i in selected and doc[i].head.i != i
        ),
    )


def _canonical(doc: Doc, graph: GraphView) -> PredicateGraph:
    local = {i: j for j, i in enumerate(graph.nodes)}
    return PredicateGraph(
        local[graph.root],
        tuple(
            PredicateNode(
                doc[i].lemma_,
                doc[i].tag_,
                tuple(doc[i].morph.get("Inflection")),
                None if i == graph.root else local[doc[i].head.i],
                None if i == graph.root else doc[i].dep_,
            )
            for i in graph.nodes
        ),
    )


def _endpoint(doc: Doc, indices: tuple[int, ...]) -> Endpoint:
    if not indices:
        raise ValueError("原文位置を示す語がありません。")
    ranges = []
    for i in indices:
        token = doc[i]
        span = (token.idx, token.idx + len(token))
        if ranges and ranges[-1][1] == span[0]:
            ranges[-1] = (ranges[-1][0], span[1])
        else:
            ranges.append(span)
    return Endpoint(doc.text[ranges[0][0] : ranges[-1][1]], tuple(ranges))


def comparison_views(doc: Doc, document_id: str) -> Comparison:
    """構成内部の元辺と、構成間の元対象辺を分けて参照する。"""
    from .parser import validate_doc

    validate_doc(doc, doc.text)
    bunsetsu = {}
    for token in doc:
        span = ginza.bunsetu_span(token)
        if span.doc is not doc or not span.start <= token.i < span.end:
            raise ValueError("解析された文節に中心となる語が含まれていません。")
        bunsetsu[token.i] = span.start

    adjacency = {token.i: [] for token in doc}
    for child in doc:
        head = child.head
        if head == child or any(t.is_space or t.is_punct for t in (head, child)):
            continue
        dependency = _base(child)
        if dependency in _CONSTITUENT or (
            dependency in _SAME_BUNSETU
            and bunsetsu[head.i] == bunsetsu[child.i]
            and head.pos_ in _PREDICATIVE
            and child.pos_ in _PREDICATIVE
        ):
            adjacency[head.i].append(child.i)
            adjacency[child.i].append(head.i)

    components = {}
    graphs = {}
    canonical = {}
    for token in doc:
        if token.i in components or token.is_space or token.is_punct:
            continue
        selected = {token.i}
        queue = [token.i]
        while queue:
            for i in adjacency[queue.pop()]:
                if i not in selected:
                    selected.add(i)
                    queue.append(i)
        indices = tuple(sorted(selected))
        roots = [i for i in indices if doc[i].head.i not in selected or doc[i].head.i == i]
        if len(roots) != 1:
            raise ValueError("述語構成の元依存木に一意の根がありません。")
        root = roots[0]
        graph = _graph(doc, indices, root)
        graphs[root] = graph
        canonical[root] = _canonical(doc, graph)
        components.update((i, root) for i in indices)

    evidence = {}

    def add(selected: Token, observed: Token, kind: str):
        if selected.i not in components:
            return
        evidence.setdefault(components[selected.i], []).append(
            PredicateEvidence(
                kind,
                selected.i,
                observed.i,
                observed.head.i,
                observed.dep_,
                observed.pos_,
                observed.tag_,
            )
        )

    for token in doc:
        dependency = _base(token)
        if dependency in _ARGUMENT:
            add(token.head, token, "argument_head")
        elif dependency in _MODIFIER:
            add(token, token, "modifier_dependent")
        if dependency in _CLAUSAL:
            if token.pos_ in {"VERB", "ADJ"}:
                add(token, token, "clausal_upos")
            elif token.tag_.split("-", 1)[0] in _XPOS_PREDICATIVE:
                add(token, token, "clausal_xpos")
        if dependency in {"aux", "cop"}:
            add(token.head, token, "aux_cop_head")

    predicates = tuple(
        PredicateOccurrence(graphs[root], Query(canonical[root]), tuple(dict.fromkeys(reasons)))
        for root, reasons in sorted(evidence.items())
    )
    relations = []
    related = set()
    for child in doc:
        dependency = _base(child)
        if dependency in _ARGUMENT:
            p, n, role = child.head, child, "dependent"
        elif dependency in _MODIFIER:
            p, n, role = child, child.head, "head"
        else:
            continue
        if p.i not in components or n.is_space or n.is_punct:
            continue
        root = components[p.i]
        graph = graphs[root]
        if n.i in graph.nodes:
            continue
        related.add(root)
        noun_nodes = tuple(sorted(t.i for t in n.subtree))
        excluded = {t.i for t in p.subtree} if role == "head" else set()
        feature_nodes = tuple(i for i in noun_nodes if i not in excluded)
        noun = tuple(
            doc[i].lemma_ for i in feature_nodes if not doc[i].is_space and not doc[i].is_punct
        )
        query = Query(canonical[root], graph.nodes.index(p.i), child.dep_, noun, role)
        view = OccurrenceView(
            document_id,
            graph,
            p.i,
            _graph(doc, noun_nodes, n.i),
            (child.head.i, child.i, child.dep_),
            feature_nodes,
        )
        relations.append(
            Relation(_endpoint(doc, graph.nodes), _endpoint(doc, noun_nodes), (query,), view)
        )
    for predicate in predicates:
        if predicate.graph.root not in related:
            relations.append(
                Relation(_endpoint(doc, predicate.graph.nodes), None, (predicate.query,))
            )
    return Comparison(predicates, tuple(relations))
