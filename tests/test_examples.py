"""正本の完全一致・全出典保持・transactionと逐次列挙を確認する。"""

import json
from dataclasses import replace

import pytest

from native_japanese.examples import (
    Examples,
    extract_records,
    group_key,
    query_key,
    validate_record,
)
from native_japanese.models import Analysis, Endpoint, Query, Relation

QUERY = Query(("比べる",), "VERB", "obj", ("東",), "NOUN", "dependent")


def record(query=QUERY, source="出典A", observation="出現1"):
    analysis = Analysis(
        "東を比べる",
        (Relation(Endpoint("比べる", ((2, 5),)), Endpoint("東", ((0, 1),)), (query,)),),
    )
    return {
        **next(extract_records(analysis, source, "https://example.test")),
        "observation_id": observation,
    }


def lines(*records):
    return [json.dumps(row, ensure_ascii=False) for row in records]


def test_deduplication_keeps_all_sources_and_invalid_line_rolls_back(tmp_path):
    path = tmp_path / "正本.sqlite"
    with Examples(path, writable=True) as examples:
        result = examples.import_jsonl(lines(record(), record(source="出典B")))
        assert result == {"records": 2, "usages_added": 1, "observations_added": 2}
        assert examples.import_jsonl(lines(record()))["observations_added"] == 0
        assert len(examples.lookup(QUERY)) == 1
        assert examples.connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
        with pytest.raises(ValueError, match="3行目"):
            examples.import_jsonl(["\n", *lines(record(source="戻す")), "{}"])
        assert examples.connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
        extra = replace(QUERY, noun=("北",))
        with pytest.raises(ValueError):
            examples.import_jsonl([*lines(record(extra)), "{}"])
        assert not examples.lookup(extra)
    with Examples(path) as reopened:
        assert reopened.lookup(QUERY)
        with pytest.raises(ValueError, match="読み取り専用"):
            reopened.import_jsonl(lines(record()))


def test_complete_query_and_array_boundaries_are_preserved(tmp_path):
    queries = [
        QUERY,
        replace(QUERY, predicate=("比べる", "補助")),
        replace(QUERY, predicate_type="ADJ"),
        replace(QUERY, dependency="nsubj"),
        replace(QUERY, noun_type="PROPN"),
        replace(QUERY, noun_role="head"),
        replace(QUERY, noun=("a/+ b",)),
        replace(QUERY, noun=("a/", "+ b")),
    ]
    assert len({query_key(query) for query in queries}) == len(queries)
    with Examples(tmp_path / "正本.sqlite", writable=True) as examples:
        examples.import_jsonl(lines(*(record(q) for q in reversed(queries))))
        iterator = examples.iter_usages()
        assert iter(iterator) is iterator
        result = list(iterator)
        assert set(result) == set(queries)
        assert [group_key(q) for q in result] == sorted(group_key(q) for q in queries)
        assert all(examples.lookup(q) for q in queries)
        assert not examples.lookup(replace(QUERY, dependency="別ラベル"))


def test_extract_has_stable_occurrences_without_changing_lemmas():
    query = replace(QUERY, noun=("区切り/+", " 空白"))
    relation = Relation(Endpoint("比べる", ((5, 8),)), Endpoint("名詞", ((0, 2),)), (query, query))
    analysis = Analysis("名詞を  比べる", (relation,))
    first = list(extract_records(analysis, "出典", "url"))
    assert len(first) == 1
    assert first[0]["noun"] == list(query.noun)
    assert first == list(extract_records(analysis, "出典", "url"))
    moved = replace(
        analysis, relations=(replace(relation, predicate=Endpoint("比べる", ((6, 9),))),)
    )
    assert (
        first[0]["observation_id"] != next(extract_records(moved, "出典", "url"))["observation_id"]
    )
    for changed in (
        {"schema_version": 1},
        {"normalization_identity": "knp-standard-cv-head-v1"},
        {"noun": "再解析しない"},
        {"noun": []},
        {"noun": [""]},
        {"noun": [None]},
        {"noun_role": "推定"},
    ):
        with pytest.raises(ValueError):
            validate_record({**first[0], **changed})
