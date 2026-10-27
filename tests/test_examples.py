"""原文・完全Doc・全出典と、再構築できる比較索引を確認する。"""

import base64
import json
from dataclasses import replace

import pytest
import spacy
from spacy.tokens import Doc

from native_japanese.examples import (
    Examples,
    analysis_from_record,
    extract_records,
    group_key,
    parse_query_key,
    query_key,
    validate_record,
)
from native_japanese.models import PredicateGraph, PredicateNode, Query
from native_japanese.parser import adapt_doc

PREDICATE = PredicateGraph(0, (PredicateNode("比べる", "動詞-一般", (), None, None),))
QUERY = Query(PREDICATE, 0, "obj", ("東",), "dependent")


def make_analysis(query=QUERY):
    # 形式変換の対照となる公開Doc。日本語解析のgoldは実モデルの試験で扱う。
    nodes = query.predicate.nodes
    size = len(nodes)
    words = [n.lemma for n in nodes] + list(query.noun)
    heads = [query.predicate.root if n.head is None else n.head for n in nodes]
    deps = ["ROOT" if n.dependency is None else n.dependency for n in nodes]
    heads += [query.attachment if i == 0 else size for i in range(len(query.noun))]
    deps += [query.dependency if i == 0 else "compound" for i in range(len(query.noun))]
    if query.noun_role == "head":
        heads[query.predicate.root] = size
        deps[query.predicate.root] = query.dependency
        heads[size], deps[size] = size, "ROOT"
    doc = Doc(
        spacy.blank("ja").vocab,
        words=words,
        spaces=[False] * len(words),
        heads=heads,
        deps=deps,
        lemmas=words,
        pos=["VERB"] * size + ["NOUN"] * len(query.noun),
        tags=[n.xpos for n in nodes] + ["名詞-普通名詞-一般"] * len(query.noun),
        morphs=[{"Inflection": n.inflection} if n.inflection else {} for n in nodes]
        + [{}] * len(query.noun),
    )
    doc.user_data["bunsetu_bi_labels"] = ["B"] * len(doc)
    return adapt_doc(doc, doc.text)


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
        extra = replace(QUERY, noun=("北",))
        with pytest.raises(ValueError, match="3行目"):
            examples.import_jsonl(["\n", *lines(record(extra, source="戻す")), "{}"])
        assert not examples.lookup(extra)
        exported = list(examples.iter_records())
        assert {row["source_id"] for row in exported} == {"出典A", "出典B"}
        assert all(
            analysis_from_record(row).snapshot == make_analysis().snapshot for row in exported
        )
    with Examples(path) as reopened:
        assert reopened.lookup(QUERY)
        with pytest.raises(ValueError, match="読み取り専用"):
            reopened.import_jsonl(lines(record()))


def test_graph_attachment_relation_and_array_boundaries_are_exact():
    second = PredicateNode("補助", "助動詞", (), 0, "aux")
    composed = replace(PREDICATE, nodes=(*PREDICATE.nodes, second))
    variants = [
        QUERY,
        replace(QUERY, predicate=composed),
        replace(QUERY, predicate=composed, attachment=1),
        replace(
            QUERY,
            predicate=replace(PREDICATE, nodes=(replace(PREDICATE.nodes[0], xpos="形容詞-一般"),)),
        ),
        replace(
            QUERY,
            predicate=replace(
                PREDICATE, nodes=(replace(PREDICATE.nodes[0], inflection=("連体形",)),)
            ),
        ),
        replace(QUERY, dependency="nsubj"),
        replace(QUERY, noun_role="head"),
        replace(QUERY, noun=("a/+ b",)),
        replace(QUERY, noun=("a/", "+ b")),
    ]
    assert len({query_key(query) for query in variants}) == len(variants)
    for query in variants:
        assert parse_query_key(query_key(query)) == query
        assert parse_query_key(group_key(query), group=True) == replace(query, noun=())


def test_archive_roundtrip_preserves_all_token_attributes_and_user_data(tmp_path):
    analysis = make_analysis()
    doc = analysis.restore_doc()
    doc.user_data["extra"] = {"全属性": [1, 2, 3]}
    doc[0].set_morph("Reading=クラベル|Inflection=下一段;終止形-一般")
    original = adapt_doc(doc, doc.text)
    row = next(extract_records(original, "出典", "url"))
    assert base64.b64decode(row["doc_base64"]) == original.snapshot
    assert row == next(extract_records(original, "出典", "url"))
    with Examples(tmp_path / "正本.sqlite", writable=True) as examples:
        examples.import_jsonl(lines(row))
        restored = analysis_from_record(next(examples.iter_records()))
    assert restored == original
    assert restored.restore_doc().to_bytes() == original.snapshot
    assert restored.comparison == original.comparison


@pytest.mark.parametrize(
    "changed",
    [
        {"schema_version": 2},
        {"normalization_identity": "ginza-bunsetu-functional-projection-v10"},
        {"doc_base64": "!"},
        {"doc_base64": ""},
        {"source_id": " "},
        {"parser_versions": {}},
        {"noun": ["旧投影"]},
    ],
)
def test_old_or_malformed_records_are_rejected(changed):
    with pytest.raises(ValueError):
        validate_record({**record(), **changed})


def test_mismatched_or_incomplete_document_rolls_back(tmp_path):
    row = record()
    with Examples(tmp_path / "正本.sqlite", writable=True) as examples:
        with pytest.raises(ValueError, match="原文"):
            examples.import_jsonl(lines({**row, "text": "異なる原文"}))
        assert not list(examples.iter_records())
