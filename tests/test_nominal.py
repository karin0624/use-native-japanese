"""静的名詞候補の対応・欠損状態・打切りと、索引の直接支持の分離を点検する。"""

import hashlib
import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from native_japanese import nominal
from native_japanese.nominal import NominalFingerprint, NominalGeneralizer
from native_japanese.structure import (
    DependencyEdge,
    Document,
    FragmentShape,
    ParseMetadata,
    Sentence,
    Token,
)
from native_japanese.usage_index import UsageCandidate, UsageIndex, UsageIndexBuilder

SHAPE = FragmentShape(("NOUN", "VERB"), (DependencyEdge(1, 0, "obj"),))
WORDS = ("入力", "固定")


@pytest.fixture
def generalizer(monkeypatch):
    # 語義や正誤の正解を置かず、順位と取得状態だけを既知vectorで確かめる。
    vectors = {
        "入力": [1, 0],
        "甲": [1, 0],
        "乙": [1, 0],
        "戊": [1, 0],
        "己": [1, 0],
        "丙": [0, 1],
        "丁": [-1, 0],
        "未登録": [0.6, 0.8],
        "ゼロ": [0, 0],
        "非有限": [np.nan, 1],
        "異次元": [1],
        "": [0, 0],
    }

    class Model:
        key_to_index = {word: i for i, word in enumerate(vectors) if word != "未登録"}

        def get_vector(self, word):
            return vectors[word]

    fingerprint = NominalFingerprint(nominal.MODEL_SHA256, nominal.DEPENDENCIES, 2)
    monkeypatch.setattr(nominal, "_load_fixed_model", lambda _: (Model(), fingerprint))
    return NominalGeneralizer("未読込fixture")


@pytest.fixture
def index(tmp_path):
    metadata = ParseMetadata("fixture", "1", "公開値", "1", "1", "辞書", "1", "1")
    source_sha = hashlib.sha256(b"nominal-fixture").hexdigest()
    # 甲の同じ記事の2出現と、別記事の1出現を区別する。
    rows = [("入力", "A"), ("甲", "A"), ("甲", "A"), ("甲", "B")]
    rows += [(word, "C") for word in ("乙", "丙", "丁", "ゼロ", "未登録")]
    with UsageIndexBuilder(
        tmp_path,
        metadata=metadata,
        source_identity="名詞候補fixture",
        source_sha256=source_sha,
        expected_documents=len(rows),
        max_fragments_per_sentence=10,
    ) as build:
        for i, (word, article) in enumerate(rows):
            text = f"{word} 固定"
            tokens = (
                Token(0, 0, len(word), word, word, "NOUN", "", "", 1, "obj"),
                Token(1, len(word) + 1, len(text), "固定", "固定", "VERB", "", "", 1, "ROOT"),
            )
            doc = Document(str(i), text, tokens, (Sentence(0, 0, 2, 0, len(text)),), metadata)
            build.add_document(doc, article_id=article)
        build.finish(input_complete=True)
    with UsageIndex(
        tmp_path,
        expected_metadata=metadata,
        source_identity="名詞候補fixture",
        source_sha256=source_sha,
    ) as loaded:
        yield loaded


def test_top_three_keep_counts_and_keys_without_reading_citations(generalizer, index):
    direct = index.lookup(SHAPE, WORDS, target_slot=0)
    index._sources.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
    result = generalizer.generalize(index, SHAPE, WORDS, target_slot=0, batch_size=2)
    index._sources.set_authorizer(None)
    # 同点は記事数降順、lemma順。元名詞を混ぜず、batchをまたいで上位3を取る。
    assert [item.noun for item in result.top_candidates] == ["甲", "乙", "未登録"]
    assert [item.cosine for item in result.top_candidates] == pytest.approx([1, 1, 0.6])
    assert (result.status, result.candidate_count, result.evaluated_candidate_count) == (
        "available",
        6,
        6,
    )
    assert (result.valid_vector_count, result.unavailable_vector_count) == (5, 1)
    candidate = result.top_candidates[0]
    assert (candidate.lemmas, candidate.count, candidate.articles) == (("甲", "固定"), 3, 2)
    assert result.top_candidates[2].vector_status == "subword_generated"
    citation = index.citation(SHAPE, candidate.lemmas)
    assert citation.surfaces == ("甲", "固定")
    assert index.lookup(SHAPE, WORDS, target_slot=0) == direct
    assert (direct.c, direct.c_articles) == (1, 1)


@pytest.mark.parametrize("batch_size", [2, 4])
def test_cosine_ties_keep_most_articles_within_and_across_batches(generalizer, batch_size):
    class TiedIndex:
        def candidates(self, *_args, **_kwargs):
            return iter(
                UsageCandidate(word, (word, "固定"), articles, articles)
                for word, articles in (("乙", 1), ("戊", 1), ("己", 1), ("甲", 9))
            )

    result = generalizer.generalize(TiedIndex(), SHAPE, WORDS, target_slot=0, batch_size=batch_size)
    # 同cosineが4候補あっても、lemma順で最後の記事支持最大候補を落とさない。
    assert [item.noun for item in result.top_candidates] == ["甲", "乙", "己"]
    assert [item.articles for item in result.top_candidates] == [9, 1, 1]


@pytest.mark.parametrize("word", ["ゼロ", "非有限", "異次元", "未知", ""])
def test_unavailable_query_does_not_scan_or_become_zero(generalizer, word):
    class UnreadIndex:
        def candidates(self, *_args, **_kwargs):
            raise AssertionError("入力vector不能時には候補を読まない")

    result = generalizer.generalize(UnreadIndex(), SHAPE, (word, "固定"), target_slot=0)
    assert result.status == "query_vector_unavailable"
    assert result.query_vector.status == "unavailable"
    assert result.query_vector.reason
    assert result.max_cosine is None
    assert result.candidate_count is None
    assert result.evaluated_candidate_count == 0


def test_zero_cosine_subword_and_missing_candidates_are_distinct(generalizer):
    class SmallIndex:
        def __init__(self, words):
            self.words = words

        def candidates(self, *_args, **_kwargs):
            return iter(UsageCandidate(w, (w, "固定"), 1, 1) for w in self.words)

    orthogonal = generalizer.generalize(SmallIndex(["丙"]), SHAPE, WORDS, target_slot=0)
    assert orthogonal.max_cosine == 0
    assert orthogonal.status == "available"
    empty = generalizer.generalize(SmallIndex([]), SHAPE, WORDS, target_slot=0)
    assert (empty.status, empty.candidate_count, empty.max_cosine) == ("no_candidates", 0, None)
    missing = generalizer.generalize(SmallIndex(["ゼロ"]), SHAPE, WORDS, target_slot=0)
    assert missing.status == "candidate_vectors_unavailable"
    assert missing.max_cosine is None
    subword = generalizer.generalize(SmallIndex(["甲"]), SHAPE, ("未登録", "固定"), target_slot=0)
    assert subword.query_vector.status == "subword_generated"
    assert subword.max_cosine == pytest.approx(0.6)


def test_limit_reports_partial_maximum_and_unknown_total(generalizer, index):
    result = generalizer.generalize(index, SHAPE, WORDS, target_slot=0, max_candidates=1)
    assert (result.status, result.truncated, result.candidate_count) == ("truncated", True, None)
    assert result.evaluated_candidate_count == 1
    assert len(result.top_candidates) <= 1
    zero = generalizer.generalize(index, SHAPE, WORDS, target_slot=0, max_candidates=0)
    assert zero.truncated
    assert zero.max_cosine is None
    exact = generalizer.generalize(index, SHAPE, WORDS, target_slot=0, max_candidates=6)
    assert not exact.truncated
    assert exact.candidate_count == 6


def test_only_nominal_slots_and_valid_budgets_are_accepted(generalizer, index):
    with pytest.raises(ValueError, match="NOUN/PROPN"):
        generalizer.generalize(index, SHAPE, WORDS, target_slot=1)
    proper = replace(SHAPE, upos=("PROPN", "VERB"))
    assert generalizer.generalize(index, proper, WORDS, target_slot=0).status == "no_candidates"
    for kwargs in (
        {"target_slot": True},
        {"target_slot": -1},
        {"max_candidates": -1},
        {"batch_size": 0},
    ):
        with pytest.raises(ValueError):
            generalizer.generalize(index, SHAPE, WORDS, **({"target_slot": 0} | kwargs))


@pytest.mark.parametrize("payload", [b"short", b"untrusted pickle"])
def test_untrusted_model_is_rejected_before_public_load(tmp_path, monkeypatch, payload):
    path = tmp_path / "untrusted"
    path.write_bytes(payload)
    monkeypatch.setattr(nominal, "MODEL_BYTES", len(b"untrusted pickle"))
    with pytest.raises(ValueError, match="固定配布|SHA-256"):
        NominalGeneralizer(path)
