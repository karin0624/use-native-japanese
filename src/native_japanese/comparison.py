"""元依存木に基づき、述語構成と評価辺の参照範囲を静的に定める。"""

from dataclasses import dataclass, replace
from functools import cached_property

import ginza
from spacy.tokens import Doc, Token

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
)
from .vocabulary import restore_lexeme_view


@dataclass(frozen=True)
class UsageStructure:
    """Analysisから用法照合用のDocと比較構造を構築する。"""

    analysis: Analysis

    @cached_property
    def document(self) -> Doc:
        from .parser import validate_doc

        doc = Doc(restore_lexeme_view(self.analysis.vocabulary)).from_bytes(self.analysis.snapshot)
        validate_doc(doc, self.analysis.text)
        return doc

    @cached_property
    def comparison(self) -> Comparison:
        return comparison_views(self.document, self.analysis.document_id)


_PREDICATIVE = frozenset({"VERB", "AUX", "ADJ"})
_SAME_BUNSETU = frozenset({"advcl", "acl", "amod"})
_ARGUMENT_ROLE = {
    "nsubj": "nsubj",
    "csubj": "nsubj",
    "obj": "obj",
    "iobj": "iobj",
    "obl": "obl",
    "ccomp": "ccomp",
    "xcomp": "xcomp",
    "advcl": "advcl",
}
_MODIFIER = frozenset({"acl", "amod"})
_CLAUSAL = frozenset({"csubj", "ccomp", "xcomp", "advcl", "acl"})
_XPOS_PREDICATIVE = frozenset({"動詞", "形容詞", "形状詞"})


def _base(token: Token) -> str:
    return token.dep_.split(":", 1)[0]


def _lexical_inflection(token: Token) -> bool:
    """公開された活用情報・詳細品詞・UPOS・依存関係から語彙述語の活用を判定する。"""
    return (
        bool(token.morph.get("Inflection"))
        and token.tag_.split("-", 1)[0] in _XPOS_PREDICATIVE
        and token.pos_ not in {"AUX", "SCONJ", "ADP", "PART", "CCONJ"}
        and _base(token) not in {"aux", "cop", "fixed", "mark", "case", "compound"}
    )


def _predicate_roles(doc: Doc) -> tuple[PredicateEvidence, ...]:
    """文書全体のUD情報から述語役割の根拠を収集する。"""
    roles = []

    def add(selected, observed, kind):
        if selected.is_space or selected.is_punct:
            return
        roles.append(
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
        if _lexical_inflection(token):
            add(token, token, "inflected_lexical_occurrence")
        if dependency in _CLAUSAL:
            add(token, token, "clausal_dependent")
        elif dependency == "amod":
            add(token, token, "adjectival_modifier")
        elif dependency == "advmod" and token.pos_ in {"VERB", "ADJ"}:
            add(token, token, "adverbial_predicate")
        elif dependency in {"ROOT", "conj", "parataxis"}:
            if token.pos_ in {"VERB", "ADJ"}:
                add(token, token, "clausal_upos")
            elif token.tag_.split("-", 1)[0] in _XPOS_PREDICATIVE:
                add(token, token, "clausal_xpos")
        # 項の支配と機能語の接続は、核が名詞などの非動詞述語である場合も証拠になる。
        if dependency in {"nsubj", "csubj", "obj", "iobj", "obl", "ccomp", "xcomp"}:
            add(token.head, token, "argument_head")
        if dependency == "advcl":
            add(token.head, token, "clausal_modifier_head")
        if dependency in {"aux", "cop"}:
            add(token.head, token, "aux_cop_head")
    return tuple(dict.fromkeys(roles))


def _fixed_unit(token: Token) -> tuple[Token, ...]:
    selected = [token]
    for member in selected:
        selected.extend(child for child in member.children if _base(child) == "fixed")
    return tuple(selected)


def _construction_edge(child: Token, bunsetsu: dict[int, int]) -> bool:
    """語形成・補助構成と、格標識・名詞化・節接続を元解析で区別する。"""
    head, dependency = child.head, _base(child)
    if dependency in {"aux", "cop", "compound"}:
        return True
    same = bunsetsu[head.i] == bunsetsu[child.i]
    if not same:
        return False
    if dependency == "fixed":
        return True
    if dependency == "mark":
        # GiNZAの文脈上のUPOSを使う。XPOSが動詞でもSCONJなら節接続である。
        return any(t != child and t.pos_ in _PREDICATIVE for t in _fixed_unit(child))
    if dependency == "nummod":
        return head.tag_.startswith("接尾辞-") and child.pos_ == "NUM"
    return (
        dependency in _SAME_BUNSETU
        and head.pos_ in _PREDICATIVE
        and child.pos_ in _PREDICATIVE
        and any(t.tag_ == "動詞-非自立可能" for t in (head, child))
    )


def _relation_targets(doc: Doc) -> tuple[RelationTarget, ...]:
    targets = []
    for child in doc:
        dependency = _base(child)
        if dependency in _ARGUMENT_ROLE:
            p, n, role, relation = child.head, child, "dependent", _ARGUMENT_ROLE[dependency]
        elif dependency == "advmod" and child.pos_ in {"VERB", "ADJ"}:
            p, n, role, relation = child.head, child, "dependent", dependency
        elif dependency in _MODIFIER:
            p, n, role, relation = child, child.head, "head", dependency
        else:
            continue
        scope = "nonlexical" if any(t.is_space or t.is_punct for t in (p, n)) else "relation"
        targets.append(
            RelationTarget((child.head.i, child.i, child.dep_), p.i, n.i, role, relation, scope)
        )
    return tuple(targets)


def _nominalized(doc: Doc, predicate: PredicateOccurrence) -> bool:
    # 深い子節やP内部の固定機能表現を、当該Pの名詞化の証拠にしない。
    return any(
        len(unit.nodes) == 1
        and unit.root not in predicate.graph.nodes
        and (
            doc[unit.root].tag_ == "助詞-準体助詞"
            or doc[unit.root].tag_.startswith("接尾辞-名詞的")
        )
        for unit in predicate.marker_units
    )


def _nominal_expression(token: Token) -> bool:
    return not _lexical_inflection(token) and (
        token.pos_ in {"NOUN", "PROPN", "PRON", "NUM"}
        or _base(token) in {"nsubj", "obj", "iobj", "obl"}
        or any(
            _base(child) == "mark"
            and (child.tag_ == "助詞-準体助詞" or child.tag_.startswith("接尾辞-名詞的"))
            and len(_fixed_unit(child)) == 1
            for child in token.children
        )
    )


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
    roles = _predicate_roles(doc)
    targets = _relation_targets(doc)
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
        if _construction_edge(child, bunsetsu):
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

    for role in roles:
        evidence.setdefault(components[role.selected_token], []).append(role)

    predicates = tuple(
        PredicateOccurrence(
            graphs[root],
            Query(canonical[root]),
            tuple(dict.fromkeys(reasons)),
            tuple(
                _graph(doc, tuple(sorted(t.i for t in _fixed_unit(marker))), marker.i)
                for marker in doc
                if _base(marker) == "mark" and marker.head.i in graphs[root].nodes
            ),
        )
        for root, reasons in sorted(evidence.items())
    )
    relations = []
    predicate_by_root = {item.graph.root: item for item in predicates}
    related = set()
    resolved_targets = []
    for target in targets:
        if target.scope != "relation":
            resolved_targets.append(target)
            continue
        p, n, role = doc[target.predicate_attachment], doc[target.noun_root], target.noun_role
        root = components[p.i]
        if target.dependency == "advmod" and root not in evidence:
            if not _nominal_expression(p):
                resolved_targets.append(replace(target, scope="non_nominal_modifier"))
                continue
            # 修飾先に述語役割がない場合は、語彙的な述語とその修飾対象を照会する。
            p, n, role = n, p, "head"
            root = components[p.i]
            target = replace(target, predicate_attachment=p.i, noun_root=n.i, noun_role=role)
        graph = graphs[root]
        if n.i in graph.nodes:
            resolved_targets.append(replace(target, scope="composition"))
            continue
        resolved_targets.append(target)
        if root not in evidence:
            raise ValueError("評価する関係の述語に、文中の役割がありません。")
        related.add(root)
        noun_nodes = tuple(sorted(t.i for t in n.subtree))
        excluded = {t.i for t in p.subtree} if role == "head" else set()
        case_nodes = tuple(
            sorted(
                {
                    member.i
                    for i in noun_nodes
                    if i != n.i and _base(doc[i]) == "case"
                    for member in _fixed_unit(doc[i])
                }
            )
        )
        feature_nodes = tuple(i for i in noun_nodes if i not in excluded and i not in case_nodes)
        other = predicate_by_root.get(components[n.i])
        predicative_relation = (
            other is not None
            and not _nominalized(doc, other)
            and _base(doc[target.target_edge[1]]) != "csubj"
            and not (
                role == "head"
                and n.pos_ in {"NOUN", "PROPN", "PRON", "NUM"}
                and not _lexical_inflection(n)
            )
        )
        if predicative_relation:
            feature_nodes = ()
        noun = tuple(
            doc[i].lemma_ for i in feature_nodes if not doc[i].is_space and not doc[i].is_punct
        )
        if not noun and not predicative_relation:
            raise ValueError("評価する名詞表現に語彙特徴がありません。")
        query = Query(
            canonical[root],
            graph.nodes.index(p.i),
            target.dependency,
            noun,
            role,
            other.query.predicate if predicative_relation else None,
            other.graph.nodes.index(n.i) if predicative_relation else None,
        )
        view = OccurrenceView(
            document_id,
            graph,
            p.i,
            _graph(doc, noun_nodes, n.i),
            target.target_edge,
            feature_nodes,
            case_nodes,
            tuple(i for i in noun_nodes if _base(doc[i]) == "mark"),
            tuple(
                item.graph.root for item in predicates if set(item.graph.nodes) <= set(noun_nodes)
            ),
            other.graph if predicative_relation else None,
        )
        relations.append(
            Relation(_endpoint(doc, graph.nodes), _endpoint(doc, noun_nodes), (query,), view, root)
        )
    for predicate in predicates:
        if predicate.graph.root not in related:
            relations.append(
                Relation(
                    _endpoint(doc, predicate.graph.nodes),
                    None,
                    (predicate.query,),
                    predicate_root=predicate.graph.root,
                )
            )
    result = Comparison(predicates, tuple(relations), roles, tuple(resolved_targets))
    result.validate(doc)
    return result
