"""原文・KNP全情報・出典と、再構築できる比較索引を確認する。"""

import json
import sqlite3
from dataclasses import replace

import pytest
from rhoknp import Document

from native_japanese.examples import (
    SCHEMA_VERSION,
    Examples,
    analysis_from_record,
    extract_records,
    group_key,
    parse_predicate,
    parse_predicate_key,
    parse_query_key,
    predicate_key,
    query_key,
    validate_record,
)
from native_japanese.models import (
    NORMALIZATION_IDENTITY,
    PARSER_VERSIONS,
    Analysis,
    PredicateGraph,
    PredicateNode,
    Query,
)

PREDICATE = PredicateGraph(
    0, (PredicateNode("比べる/くらべる", "動詞-*", ("母音動詞", "基本形"), None, None),)
)
QUERY = Query(PREDICATE, 0, "N-P", ("東/*",), "noun")


def make_analysis(query=QUERY):
    # 公開KNP形式を入力とする交換契約の試験。日本語解析のgoldにはしない。
    if query.predicate != PREDICATE or query.dependency != "N-P" or query.noun_role != "noun":
        raise ValueError("このfixtureは比べるのヲ格だけを表します。")
    canonical_nouns = query.noun
    noun = tuple(key.rsplit("/", 1)[0] for key in canonical_nouns)
    assert noun
    knp = "\n".join(
        [
            "# S-ID:fixture kwja:2.6.0",
            "* 1D",
            "+ 1D <体言><係:ヲ格>",
            *[
                f'{word} {word} {word} 名詞 6 普通名詞 1 * 0 * 0 "代表表記:{canonical_nouns[index]}"'
                + (" <基本句-主辞>" if index == 0 else "")
                for index, word in enumerate(noun)
            ],
            "を を を 助詞 9 格助詞 1 * 0 * 0",
            "* -1D",
            f'+ -1D <用言:動><rel type="ヲ" target="{noun[0]}" sid="fixture" id="0"/>',
            '比べる くらべる 比べる 動詞 2 * 0 母音動詞 1 基本形 2 "代表表記:比べる/くらべる" <基本句-主辞>',
            "EOS",
            "",
        ]
    )
    doc = Document.from_knp(knp)
    offsets = []
    cursor = 0
    for morpheme in doc.morphemes:
        offsets.append((cursor, cursor + len(morpheme.text)))
        cursor += len(morpheme.text)
    return Analysis(doc.text, doc.to_knp().encode("utf-8"), tuple(offsets))


def record(query=QUERY, source="出典A", observation="出現1"):
    return {
        **next(extract_records(make_analysis(query), source, "https://example.test")),
        "observation_id": observation,
    }


def lines(*records):
    return [json.dumps(row, ensure_ascii=False) for row in records]


def test_deduplication_keeps_all_sources_and_invalid_line_rolls_back(tmp_path):
    path = tmp_path / "正本.sqlite"
    with Examples(path, writable=True) as examples:
        result = examples.import_jsonl(lines(record(), record(source="出典B")))
        assert result == {
            "records": 2,
            "documents_added": 1,
            "predicates_added": 1,
            "usages_added": 1,
            "observations_added": 2,
        }
        assert examples.import_jsonl(lines(record()))["observations_added"] == 0
        assert examples.lookup(QUERY)
        assert examples.connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
        extra = replace(QUERY, noun=("北/*",))
        with pytest.raises(ValueError, match="3行目"):
            examples.import_jsonl(["\n", *lines(record(extra, source="戻す")), "{}"])
        assert not examples.lookup(extra)
        exported = list(examples.iter_records())
        assert {row["source_id"] for row in exported} == {"出典A", "出典B"}
        assert all(analysis_from_record(row) == make_analysis() for row in exported)
    with Examples(path) as reopened:
        assert reopened.lookup(QUERY)
        with pytest.raises(ValueError, match="読み取り専用"):
            reopened.import_jsonl(lines(record()))


def test_graph_attachment_relation_and_array_boundaries_are_exact():
    # 交換形式の異なる値が衝突しないことを確認し、native格の共有可否のgoldにはしない。
    second = PredicateNode("せる", "助動詞-*", ("母音動詞", "基本形"), None, None)
    composed = replace(PREDICATE, nodes=(*PREDICATE.nodes, second))
    variants = [
        QUERY,
        replace(QUERY, predicate=composed),
        replace(QUERY, predicate=composed, attachment=1),
        replace(
            QUERY,
            predicate=replace(PREDICATE, nodes=(replace(PREDICATE.nodes[0], xpos="形容詞-*"),)),
        ),
        replace(
            QUERY,
            predicate=replace(
                PREDICATE, nodes=(replace(PREDICATE.nodes[0], inflection=("母音動詞", "タ形")),)
            ),
        ),
        replace(QUERY, dependency="PAS:ガ"),
        replace(QUERY, noun_role="N"),
        replace(QUERY, noun=("東/ひがし",)),
        replace(QUERY, noun=("東/あずま",)),
        replace(QUERY, noun=("a/+ b",)),
        replace(QUERY, noun=("a/", "+ b")),
        replace(QUERY, noun=(), related_predicate=PREDICATE, related_attachment=0),
        replace(QUERY, noun=(), related_predicate=composed, related_attachment=1),
    ]
    assert len({query_key(query) for query in variants}) == len(variants)
    for query in variants:
        assert parse_query_key(query_key(query)) == query
        assert parse_query_key(group_key(query), group=True) == replace(query, noun=())


def test_archive_roundtrip_preserves_complete_native_analysis_and_offsets(tmp_path):
    analysis = make_analysis()
    doc = analysis.restore_doc()
    doc.base_phrases[1].features["追加情報"] = "文書の全属性"
    doc.morphemes[-1].semantics["代表表記"] = "比べる/くらべる"
    doc.morphemes[-1].reading = "クラベル"
    original = replace(analysis, snapshot=doc.to_knp().encode("utf-8"))
    row = next(extract_records(original, "出典", "url"))
    assert row["knp"].encode("utf-8") == original.snapshot
    assert row["offsets"] == [list(span) for span in original.offsets]
    assert row == next(extract_records(original, "出典", "url"))
    with Examples(tmp_path / "正本.sqlite", writable=True) as examples:
        examples.import_jsonl(lines(row))
        restored = analysis_from_record(next(examples.iter_records()))
    assert restored == original
    assert restored.restore_doc().to_knp().encode("utf-8") == original.snapshot
    assert restored.comparison == original.comparison
    restored.restore_doc().morphemes[-1].reading = "別の読み"
    assert restored.snapshot == original.snapshot


@pytest.mark.parametrize(
    "changed",
    [
        {"schema_version": 4},
        {"schema_version": 5},
        {"normalization_identity": "kwja-base-char-word-native-v1"},
        {"parser_versions": {"kwja": "2.5.0", "rhoknp": "1.8.2"}},
        {"normalization_identity": "ginza-predicate-role-occurrence-v12"},
        {"knp": 123},
        {"knp": ""},
        {"source_id": " "},
        {"parser_versions": {}},
        {"noun": ["旧投影"]},
        {"offsets": [[False, 1]]},
        {"offsets": [[0, 999]]},
        {"offsets": [[0, 0]]},
    ],
)
def test_old_or_malformed_records_are_rejected(changed):
    with pytest.raises(ValueError):
        validate_record({**record(), **changed})


def test_mismatched_or_incomplete_document_rolls_back(tmp_path):
    row = record()
    with Examples(tmp_path / "正本.sqlite", writable=True) as examples:
        for changed in ({"text": "異なる原文"}, {"offsets": row["offsets"][:-1]}):
            with pytest.raises(ValueError, match="原文|位置"):
                examples.import_jsonl(lines({**row, **changed}))
            assert not list(examples.iter_records())


def test_native_analysis_differences_are_not_deduplicated(tmp_path):
    first = make_analysis()
    doc = first.restore_doc()
    doc.morphemes[-1].reading = "別の読み"
    second = replace(first, snapshot=doc.to_knp().encode("utf-8"))
    assert second.document_id != first.document_id
    with Examples(tmp_path / "正本.sqlite", writable=True) as examples:
        counts = examples.import_jsonl(
            lines(*(next(extract_records(a, "出典", "url")) for a in (first, second)))
        )
        assert counts["documents_added"] == 2
        restored = [analysis_from_record(row) for row in examples.iter_records()]
    assert {a.document_id for a in restored} == {first.document_id, second.document_id}
    assert {a.restore_doc().morphemes[-1].reading for a in restored} == {"くらべる", "別の読み"}


def test_corrupted_stored_offsets_are_rejected_on_export(tmp_path):
    path = tmp_path / "正本.sqlite"
    with Examples(path, writable=True) as examples:
        examples.import_jsonl(lines(record()))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE documents SET offsets='[]'")
    with Examples(path) as examples:
        with pytest.raises(ValueError, match="原文|位置"):
            list(examples.iter_records())


def test_native_prefix_base_phrase_edges_roundtrip_through_archive_and_reference(
    tmp_path, monkeypatch
):
    # 同文節の接頭辞基本句がPに係る公開辺を保持し、形態素間の辺へ変換しない。
    from test_support import vector_model

    from native_japanese import reference
    from native_japanese.parser import adapt_doc

    document = Document.from_knp(
        "# S-ID:prefix kwja:2.6.0\n"
        "* 1D\n"
        "+ 2D <体言>\n"
        '東 ひがし 東 名詞 6 普通名詞 1 * 0 * 0 "代表表記:東/*" <基本句-主辞>\n'
        "を を を 助詞 9 格助詞 1 * 0 * 0\n"
        "* -1D\n"
        "+ 2D\n"
        '再 さい 再 接頭辞 13 名詞接頭辞 1 * 0 * 0 "代表表記:再/さい" <基本句-主辞>\n'
        '+ -1D <用言:判><体言><rel type="ヲ" target="東" sid="prefix" id="0"/>\n'
        '利用 りよう 利用 名詞 6 サ変名詞 2 * 0 * 0 "代表表記:利用/りよう" <基本句-主辞>\n'
        "EOS\n"
    )
    analysis = adapt_doc(document, document.text)
    predicate = PredicateGraph(
        1,
        (
            PredicateNode("再/さい", "接頭辞-名詞接頭辞", ("*", "*"), 1, "基本句:D"),
            PredicateNode("利用/りよう", "名詞-サ変名詞", ("*", "*"), None, None),
        ),
    )
    assert analysis.predicates[0].query.predicate == predicate
    assert analysis.predicates[0].graph.edges == ((3, 2, "基本句:D"),)
    assert parse_predicate_key(predicate_key(predicate)) == predicate
    query = Query(predicate, 1, "N-P", ("東/*",), "noun")
    assert any(row.queries == (query,) for row in analysis.relations)
    archive, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(archive, writable=True) as examples:
        examples.import_jsonl(lines(*extract_records(analysis, "出典", "url")))
        assert tuple(examples.iter_predicates()) == (predicate,)
        assert tuple(examples.iter_usages()) == (query,)
        assert analysis_from_record(next(examples.iter_records())) == analysis
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    reference.build_reference(archive, output, tmp_path)
    with reference.Reference(output, tmp_path) as runtime:
        assert runtime.assess(Query(predicate)).indexed
        assert runtime.assess(query).indexed


@pytest.mark.parametrize(
    "nodes",
    [
        [["核", "名詞", [], None, None], ["接頭辞", "接頭辞", [], 1, "基本句:D"]],
        [["核", "名詞", [], None, None], ["接頭辞", "接頭辞", [], 9, "基本句:D"]],
        [["核", "名詞", [], None, None], ["接頭辞", "接頭辞", [], 0, "aux"]],
        [
            ["核", "名詞", [], None, None],
            ["前", "接頭辞", [], 2, "基本句:D"],
            ["後", "接頭辞", [], 1, "基本句:D"],
        ],
    ],
)
def test_malformed_or_cyclic_native_base_phrase_edges_are_rejected(nodes):
    with pytest.raises(ValueError):
        parse_predicate([0, nodes])


def test_archive_contract_is_committed_for_empty_database_and_preserved_on_roundtrip(tmp_path):
    path = tmp_path / "正本.sqlite"
    with Examples(path, writable=True):
        pass
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
        stored = dict(db.execute("SELECT key,value FROM metadata"))
        assert stored["normalization_identity"] == NORMALIZATION_IDENTITY
        assert json.loads(stored["parser_versions"]) == PARSER_VERSIONS
    with Examples(path, writable=True) as examples:
        assert not list(examples.iter_records())
        examples.import_jsonl(lines(record()))
    with Examples(path) as examples:
        exported = list(examples.iter_records())
    assert exported[0]["schema_version"] == 6
    assert exported[0]["normalization_identity"] == stored["normalization_identity"]
    assert exported[0]["parser_versions"] == json.loads(stored["parser_versions"])
    with Examples(tmp_path / "再取込.sqlite", writable=True) as examples:
        examples.import_jsonl(lines(*exported))
        assert list(examples.iter_records()) == exported


@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize(
    "damage",
    [
        "old_schema",
        "missing_table",
        "missing_identity",
        "old_identity",
        "wrong_versions",
        "malformed_versions",
        "extra_metadata",
    ],
)
def test_unknown_or_modified_archive_contract_is_rejected_without_relabeling_or_writing(
    tmp_path, writable, damage
):
    # schemaだけでは解析方針の同一性を保証できない。保存済み契約を持たない正本も拒否する。
    path = tmp_path / "正本.sqlite"
    with Examples(path, writable=True) as examples:
        examples.import_jsonl(lines(record()))
    with sqlite3.connect(path) as db:
        if damage == "old_schema":
            db.execute("PRAGMA user_version=5")
        elif damage == "missing_table":
            db.execute("DROP TABLE metadata")
        elif damage == "missing_identity":
            db.execute("DELETE FROM metadata WHERE key='normalization_identity'")
        elif damage == "old_identity":
            db.execute(
                "UPDATE metadata SET value=? WHERE key='normalization_identity'",
                ("kwja-base-char-word-native-v1",),
            )
        elif damage == "wrong_versions":
            db.execute(
                "UPDATE metadata SET value=? WHERE key='parser_versions'",
                (json.dumps({**PARSER_VERSIONS, "kwja": "2.5.0"}),),
            )
        elif damage == "malformed_versions":
            db.execute("UPDATE metadata SET value='{' WHERE key='parser_versions'")
        else:
            db.execute("INSERT INTO metadata VALUES ('unknown','value')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="解析契約|種類または版"):
        with Examples(path, writable=writable) as examples:
            pytest.fail("不明な契約の正本を開きました。")
    assert path.read_bytes() == before


def test_exported_record_cannot_mutate_the_fixed_parser_contract():
    row = record()
    expected = dict(PARSER_VERSIONS)
    row["parser_versions"]["kwja"] = "2.5.0"
    assert PARSER_VERSIONS == expected
    with pytest.raises(ValueError, match="解析契約"):
        validate_record(row)
