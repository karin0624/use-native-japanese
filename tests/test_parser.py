"""原Docの保持と、元辺を参照する述語構成・名詞特徴を確かめる。"""

from dataclasses import replace

import ginza
import numpy as np
import pytest
import spacy
import srsly
from spacy.tokens import Doc
from spacy.vocab import Vocab

from native_japanese.models import PARSER_VERSIONS
from native_japanese.parser import Parser, _merge_identifiers, adapt_doc


@pytest.fixture(scope="module")
def parser():
    instance = Parser()
    yield instance
    instance.close()


def comparable(analysis):
    # NN tensorのbatch演算差は、token属性・依存辺・viewの同一性と分ける。
    rows = tuple(
        replace(row, view=replace(row.view, document_id="同一原文") if row.view else None)
        for row in analysis.relations
    )
    return analysis.restore_doc().to_bytes(exclude=["tensor"]), analysis.predicates, rows


def test_full_document_snapshot_is_independent_and_lossless(parser):
    text = "😀 説明にもっともらしさを与える。\r\n彼は仕事をやり始める。"
    doc = parser._require_nlp()(text)
    original = doc.to_bytes()
    result = adapt_doc(doc, text)
    assert doc.to_bytes() == original == result.snapshot
    restored = result.restore_doc()
    assert restored.to_bytes() == original
    assert np.array_equal(restored.tensor, doc.tensor)
    assert restored.vocab.vectors.shape == doc.vocab.vectors.shape
    for before, after in zip(doc, restored, strict=True):
        assert np.array_equal(before.vector, after.vector)
        assert before.has_vector == after.has_vector
        assert before.vector_norm == after.vector_norm
        assert before.norm_ == after.norm_
    assert srsly.msgpack_dumps(restored.user_data) == srsly.msgpack_dumps(doc.user_data)
    restored[0].lemma_ = "変更"
    assert result.restore_doc().to_bytes() == original
    assert doc.to_bytes() == original
    for relation in result.relations:
        for endpoint in (relation.predicate, relation.argument):
            if endpoint:
                assert text[endpoint.ranges[0][0] : endpoint.ranges[-1][1]] == endpoint.text


def test_restored_lexeme_mutation_cannot_change_later_restoration_or_lazy_comparison(parser):
    analysis = parser.analyze("本を読む。\n")
    original = analysis.snapshot
    first = analysis.restore_doc()
    assert not first[0].is_punct
    assert first[-2].is_punct and first[-1].is_space
    first.vocab["本"].is_punct = True
    first.vocab["。"].is_punct = False
    first.vocab["\n"].is_space = False

    restored = analysis.restore_doc()
    assert restored.vocab is not first.vocab
    assert not restored[0].is_punct
    assert restored[-2].is_punct and restored[-1].is_space
    assert restored.to_bytes() == analysis.snapshot == original
    assert any(
        row.queries[0].dependency == "obj" and row.view.noun.root == 0 for row in analysis.relations
    )
    other = parser.analyze("本を読む。\n")
    assert comparable(analysis) == comparable(other)


def test_nominalized_auxiliary_and_its_original_edges_are_preserved(parser):
    analysis = parser.analyze("説明にもっともらしさを与える。")
    doc = analysis.restore_doc()
    relation = next(row for row in analysis.relations if row.queries[0].dependency == "obj")
    query, view = relation.queries[0], relation.view
    assert [doc[i].text for i in view.noun.nodes] == ["もっとも", "らし", "さ", "を"]
    assert query.noun == ("もっとも", "らしい", "さ")
    assert view.noun_case_nodes == (5,)
    assert doc[view.noun.root].pos_ == "AUX"
    assert {(3, 2, "advmod"), (3, 4, "mark"), (3, 5, "case")} <= set(view.noun.edges)
    assert relation.argument.text == "もっともらしさを"
    assert "もっともらしさ" not in [token.lemma_ for token in doc]


@pytest.mark.parametrize(
    "text,components",
    [
        ("重複を畳ませる。", ("畳む", "せる")),
        ("重複を畳んでいる。", ("畳む", "で", "いる")),
        ("彼は仕事をやり始める。", ("やる", "始める")),
        ("雨が降るかもしれない。", ("降る", "か", "も", "しれる", "ない")),
    ],
)
def test_entire_predicate_composition_is_required(parser, text, components):
    analysis = parser.analyze(text)
    relation = next(row for row in analysis.relations if row.view is not None)
    query = relation.queries[0]
    assert tuple(node.lemma for node in query.predicate.nodes) == components
    root = query.predicate.nodes[query.predicate.root]
    assert root.head is None and root.dependency is None
    assert all(row.queries[0].dependency not in {"aux", "fixed"} for row in analysis.relations)
    assert query.predicate != parser.analyze("重複を畳む。").relations[0].queries[0].predicate


def test_reverse_modifier_preserves_full_n_graph_and_excludes_only_p_branch_from_features(parser):
    first = parser.analyze("本を読む人")
    second = parser.analyze("新聞を読む人")
    rows = [
        next(row for row in analysis.relations if row.queries[0].noun_role == "head")
        for analysis in (first, second)
    ]
    assert rows[0].queries == rows[1].queries
    assert rows[0].queries[0].noun == ("人",)
    assert rows[0].argument.text == "本を読む人"
    assert rows[1].argument.text == "新聞を読む人"
    for analysis, relation in zip((first, second), rows, strict=True):
        doc, view = analysis.restore_doc(), relation.view
        assert view.noun.nodes == tuple(sorted(t.i for t in doc[view.noun.root].subtree))
        assert set(view.predicate.nodes) <= set(view.noun.nodes)
        assert not set(view.predicate.nodes) & set(view.noun_feature_nodes)
        assert view.target_edge == (3, 2, "acl")


def test_hazu_noun_is_not_promoted_into_reading_predicate(parser):
    analysis = parser.analyze("彼は本を読むはずだ。")
    relation = next(row for row in analysis.relations if row.queries[0].dependency == "acl")
    assert tuple(n.lemma for n in relation.queries[0].predicate.nodes) == ("読む",)
    assert relation.queries[0].noun == ("はず", "だ")
    assert {tuple(n.lemma for n in item.query.predicate.nodes) for item in analysis.predicates} == {
        ("読む",),
        ("はず", "だ"),
    }


def test_predicate_only_evidence_does_not_register_ordinary_nouns(parser):
    bare = parser.analyze("畳む。")
    assert len(bare.predicates) == len(bare.relations) == 1
    assert bare.restore_doc()[0].pos_ == "NOUN"
    evidence = bare.predicates[0].evidence
    assert any(e.kind == "clausal_xpos" and e.upos == "NOUN" for e in evidence)
    assert bare.relations[0].argument is None
    assert (
        bare.relations[0].queries[0].predicate
        == parser.analyze("重複を畳む。").predicates[0].query.predicate
    )
    assert not parser.analyze("処理の再利用").predicates
    ordinary = parser.analyze("彼が本を読む。")
    assert len(ordinary.predicates) == 1
    assert ordinary.predicates[0].graph.nodes == (4,)
    assert {r.queries[0].dependency for r in ordinary.relations} == {"nsubj", "obj"}
    observed = [e for e in ordinary.predicates[0].evidence if e.kind == "argument_head"]
    assert {(e.head, e.token, e.dependency) for e in observed} == {(4, 0, "nsubj"), (4, 2, "obj")}


def test_parent_component_traversal_keeps_original_attachment_separate_from_root():
    # 元辺の対照。objの接続先は構成内の子であり、構成根へ移さない。
    doc = Doc(
        Vocab(),
        words=["N", "P", "A"],
        spaces=[False] * 3,
        heads=[1, 2, 2],
        deps=["obj", "compound", "ROOT"],
        pos=["NOUN", "VERB", "VERB"],
        tags=["名詞", "動詞", "動詞"],
        lemmas=["N", "P", "A"],
    )
    doc.user_data["bunsetu_bi_labels"] = ["B"] * 3
    row = adapt_doc(doc, doc.text).relations[0]
    assert row.view.predicate.nodes == (1, 2)
    assert row.view.predicate.root == 2 and row.view.predicate_attachment == 1
    assert row.queries[0].predicate.root == 1 and row.queries[0].attachment == 0
    assert row.view.target_edge == (1, 0, "obj")
    assert row.queries[0].predicate.nodes[0].head == 1
    assert row.queries[0].predicate.nodes[1].head is None


@pytest.mark.parametrize("dependency", ["nmod", "advcl"])
def test_unselected_edges_are_not_reinterpreted_as_argument_or_component(dependency):
    doc = Doc(
        Vocab(),
        words=["N", "P"],
        spaces=[False] * 2,
        heads=[1, 1],
        deps=[dependency, "ROOT"],
        pos=["NOUN", "VERB"],
        tags=["名詞", "動詞"],
        lemmas=["N", "P"],
    )
    doc.user_data["bunsetu_bi_labels"] = ["B", "B"]
    analysis = adapt_doc(doc, doc.text)
    # UD advclは従属節の述語を指し、NOUNも非動詞述語の核になれる。
    expected = {(0,), (1,)} if dependency == "advcl" else {(1,)}
    assert {p.graph.nodes for p in analysis.predicates} == expected
    if dependency == "advcl":
        assert {row.queries[0].dependency for row in analysis.relations} == {None, "advcl"}
    else:
        assert all(row.argument is None for row in analysis.relations)
    assert analysis.restore_doc()[0].dep_ == dependency


def test_literal_text_and_batch_results_preserve_all_original_positions(parser):
    texts = ["😀 `重複`を`畳む`。\r\n重複を畳んでいる。\r\n", "", " \n ", "電気を消す。"]
    batched = list(parser.analyze_many(iter(texts)))
    assert [comparable(a) for a in batched] == [comparable(parser.analyze(t)) for t in texts]
    assert [a.text for a in batched] == texts
    assert not batched[1].relations and not batched[2].relations


@pytest.mark.parametrize(
    "text,identifier,predicate,dependency",
    [
        ("sample12moduleは値を返す。", "sample12module", "返す", "nsubj"),
        ("3sample12moduleは値を返す。", "3sample12module", "返す", "nsubj"),
        ("sample_module-v2.3.zipを配置します。", "sample_module-v2.3.zip", "配置", "obj"),
        ("Node.jsを使う。", "Node.js", "使う", "obj"),
    ],
)
def test_identifiers_are_single_tokens_before_parsing_and_keep_external_edges(
    parser, text, identifier, predicate, dependency
):
    pipeline = parser._require_nlp()
    doc = pipeline.make_doc(text)
    start, end = text.index(identifier), text.index(identifier) + len(identifier)
    assert len(doc.char_span(start, end, alignment_mode="strict")) > 1
    for name, component in pipeline.pipeline:
        if name in {"tok2vec", "parser", "morphologizer"}:
            span = doc.char_span(start, end, alignment_mode="strict")
            assert len(span) == 1 and span[0].text == span[0].lemma_ == identifier
            assert "sub_tokens" not in doc.user_data
        doc = component(doc)
    token = doc.char_span(start, end)[0]
    assert token.dep_ == dependency and token.head.lemma_ == predicate
    analysis = adapt_doc(doc, text)
    assert comparable(analysis) == comparable(parser.analyze(text))
    row = next(row for row in analysis.relations if row.view and row.view.noun.root == token.i)
    assert row.argument.ranges[0][0] == start and identifier in row.queries[0].noun


def test_identifier_merge_preserves_partial_tokens_single_lemmas_and_numeric_punctuation():
    words = [
        "alpha_beta日本語",
        "npm",
        "20.18",
        "2023",
        "-",
        "11",
        "-",
        "25",
        "1",
        "-",
        "2",
        "3",
        "alpha",
        "42",
        "beta",
        "alpha",
        "42",
        "beta",
    ]
    doc = Doc(
        Vocab(),
        words=words,
        spaces=[i in {0, 1, 2, 7, 10, 14} for i in range(len(words))],
        lemmas=["NPM" if word == "npm" else word for word in words],
    )
    original = doc.text
    assert _merge_identifiers(doc) is doc
    assert [t.text for t in doc] == words[:11] + ["3alpha42beta", "alpha42beta"]
    assert doc[1].lemma_ == "NPM" and doc.text == original


def test_identifier_pipeline_preserves_ordinary_japanese_analysis(parser):
    baseline = spacy.load("ja_ginza", exclude=["ner"])
    for text in [
        "家でお皿を洗います。",
        "本を読む人",
        "重複を畳んでいる。",
        "npmでgit cloneを実行する。",
    ]:
        original = baseline(text)
        # 構文解析前に消費するtokenizer補助値だけがパイプライン間で異なる。
        original.user_data.pop("sub_tokens", None)
        assert comparable(adapt_doc(parser._require_nlp()(text), text)) == comparable(
            adapt_doc(original, text)
        )


def test_repeated_occurrences_keep_separate_original_token_and_character_positions(parser):
    sentence = "sample_moduleを読む。"
    rows = parser.analyze(sentence * 2).relations
    assert len(rows) == 2 and rows[0].queries == rows[1].queries
    assert rows[0].view.noun.root != rows[1].view.noun.root
    assert rows[0].argument.ranges[0][0] == 0
    assert rows[1].argument.ranges[0][0] == len(sentence)


@pytest.mark.parametrize(
    "words,pos,dependency",
    [
        (["考える", "\n"], ["VERB", "NOUN"], "acl"),
        (["精度", "\n"], ["NOUN", "VERB"], "obj"),
        (["読む", "#"], ["VERB", "NOUN"], "acl"),
    ],
)
def test_space_and_punctuation_are_preserved_but_not_relation_candidates(words, pos, dependency):
    # 空白・記号の元Lexeme属性を持つ言語Vocabを使い、UPOSだけ誤った構造対照にする。
    doc = Doc(
        spacy.blank("ja").vocab,
        words=words,
        spaces=[False] * 2,
        pos=pos,
        heads=[1, 1],
        deps=[dependency, "ROOT"],
        lemmas=words,
    )
    doc.user_data["bunsetu_bi_labels"] = ["B", "B"]
    analysis = adapt_doc(doc, doc.text)
    assert not any(row.argument for row in analysis.relations)
    assert analysis.restore_doc().text == doc.text


def test_bunsetu_contract_violation_is_a_system_error(parser, monkeypatch):
    doc = parser._require_nlp()("重複を畳む。")
    monkeypatch.setattr(ginza, "bunsetu_span", lambda token: token.doc[0:1])
    with pytest.raises(ValueError, match="中心となる語が含まれていません"):
        _ = adapt_doc(doc, doc.text).comparison


def test_surface_changes_and_model_failure_are_not_unobserved(parser):
    doc = Doc(Vocab(), words=["重複"], spaces=[False])
    with pytest.raises(ValueError, match="原文全文"):
        adapt_doc(doc, "別の本文")
    instance = Parser()

    def fail(_):
        raise RuntimeError("解析失敗")

    instance._nlp = fail
    with pytest.raises(RuntimeError, match="解析失敗"):
        instance.analyze("重複を畳む。")
    instance.close()
    instance.close()
    with pytest.raises(RuntimeError, match="終了"):
        instance.analyze("重複を畳む。")
    with pytest.raises(RuntimeError, match="終了"):
        list(instance.analyze_many(["重複を畳む。"]))


def test_different_dependency_version_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "native_japanese.parser.version",
        lambda package: "異なる版" if package == "spacy" else PARSER_VERSIONS[package],
    )
    with pytest.raises(RuntimeError, match="解析に必要なバージョンと異なります.*spacy"):
        Parser()


def test_issue19_original_paragraph_keeps_full_nominal_argument_and_edges(parser):
    text = (
        "目的は、AIが出した誤った日本語をリアルタイムで位置付き指摘し、可能なら訂正案を出すこと。"
        "「重複を畳む」のような用例に裏付けられない語の使い方を中心に、スクリプトやtextlintから利用できるようにする。"
        "将来の商用利用を考慮する。用例のある正用同士の意味差、意味のもっともらしさ、作者やAI生成の確率、"
        "分野ごとの文体の適否を、今回の検出対象へ加えない。"
    )
    analysis = parser.analyze(text)
    doc = analysis.restore_doc()
    stem = next(t for t in doc if t.text == "らし")
    assert stem.pos_ == "AUX" and stem.dep_ == "obl"
    relation = next(
        row for row in analysis.relations if row.view and row.view.target_edge[1] == stem.i
    )
    view, query = relation.view, relation.queries[0]
    assert view.noun.nodes == tuple(sorted(t.i for t in stem.subtree))
    assert {"もっとも", "らしい", "さ"} <= set(query.noun)
    assert all(doc[i].dep_ != "case" for i in view.noun_feature_nodes)
    assert set(view.noun_case_nodes) <= set(view.noun.nodes)
    assert {
        (head, child, dep)
        for head, child, dep in view.noun.edges
        if doc[child].text in {"もっとも", "さ"}
    } == {(stem.i, stem.i - 1, "advmod"), (stem.i, stem.i + 1, "mark")}
    assert relation.argument.ranges == ((116, 141),)
    assert (
        relation.argument.text
        == text[116:141]
        == "用例のある正用同士の意味差、意味のもっともらしさ、"
    )
    assert relation.predicate.text == "加えない"
    assert not any(
        row.argument and row.argument.ranges == ((137, 139),) for row in analysis.relations
    )


@pytest.mark.parametrize(
    "plain,causative",
    [
        ("母が布を畳む。", "母が布を畳ませる。"),
        ("先生が字を書く。", "先生が字を書かせる。"),
    ],
)
def test_causative_requires_its_own_composition_with_same_argument_relations(
    parser, plain, causative
):
    from native_japanese.examples import predicate_key

    rows = [
        next(row for row in parser.analyze(text).relations if row.queries[0].dependency == "obj")
        for text in (plain, causative)
    ]
    first, second = [row.queries[0] for row in rows]
    assert (first.noun, first.dependency, first.noun_role, first.attachment) == (
        second.noun,
        second.dependency,
        second.noun_role,
        second.attachment,
    )
    assert first.predicate.nodes[0].lemma == second.predicate.nodes[0].lemma
    assert predicate_key(first.predicate) != predicate_key(second.predicate)
    assert len(second.predicate.nodes) > len(first.predicate.nodes)


@pytest.mark.parametrize("verb", ["走る", "泳ぐ"])
def test_nominalized_clause_keeps_inner_predicate_and_outer_subject(parser, verb):
    # UD ja/csubj: 語彙述語が主語節の端点であり、の(mark)・が(case)とは別役割。
    analysis = parser.analyze(f"{verb}のが好きだ。")
    predicates = {tuple(n.lemma for n in p.query.predicate.nodes): p for p in analysis.predicates}
    assert set(predicates) == {(verb,), ("好き", "だ")}
    inner = predicates[(verb,)]
    assert any(e.dependency == "csubj" for e in inner.evidence)
    outer = next(
        row for row in analysis.relations if row.view and row.view.target_edge[2] == "csubj"
    )
    assert outer.predicate.text == "好きだ"
    assert outer.argument.text == f"{verb}のが"
    assert outer.queries[0].dependency == "nsubj"
    assert outer.queries[0].noun == (verb, "の")
    assert outer.view.noun_case_nodes == (2,)
    assert outer.view.noun_mark_nodes == (1,)
    assert outer.view.noun_predicate_roots == (inner.graph.root,)
    assert any(row.argument is None and row.predicate.text == verb for row in analysis.relations)


def test_case_markers_are_preserved_separately_from_the_compared_words(parser):
    # 比較対象は父と似る。助詞の選択自体は今回の判定対象ではない。
    first, second = [parser.analyze(text).relations[0] for text in ("父に似る。", "父から似る。")]
    assert first.queries == second.queries
    assert first.queries[0].noun == ("父",)
    assert first.argument.text == "父に" and second.argument.text == "父から"
    assert first.view.noun_case_nodes == second.view.noun_case_nodes == (1,)


@pytest.mark.parametrize(
    "text,predicate,marker,units",
    [
        ("本を読むために走る。", ("読む",), ("ため", "に"), ((3, 4),)),
        ("本を読むのが好きだ。", ("読む",), ("の",), ((3,),)),
        ("本を読んでいるのが好きだ。", ("読む", "で", "いる"), ("の",), ((3, 4), (5,))),
        ("走るという話を聞く。", ("走る",), ("と", "いう"), ()),
        ("走るとしたら疲れる。", ("走る",), ("と", "する", "た"), ()),
        ("雨が降るにしても、出かける。", ("降る",), ("に", "する", "て", "も"), ((3, 4, 5, 6),)),
        ("彼が帰るにつれて、静かになる。", ("帰る",), ("に", "つれる", "て"), ((3, 4, 5),)),
        ("彼が帰るに従って、静かになる。", ("帰る",), ("に", "従う", "て"), ((3, 4, 5),)),
        ("走るのではない。", ("走る", "の", "だ", "は", "ない"), ("の",), ((1, 2, 3, 4),)),
        ("雨が降るので試合は中止。", ("降る", "の", "だ"), ("の", "だ"), ((3, 4),)),
    ],
)
def test_clause_markers_do_not_replace_predicate_construction(
    parser, text, predicate, marker, units
):
    # Pへの参照範囲と元mark/fixedの機能単位は別の契約で、所属を語の意味と同一視しない。
    analysis = parser.analyze(text)
    occurrence = next(p for p in analysis.predicates if p.graph.root == 2 or p.graph.root == 0)
    assert tuple(n.lemma for n in occurrence.query.predicate.nodes) == predicate
    doc = analysis.restore_doc()
    assert all(word in {t.lemma_ for t in doc} for word in marker)
    assert tuple(unit.nodes for unit in occurrence.marker_units) == units
    assert tuple(unit.root for unit in occurrence.marker_units) == tuple(
        nodes[0] for nodes in units
    )
    for unit in occurrence.marker_units:
        assert doc[unit.root].dep_ == "mark"
        assert all(doc[i].dep_ == "fixed" for i in unit.nodes if i != unit.root)


@pytest.mark.parametrize("text", ["読むのは楽しい。", "走るのを覚える。", "本を読むために走る。"])
def test_clause_modifier_keeps_outer_relation_without_relabeling(parser, text):
    # GiNZAのadvclを主語等へ推測し直さず、節修飾として外側の組合せを検査する。
    analysis = parser.analyze(text)
    outer = next(
        row for row in analysis.relations if row.view and row.view.target_edge[2] == "advcl"
    )
    assert outer.queries[0].dependency == "advcl"
    inner = next(p for p in analysis.predicates if p.graph.root == outer.view.noun.root)
    assert inner.graph.root != outer.view.predicate.root
    assert any(row.predicate_root == inner.graph.root for row in analysis.relations)


def test_noun_root_external_case_role_does_not_remove_its_lexical_identity():
    # 「行う際」の際が別句へcase接続しても、このaclの修飾対象としてはNの語彙核である。
    doc = Doc(
        Vocab(),
        words=["行う", "際", "使う"],
        spaces=[False] * 3,
        heads=[1, 2, 2],
        deps=["acl", "case", "ROOT"],
        pos=["VERB", "NOUN", "VERB"],
        tags=["動詞-一般", "名詞-普通名詞-副詞可能", "動詞-一般"],
        lemmas=["行う", "際", "使う"],
    )
    doc.user_data["bunsetu_bi_labels"] = ["B"] * 3
    analysis = adapt_doc(doc, doc.text)
    relation = next(row for row in analysis.relations if row.view)
    assert relation.queries[0].noun == ("際",)
    assert relation.view.noun_case_nodes == ()
    assert analysis.restore_doc()[1].dep_ == "case"


def test_adverbial_lexical_predicate_keeps_both_occurrences_and_external_connection(parser):
    # 文脈上のVERBがadvmod修飾を担う場合も、活用形や語別の規則を作らず照合へ渡す。
    analysis = parser.analyze("彼は走ろうとする。")
    assert {tuple(n.lemma for n in p.query.predicate.nodes) for p in analysis.predicates} == {
        ("走る",),
        ("する",),
    }
    outer = next(row for row in analysis.relations if row.queries[0].dependency == "advmod")
    assert outer.predicate.text == "する"
    assert outer.argument.text == "走ろうと"
    assert outer.queries[0].noun == ()
    assert tuple(n.lemma for n in outer.queries[0].related_predicate.nodes) == ("走る",)
    assert outer.queries[0].related_attachment == 0
    assert outer.view.target_edge == (4, 2, "advmod")
    assert any(
        row.predicate.text == "走ろう" and row.argument is None for row in analysis.relations
    )


@pytest.mark.parametrize(
    "upos,xpos,expected",
    [
        ("VERB", "動詞-一般", True),
        ("ADJ", "形容詞-一般", True),
        ("ADV", "形状詞-一般", False),
        ("SCONJ", "動詞-一般", False),
    ],
)
def test_adverbial_role_uses_contextual_pos_and_does_not_promote_nominal_head(upos, xpos, expected):
    doc = Doc(
        Vocab(),
        words=["M", "N"],
        spaces=[False] * 2,
        heads=[1, 1],
        deps=["advmod", "ROOT"],
        pos=[upos, "NOUN"],
        tags=[xpos, "名詞-普通名詞-一般"],
        lemmas=["M", "N"],
    )
    doc.user_data["bunsetu_bi_labels"] = ["B", "B"]
    analysis = adapt_doc(doc, doc.text)
    assert bool(analysis.predicates) is expected
    if expected:
        assert len(analysis.predicates) == len(analysis.relations) == 1
        row = analysis.relations[0]
        assert row.queries[0].noun_role == "head" and row.queries[0].noun == ("N",)
        assert row.view.predicate.root == 0 and row.view.noun.root == 1


@pytest.mark.parametrize("word,upos", [("読もう", "NOUN"), ("寝よう", "PROPN")])
def test_inflected_lexical_occurrence_is_not_lost_under_nominal_upos(parser, word, upos):
    analysis = parser.analyze(f"彼は{word}とする。")
    doc = analysis.restore_doc()
    token = next(t for t in doc if t.text == word)
    assert token.pos_ == upos and token.dep_ == "obl"
    assert token.tag_ == "動詞-一般" and token.morph.get("Inflection")
    inner = next(p for p in analysis.predicates if token.i in p.graph.nodes)
    assert any(e.kind == "inflected_lexical_occurrence" for e in inner.evidence)
    outer = next(row for row in analysis.relations if row.queries[0].dependency == "obl")
    assert outer.queries[0].noun == ()
    assert outer.queries[0].related_predicate == inner.query.predicate
    assert outer.view.target_edge == (4, 2, "obl")
    assert analysis.restore_doc()[token.i].pos_ == upos


def test_inflected_word_with_unspecified_connection_is_checked_without_inventing_an_edge(parser):
    analysis = parser.analyze("彼は飛ぼうとする。")
    doc = analysis.restore_doc()
    token = next(t for t in doc if t.text == "飛ぼう")
    assert token.dep_ == "dep" and token.head.text == "彼"
    inner = next(row for row in analysis.relations if row.predicate.text == "飛ぼう")
    assert inner.argument is None and inner.queries[0].dependency is None
    assert not any(row.view and row.view.target_edge[1] == token.i for row in analysis.relations)


def test_non_nominal_adverbial_modifier_target_is_not_compared_as_a_noun():
    doc = Doc(
        Vocab(),
        words=["P", "A"],
        spaces=[False] * 2,
        heads=[1, 1],
        deps=["advmod", "ROOT"],
        pos=["VERB", "ADV"],
        tags=["動詞-一般", "副詞"],
        lemmas=["P", "A"],
    )
    doc.user_data["bunsetu_bi_labels"] = ["B", "B"]
    analysis = adapt_doc(doc, doc.text)
    assert analysis.relations[0].argument is None
    assert analysis.comparison.targets[0].scope == "non_nominal_modifier"
    assert analysis.usage_structure.analysis is analysis
    assert analysis.restore_doc()[0].dep_ == "advmod"


def test_parser_keeps_merged_vectors_and_prior_vocabulary_outside_current_document():
    instance = Parser()
    try:
        first = instance.analyze("1600rpmで回る。")
        earlier = first.restore_doc().vocab.get_vector("1600rpm").copy()
        later = instance.analyze("本を読む。")
        assert later.vocabulary.generation.parent is first.vocabulary.generation
        source = instance._require_nlp().vocab
        restored = later.restore_doc()
        assert restored.vocab.vectors.shape == source.vectors.shape
        assert restored.vocab.vectors.key2row == source.vectors.key2row
        assert np.array_equal(restored.vocab.vectors.data, source.vectors.data)
        assert tuple(restored.vocab.strings) == tuple(source.strings)
        assert restored.vocab["1600rpm"].has_vector
        assert np.array_equal(restored.vocab.get_vector("1600rpm"), earlier)
        restored.vocab.set_vector("1600rpm", np.zeros_like(earlier))
        assert np.array_equal(later.restore_doc().vocab.get_vector("1600rpm"), earlier)
    finally:
        instance.close()
