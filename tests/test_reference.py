"""実SQLiteの構築・固定群比較・安全な配布読込を確認する。"""

import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest
from test_examples import QUERY, lines, record
from test_support import vector_model

from native_japanese import reference
from native_japanese.examples import Examples, extract_records, group_key, query_key
from native_japanese.models import PredicateNode, Query, lexical_identity
from native_japanese.parser import Parser
from native_japanese.service import Inspector


@pytest.fixture
def setup_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(
        reference.resources, "load_fixed_vector", lambda assets: vector_model().model
    )
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(
            lines(
                record(),
                record(replace(QUERY, noun=("北/*",))),
                record(replace(QUERY, noun=("未収録/*",))),
            )
        )
    return source, output, tmp_path


def test_compile_indexed_and_generalization_without_archive(setup_reference, monkeypatch):
    source, output, assets = setup_reference
    result = reference.build_reference(source, output, assets)
    assert (result["usages"], result["groups"], result["comparable"]) == (3, 1, 2)
    assert result["bytes"] == output.stat().st_size
    assert result["membership_bytes"] > 0
    source.unlink()
    with reference.Reference(output, assets) as runtime:
        # 直接の索引陽性には、検査時にvector資源を開く必要がない。
        monkeypatch.setattr(
            reference.resources, "load_fixed_vector", lambda _: pytest.fail("不要load")
        )
        direct = runtime.assess(QUERY)
        assert direct.indexed and direct.score is None
        assert direct.evidence[0]["source"] == "compiled-reference"
        assert "observation_id" not in direct.evidence[0]
        monkeypatch.setattr(
            reference.resources, "load_fixed_vector", lambda _: vector_model().model
        )
        support = runtime.assess(replace(QUERY, noun=("西/*",)))
        assert not support.indexed and support.score == pytest.approx(-1 / np.sqrt(2))
        assert (support.comparable, support.total) == (2, 3)
        assert runtime.assess(replace(QUERY, noun=("零/*",))).score is None


def test_every_non_noun_condition_stays_fixed(setup_reference):
    # 索引は任意の異なるキーを混同しない。元格とN-P支持の対応は父の対照例で検証する。
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    with reference.Reference(output, assets) as runtime:
        for changed in (
            {
                "predicate": replace(
                    QUERY.predicate, nodes=(replace(QUERY.predicate.nodes[0], lemma="較べる"),)
                )
            },
            {
                "predicate": replace(
                    QUERY.predicate, nodes=(replace(QUERY.predicate.nodes[0], xpos="形容詞-*"),)
                )
            },
            {
                "predicate": replace(
                    QUERY.predicate,
                    nodes=(replace(QUERY.predicate.nodes[0], inflection=("母音動詞", "タ形")),),
                )
            },
            {"dependency": "PAS:ガ"},
            {"attachment": 1},
            {"noun_role": "head"},
        ):
            support = runtime.assess(replace(QUERY, noun=("西/*",), **changed))
            assert not support.indexed and support.score is None and support.total == 0


def test_role_queries_roundtrip_with_original_predicate_and_argument_pos(tmp_path, monkeypatch):
    parser = Parser()
    try:
        analyses = [
            parser.analyze(text) for text in ("既存の読込み経路を再利用する。", "基本形を返す。")
        ]
    finally:
        parser.close()
    queries = {
        query
        for analysis in analyses
        for row in analysis.relations
        if row.view is not None
        for query in row.queries
    }
    # 形態素の品詞と照会の接続点は、公開KNP上の同じIDを参照する。
    for analysis in analyses:
        document = analysis.restore_doc()
        for relation in analysis.relations:
            if relation.view is None:
                continue
            view, query = relation.view, relation.queries[0]
            morpheme = document.morphemes[view.predicate_attachment]
            node = query.predicate.nodes[query.attachment]
            assert node.xpos == f"{morpheme.pos}-{morpheme.subpos}"
            assert node.lemma == lexical_identity(morpheme)
    assert queries
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(
            lines(
                *(row for analysis in analyses for row in extract_records(analysis, "出典", "url"))
            )
        )
    with Examples(source) as examples:
        assert set(examples.iter_usages()) == queries
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    assert reference.build_reference(source, output, tmp_path)["usages"] == len(queries)
    with reference.Reference(output, tmp_path) as runtime:
        assert all(runtime.assess(query).indexed for query in queries)


def test_additions_change_mean_but_repeated_sources_do_not(setup_reference):
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    probe = replace(QUERY, noun=("西/*", "西/*"))
    with reference.Reference(output, assets) as runtime:
        before = runtime.assess(probe)
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(*(record(source=f"重複{i}") for i in range(20))))
    reference.build_reference(source, output, assets)
    with reference.Reference(output, assets) as runtime:
        assert runtime.assess(probe) == before
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(record(replace(QUERY, noun=("西/*",)))))
    reference.build_reference(source, output, assets)
    with reference.Reference(output, assets) as runtime:
        after = runtime.assess(probe)
        assert after.score == pytest.approx(0, abs=1e-14)
        assert (after.comparable, after.total) == (3, 4)
        assert runtime.assess(replace(QUERY, noun=("西/*",))).indexed


def test_empty_and_zero_mean_add_no_generalization_support(tmp_path, monkeypatch):
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True):
        pass
    assert reference.build_reference(source, output, tmp_path)["usages"] == 0
    with reference.Reference(output, tmp_path) as runtime:
        assert runtime.assess(QUERY).score is None
        assert runtime.identity["groups"] == 0
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(record(), record(replace(QUERY, noun=("西/*",)))))
    reference.build_reference(source, output, tmp_path)
    with reference.Reference(output, tmp_path) as runtime:
        assert runtime.assess(replace(QUERY, noun=("北/*",))).score is None


def test_group_without_vectors_keeps_indexed_usage_but_adds_no_score(tmp_path, monkeypatch):
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    usage = replace(QUERY, noun=("未収録/*",))
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(record(usage)))
    reference.build_reference(source, output, tmp_path)
    with reference.Reference(output, tmp_path) as runtime:
        assert runtime.assess(usage).indexed
        support = runtime.assess(QUERY)
        assert not support.indexed and support.score is None
        assert (support.comparable, support.total) == (0, 1)


def test_failed_build_preserves_prior_output_and_never_overwrites_archive(
    setup_reference, monkeypatch
):
    source, output, assets = setup_reference
    with pytest.raises(ValueError, match="上書き"):
        reference.build_reference(source, source, assets)
    reference.build_reference(source, output, assets)
    previous = output.read_bytes()
    monkeypatch.setattr(
        reference.resources, "load_fixed_vector", lambda _: (_ for _ in ()).throw(OSError("失敗"))
    )
    with pytest.raises(OSError, match="失敗"):
        reference.build_reference(source, output, assets)
    assert output.read_bytes() == previous
    assert not list(output.parent.glob("*.part"))
    with Examples(source) as examples:
        assert len(list(examples.iter_usages())) == 3


@pytest.mark.parametrize(
    "damage",
    [
        "identity",
        "nan",
        "dimension",
        "counts",
        "missing",
        "bloom",
        "old_version",
        "missing_mean",
        "invalid_group",
        "capacity",
    ],
)
def test_corrupt_reference_is_rejected(setup_reference, damage):
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    with sqlite3.connect(output) as db:
        if damage == "identity":
            identity = json.loads(
                db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]
            )
            identity["normalization_identity"] = "別契約"
            db.execute("UPDATE metadata SET value=? WHERE key='identity'", (json.dumps(identity),))
        elif damage == "nan":
            db.execute("UPDATE groups SET mean=?", (np.full(300, np.nan).astype("<f8").tobytes(),))
        elif damage == "dimension":
            db.execute("UPDATE groups SET mean=?", (b"short",))
        elif damage == "counts":
            db.execute("UPDATE groups SET comparable=total+1")
        elif damage == "missing":
            db.execute("DELETE FROM groups WHERE key=?", (group_key(QUERY),))
        elif damage == "invalid_group":
            key = json.loads(group_key(QUERY))
            key[1] = []
            db.execute("UPDATE groups SET key=?", (reference.canonical(key),))
        elif damage == "old_version":
            db.execute("PRAGMA user_version=1")
        elif damage == "missing_mean":
            db.execute("UPDATE groups SET mean=NULL")
        elif damage == "capacity":
            builder = reference.membership.Builder(1)
            builder.add(query_key(QUERY))
            raw, manifest = builder.finish()
            db.execute("UPDATE membership SET payload=?", (raw,))
            db.execute(
                "UPDATE metadata SET value=? WHERE key='membership'",
                (reference.canonical(manifest),),
            )
        else:
            db.execute("UPDATE membership SET payload=zeroblob(length(payload))")
    with pytest.raises(ValueError):
        reference.Reference(output, assets)


def test_whitespace_is_not_lexical_and_native_attributes_are_preserved(tmp_path, monkeypatch):
    text = "重複を畳む。\n重複を畳む。"
    parser = Parser()
    try:
        analysis = parser.analyze(text)
        plain = parser.analyze(text.splitlines()[0])
    finally:
        parser.close()
    relations = [r for r in analysis.relations if r.view is not None]
    queries = [relation.queries[0] for relation in relations]
    plain_query = next(r.queries[0] for r in plain.relations if r.view is not None)
    assert len(queries) == 2
    document = analysis.restore_doc()
    for relation, query in zip(relations, queries, strict=True):
        assert query.predicate == plain_query.predicate
        assert query.noun == plain_query.noun
        assert query.dependency == plain_query.dependency
        morpheme = document.morphemes[relation.view.predicate_attachment]
        node = query.predicate.nodes[query.attachment]
        assert node.xpos == f"{morpheme.pos}-{morpheme.subpos}"
        assert not any(lemma.isspace() for lemma in query.noun)
    usage_count = len({query_key(query) for query in queries})
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(
            lines(*extract_records(analysis, "改行を含む出典", "https://example.test"))
        )
        assert set(examples.iter_usages()) == set(queries)
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    assert reference.build_reference(source, output, tmp_path)["usages"] == usage_count
    with reference.Reference(output, tmp_path) as runtime:
        assert all(runtime.assess(query).indexed for query in [plain_query, *queries])


def test_bloom_false_positive_cannot_bypass_exact_predicate_or_relation(
    setup_reference, monkeypatch
):
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    with reference.Reference(output, assets) as runtime:

        def unexpected(*args):
            pytest.fail("述語・接続先・関係・方向の厳密な確認より先にBloomを呼びました。")

        monkeypatch.setattr(reference.membership, "contains", unexpected)
        changed_predicate = replace(
            QUERY.predicate,
            nodes=(
                *QUERY.predicate.nodes,
                PredicateNode("せる", "助動詞-*", ("母音動詞", "基本形"), None, None),
            ),
        )
        for query in (
            replace(QUERY, predicate=changed_predicate),
            replace(QUERY, attachment=1),
            replace(QUERY, dependency="PAS:ガ"),
            replace(QUERY, noun_role="head"),
        ):
            support = runtime.assess(query)
            assert not support.indexed and support.score is None
        monkeypatch.setattr(reference.membership, "contains", lambda *args: True)
        assert runtime.assess(replace(QUERY, noun=("誤陽性/*",))).indexed


def test_bare_predicate_uses_exact_composition_without_noun_similarity(tmp_path, monkeypatch):
    parser = Parser()
    try:
        plain, causative = [parser.analyze(text) for text in ("畳む。", "畳ませる。")]
    finally:
        parser.close()
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(*extract_records(plain, "出典", "url")))
        assert not list(examples.iter_usages())
        assert len(list(examples.iter_predicates())) == 1
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    result = reference.build_reference(source, output, tmp_path)
    assert result["predicates"] == 1 and result["usages"] == 0
    with reference.Reference(output, tmp_path) as runtime:
        monkeypatch.setattr(
            reference.membership, "contains", lambda *args: pytest.fail("P0にBloomは不要")
        )
        assert runtime.assess(plain.relations[0].queries[0]).indexed
        support = runtime.assess(causative.relations[0].queries[0])
        assert not support.indexed and support.score is None


@pytest.fixture(scope="module")
def usage_parser():
    parser = Parser()
    analyze = parser.analyze
    snapshots = {}

    def once(text):
        # 同じ原文の実解析は一度にし、各検証へ独立した不変snapshotを渡す。
        if text not in snapshots:
            snapshots[text] = analyze(text)
        return replace(snapshots[text])

    parser.analyze = once
    yield parser
    parser.close()


@pytest.fixture
def compile_usages(tmp_path, monkeypatch, usage_parser):
    """構文と実際の共起は実解析・正本で確認し、名詞近似だけを決定的にする。"""
    from gensim.models import KeyedVectors

    from native_japanese import service

    # 文書解析は実モデルで行い、検査APIも同じ常駐プロセッサを借りる。
    # モデルの読込・終了契約はParserの試験で確認する。
    monkeypatch.setattr(service, "Parser", lambda: usage_parser)
    monkeypatch.setattr(usage_parser, "close", lambda: None)
    count = 0

    def build(texts, *, probes=(), directions=None):
        nonlocal count
        count += 1
        analyses = [usage_parser.analyze(text) for text in (*texts, *probes)]
        words = sorted(
            {
                lexical_identity(morpheme).rsplit("/", 1)[0]
                for a in analyses
                for morpheme in a.restore_doc().morphemes
                if morpheme.pos != "特殊"
            }
        )
        model = KeyedVectors(vector_size=300)
        values = np.zeros((len(words), 300), dtype=np.float32)
        for i, word in enumerate(words):
            values[i, (directions or {}).get(word, 0)] = 1
        model.add_vectors(words, values)
        monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: model)
        source, output = tmp_path / f"正本{count}.sqlite", tmp_path / f"配布{count}.sqlite"
        with Examples(source, writable=True) as examples:
            examples.import_jsonl(
                lines(
                    *(
                        record
                        for a in analyses[: len(texts)]
                        for record in extract_records(a, "出典", "url")
                    )
                )
            )
        reference.build_reference(source, output, tmp_path)
        return output

    return build


@pytest.mark.parametrize("verb", ["走る", "泳ぐ"])
def test_outer_support_cannot_replace_missing_inner_predicate(tmp_path, compile_usages, verb):
    # 本が好きだだけでは内側Pの直接用例がない。N類似を最大にしても独立Pは未支持。
    text = f"{verb}のが好きだ。"
    output = compile_usages(("本が好きだ。",), probes=(text,))
    with Inspector(output, tmp_path) as inspector:
        result = inspector.lint(text)
    inner = next(row for row in result.relations if row.relation.predicate.text == verb)
    outer = next(row for row in result.relations if row.relation.argument is not None)
    assert outer.status == "supported"
    assert inner.status == "review" and inner.score is None
    assert inner.relation.predicate.ranges == ((0, len(verb)),)
    assert any(d.range == (0, len(verb)) for d in result.diagnostics)


@pytest.mark.parametrize("text", ["走るのが好きだ。", "走るのを覚える。"])
def test_separate_predicate_examples_do_not_skip_outer_combination(tmp_path, compile_usages, text):
    outer_word = "好きだ。" if "好き" in text else "覚える。"
    output = compile_usages(("走る犬。", outer_word), probes=(text,))
    with Inspector(output, tmp_path) as inspector:
        result = inspector.lint(text)
    inner = next(row for row in result.relations if row.relation.predicate.text == "走る")
    outer = next(row for row in result.relations if row.relation.argument is not None)
    assert inner.status == "indexed"
    assert outer.status == "review" and outer.score is None
    assert outer.queries[0].support.total == 0
    assert outer.relation.argument.text == ("走るのが" if "好き" in text else "走るのを")


def test_unrelated_predicate_cannot_supply_noun_examples_to_target_group(tmp_path, compile_usages):
    # 対象Pの本(軸0)と入力の走るの(軸1)を固定し、別Pに同じNを追加する。
    text = "走るのが好きだ。"
    sources = ("本が好きだ。", "走る犬。")
    directions = {"走る": 1, "の": 1}
    first = compile_usages(sources, probes=(text,), directions=directions)
    with Inspector(first, tmp_path) as inspector:
        before = inspector.lint(text)
    second = compile_usages((*sources, "走るのが嫌いだ。"), probes=(text,), directions=directions)
    with Inspector(second, tmp_path) as inspector:
        after = inspector.lint(text)
        unrelated = inspector.lint("走るのが嫌いだ。")
    groups = [
        next(r for r in result.relations if r.relation.predicate.text == "好きだ")
        for result in (before, after)
    ]
    assert groups[0].status == groups[1].status == "review"
    assert groups[0].queries[0].support == groups[1].queries[0].support
    assert groups[0].queries[0].support.total == 1
    other = next(row for row in unrelated.relations if row.relation.argument is not None)
    target, added = groups[1].queries[0].query, other.queries[0].query
    assert added.predicate != target.predicate
    assert replace(added, predicate=target.predicate) == target


@pytest.mark.parametrize(
    "source,probe",
    [
        ("彼は学生だ。", "彼は医者だ。"),
        ("空が青い。", "空が赤い。"),
        ("花がきれいだ。", "花が静かだ。"),
        ("彼は勉強する。", "彼は運動する。"),
        ("母が布を畳む。", "母が布を畳ませる。"),
    ],
)
def test_predicate_types_require_their_own_direct_construction(
    tmp_path, compile_usages, monkeypatch, source, probe
):
    output = compile_usages((source,), probes=(probe,))
    with Inspector(output, tmp_path) as inspector:
        assert not inspector.lint(source).diagnostics
        # Bloom陽性もN類似も、構成の異なるPそのものの不存在を補わない。
        monkeypatch.setattr(reference.membership, "contains", lambda *args: True)
        result = inspector.lint(probe)
    assert result.diagnostics
    assert all(row.status == "review" and row.score is None for row in result.relations)


def test_case_difference_does_not_change_lexical_pair_support(tmp_path, compile_usages):
    # Issue #19のユーザー要件: 同じ「父」と「似る」の組合せを両入力で支持する。
    # 元の格・関係・表出型は保持し、N-Pの語彙照会と分けて検証する。
    output = compile_usages(("父に似る。",), probes=("父から似る。",))
    with Inspector(output, tmp_path) as inspector:
        results = [inspector.lint(text) for text in ("父に似る。", "父から似る。")]
    queries = []
    for result, native_role in zip(
        results, (("PAS:ニ", "C"), ("基本句:D", "dependent")), strict=True
    ):
        assert not result.diagnostics
        row = result.relations[0]
        assert row.status == "indexed"
        relation = row.relation
        query = relation.queries[0]
        queries.append(query)
        assert (query.dependency, query.noun_role) == ("N-P", "noun")
        target = next(
            target
            for target in result.analysis.comparison.targets
            if target.target_edge == relation.view.target_edge
        )
        assert (target.dependency, target.noun_role) == native_role
        assert relation.view.target_edge[2] == native_role[0]
        assert relation.view.predicate_attachment == target.predicate_attachment
        assert query.attachment == query.predicate.root
    assert queries[0] == queries[1]


@pytest.mark.parametrize("damage", ["inner", "outer", "empty", "attachment", "role", "noun"])
def test_recognized_targets_cannot_disappear_or_query_other_targets(
    tmp_path, compile_usages, monkeypatch, damage
):
    # 実Parser→正本→配布参照→Inspector。認識後の破損は用例不足と区別して停止する。
    text = "走るのが好きだ。"
    output = compile_usages((text,))
    with Inspector(output, tmp_path) as inspector:
        analysis = inspector.analyze(text)
        comparison = analysis.comparison
        outer = next(row for row in comparison.relations if row.view)
        inner = next(row for row in comparison.relations if row.view is None)
        if damage == "inner":
            rows = (outer,)
        elif damage == "outer":
            rows = (inner,)
        else:
            changed = {"attachment": 1, "role": "obj", "noun": ("本",)}
            query = outer.queries[0]
            if damage == "attachment":
                query = replace(query, attachment=changed[damage])
            elif damage == "role":
                query = replace(query, dependency=changed[damage])
            elif damage == "noun":
                query = replace(query, noun=changed[damage])
            rows = (replace(outer, queries=() if damage == "empty" else (query,)), inner)
        analysis.__dict__["comparison"] = replace(comparison, relations=rows)
        monkeypatch.setattr(inspector, "analyze", lambda _: analysis)
        with pytest.raises(ValueError, match="照会|一致"):
            inspector.lint(text)


def test_input_without_recognized_targets_needs_no_review(tmp_path, compile_usages):
    output = compile_usages(("走るのが好きだ。",))
    with Inspector(output, tmp_path) as inspector:
        for text in ("", " \n"):
            result = inspector.lint(text)
            assert not result.relations and not result.diagnostics


def test_native_nominal_predicate_and_pas_are_retained_without_linguistic_gold(usage_parser):
    # 共通の解析契約: 用言/体言というネイティブ認定を保持して同じAnalysisから参照する。
    # 名詞句の言語的な正誤や、述語を含まないというgoldはこの入力へ付与しない。
    analysis = usage_parser.analyze("処理の再利用")
    document = analysis.restore_doc()
    bp = next(bp for bp in document.base_phrases if bp.head.text == "利用")
    assert bp.features["用言"] == "判" and "体言" in bp.features
    argument = bp.pas.get_arguments("ヲ", relax=False)[0]
    assert argument.type.value == "N" and argument.base_phrase.head.text == "処理"
    row = next(r for r in analysis.relations if r.predicate_root == bp.head.global_index)
    query = row.queries[0]
    assert query.predicate.nodes[query.predicate.root].lemma == lexical_identity(bp.head)
    assert (query.dependency, query.noun_role) == ("N-P", "noun")
    target = next(t for t in analysis.comparison.targets if t.target_edge == row.view.target_edge)
    assert (target.dependency, target.noun_role) == ("PAS:ヲ", "N")
    assert row.view.target_edge[2] == "PAS:ヲ"
    assert query.noun == (lexical_identity(argument.base_phrase.head),)
    assert row.view.predicate_attachment == bp.head.global_index
    assert row.view.noun.root == argument.base_phrase.head.global_index


@pytest.mark.parametrize("separate_inner", [False, True])
def test_auxiliary_construction_requires_its_own_direct_example(
    tmp_path, compile_usages, separate_inner
):
    # 同じ用言基本句の全構成を厳密に照合し、別構成の支持で代用しない。
    text = "彼は走ろうとする。"
    sources = ("彼は仕事をする。", "走ろう。") if separate_inner else ("彼は仕事をする。",)
    output = compile_usages(sources, probes=(text,))
    with Inspector(output, tmp_path) as inspector:
        result = inspector.lint(text)
    subject = next(
        row
        for row in result.relations
        if row.relation.view is not None
        and row.relation.view.target_edge[2] == "PAS:ガ"
        and tuple(key.rsplit("/", 1)[0] for key in row.relation.queries[0].noun) == ("彼",)
    )
    query = subject.relation.queries[0]
    assert tuple(node.lemma.rsplit("/", 1)[0] for node in query.predicate.nodes) == (
        "走る",
        "と",
        "する",
    )
    assert subject.relation.predicate.ranges == ((2, 8),)
    assert subject.status == "review" and subject.queries[0].support.total == 0
    direct = compile_usages((text,))
    with Inspector(direct, tmp_path) as inspector:
        direct_result = inspector.lint(text)
    assert not direct_result.diagnostics
    assert all(row.status == "indexed" for row in direct_result.relations)


def pair_analysis(*, inner="内P", form="意志推量形", auxiliary="補助", standalone=False):
    """公開KNPの構造対照。両Pの構成と基本句間依存を独立に固定する。"""
    from rhoknp import Document

    from native_japanese.parser import adapt_doc

    knp = [
        "# S-ID:pair kwja:2.6.0",
        "* -1D" if standalone else "* 1D",
        "+ -1D <用言:動>" if standalone else "+ 1D <用言:動>",
        f"{inner} {inner} {inner} 動詞 2 * 0 母音動詞 1 {form} 1 <基本句-主辞>",
        f"{auxiliary} {auxiliary} {auxiliary} 助動詞 5 * 0 * 0 * 0",
    ]
    if not standalone:
        knp += [
            "* -1D",
            "+ -1D <用言:動>",
            "外P 外P 外P 動詞 2 * 0 母音動詞 1 基本形 2 <基本句-主辞>",
            "機能 機能 機能 助動詞 5 * 0 * 0 * 0",
        ]
    document = Document.from_knp("\n".join([*knp, "EOS", ""]))
    return adapt_doc(document, document.text)


@pytest.mark.parametrize("changed", [{"inner": "別P"}, {"form": "連体形"}, {"auxiliary": "別補助"}])
@pytest.mark.parametrize("legacy", [False, True])
def test_predicate_pair_requires_joint_strict_compositions(tmp_path, monkeypatch, changed, legacy):
    source, probe = pair_analysis(), pair_analysis(**changed)
    source_query = next(r.queries[0] for r in source.relations if r.queries[0].related_predicate)
    query = next(r.queries[0] for r in probe.relations if r.queries[0].related_predicate)
    # P2以外は一致させ、別々に存在する二つのPを合成して通す実装でも検出する。
    assert query.related_predicate != source_query.related_predicate
    assert replace(query, related_predicate=source_query.related_predicate) == source_query
    archive, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(archive, writable=True) as examples:
        examples.import_jsonl(
            lines(
                record(),
                *(
                    row
                    for a in (source, pair_analysis(**changed, standalone=True))
                    for row in extract_records(a, "出典", "url")
                ),
            )
        )
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    result = reference.build_reference(archive, output, tmp_path)
    assert result["usages"] == 2
    with sqlite3.connect(output) as db:
        manifest = json.loads(
            db.execute("SELECT value FROM metadata WHERE key='membership'").fetchone()[0]
        )
        assert manifest["capacity"] == 1
        if legacy:
            # schema 3はN–PとP–Pの両方をBloom集合に登録する。
            builder = reference.membership.Builder(2)
            for usage in (QUERY, source_query):
                builder.add(query_key(usage))
            raw, manifest = builder.finish()
            identity = json.loads(
                db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]
            )
            identity["schema_version"] = 3
            db.execute("PRAGMA user_version=3")
            db.execute(
                "UPDATE metadata SET value=? WHERE key='identity'",
                (reference.canonical(identity),),
            )
            db.execute(
                "UPDATE metadata SET value=? WHERE key='membership'",
                (reference.canonical(manifest),),
            )
            db.execute("UPDATE membership SET payload=?", (raw,))
    with reference.Reference(output, tmp_path) as runtime:
        assert runtime.assess(QUERY).indexed
        monkeypatch.setattr(reference.membership, "contains", lambda *args: True)
        monkeypatch.setattr(
            reference.resources,
            "load_fixed_vector",
            lambda _: pytest.fail("P–Pに名詞ベクトルを使いました。"),
        )
        assert runtime.assess(Query(query.related_predicate)).indexed
        assert runtime.assess(Query(query.predicate)).indexed
        unsupported = runtime.assess(query)
        assert not unsupported.indexed and unsupported.score is None
        assert runtime.assess(source_query).indexed
        for altered in (
            replace(source_query, attachment=1),
            replace(source_query, related_attachment=1),
        ):
            assert not runtime.assess(altered).indexed


@pytest.mark.parametrize(
    "text,predicate,pair",
    [
        ("走るのを覚える。", "覚える", False),
        ("読むのは楽しい。", "楽しい", False),
        ("彼が走ると考える。", "考える", True),
        ("走るので疲れる。", "疲れる", True),
        ("走るのではないと考える。", "考える", True),
        ("走るのが好きだと考える。", "考える", True),
    ],
)
def test_nominalization_belongs_to_the_current_expression_only(usage_parser, text, predicate, pair):
    # 深い子節の名詞化と、用言全体の名詞的な役割を区別する。
    analysis = usage_parser.analyze(text)
    row = next(
        r for r in analysis.relations if r.view is not None and r.predicate.text == predicate
    )
    query = row.queries[0]
    assert (query.related_predicate is not None) is pair
    assert bool(query.noun) is not pair
    assert bool(row.view.noun_feature_nodes) is not pair


def test_purpose_expression_preserves_native_noun_view_and_inner_predicate(usage_parser):
    # 共通の解析契約: 元基本句/PASを参照する。離れた用言同士の辺は生成しない。
    # 名詞表現に含まれる内側Pも、全構成を保って独立に照合する。
    analysis = usage_parser.analyze("本を読むために走る。")
    document = analysis.restore_doc()
    read = next(bp for bp in document.base_phrases if bp.head.text == "読む")
    purpose = next(bp for bp in document.base_phrases if bp.head.text == "ため")
    run = next(bp for bp in document.base_phrases if bp.head.text == "走る")
    assert read.parent == purpose and purpose.parent == run
    assert "体言" in purpose.features and "節-機能-目的" in purpose.features
    reading = next(p for p in analysis.predicates if p.graph.root == read.head.global_index)
    assert tuple(node.lemma for node in reading.query.predicate.nodes) == tuple(
        lexical_identity(m) for m in read.morphemes if m.pos != "特殊"
    )
    assert reading.query.predicate.root == reading.graph.nodes.index(read.head.global_index)
    outer = next(
        r
        for r in analysis.relations
        if r.predicate_root == run.head.global_index
        and r.view is not None
        and r.view.target_edge[2] == "PAS:ニ"
    )
    query = outer.queries[0]
    assert (query.dependency, query.noun_role) == ("N-P", "noun")
    target = next(t for t in analysis.comparison.targets if t.target_edge == outer.view.target_edge)
    assert (target.dependency, target.noun_role) == ("PAS:ニ", "C")
    assert outer.view.predicate_attachment == target.predicate_attachment
    assert query.related_predicate is None and outer.view.related_predicate is None
    feature_morphemes = [document.morphemes[i] for i in outer.view.noun_feature_nodes]
    assert [m.text for m in feature_morphemes] == ["本", "読む", "ため"]
    assert query.noun == tuple(lexical_identity(m) for m in feature_morphemes)
    assert outer.view.noun.root == purpose.head.global_index
    assert any(
        row.predicate_root == reading.graph.root
        and row.queries[0].predicate == reading.query.predicate
        for row in analysis.relations
    )


def test_each_judge_receives_the_complete_independent_analysis(
    tmp_path, compile_usages, monkeypatch
):
    from native_japanese.parser import adapt_doc
    from native_japanese.service import Diagnostic, JudgeResult

    text = "説明にもっともらしさを与える。"
    output = compile_usages((text,))
    seen = []

    class Reader:
        def __init__(self, mutate):
            self.mutate = mutate

        def assess(self, analysis):
            document = analysis.restore_doc()
            assert document.to_knp().encode("utf-8") == original
            assert document.base_phrases[0].features["用法判定が使わない情報"] == "保持"
            assert any(morpheme.pos == "助詞" for morpheme in document.morphemes)
            seen.append(analysis)
            if self.mutate:
                document.morphemes[0].lemma = "局所変更"
                document.base_phrases[0].features["用法判定が使わない情報"] = "局所変更"
            return JudgeResult(diagnostics=(Diagnostic((0, 2), "別の判定機"),))

    with Inspector(output, tmp_path) as inspector:
        raw = inspector.analyze(text).restore_doc()
        raw.base_phrases[0].features["用法判定が使わない情報"] = "保持"
        original = raw.to_knp().encode("utf-8")
        analysis = adapt_doc(raw, text)
        monkeypatch.setattr(inspector.parser, "analyze", lambda _: analysis)
        inspector.judges = (Reader(True), *inspector.judges, Reader(False))
        result = inspector.lint(text)
    assert len(seen) == 2 and all(value is analysis for value in seen)
    assert result.analysis.snapshot == original
    assert len(result.relations) > 0
    assert [d.message for d in result.diagnostics] == ["別の判定機", "別の判定機"]
