"""一般依存木fixtureで、joint・周辺数・記事支持・原文復元を点検する。"""

import hashlib
import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from native_japanese.structure import (
    DependencyEdge,
    Document,
    FragmentBudgetExceeded,
    FragmentShape,
    ParseMetadata,
    Sentence,
    Token,
)
from native_japanese.usage_index import (
    IndexUnavailableError,
    UsageIndex,
    UsageIndexBuilder,
)

META = ParseMetadata("fixture", "1", "公開値", "1", "1", "辞書", "1", "1")
IDENTITY = "固定fixture"
SOURCE_SHA = hashlib.sha256(b"fixture").hexdigest()
SHAPE = FragmentShape(
    ("NOUN", "ADJ", "VERB"),
    (
        DependencyEdge(2, 0, "obj"),
        DependencyEdge(2, 1, "advmod"),
    ),
)


def document(source_id, groups):
    """一つの文に独立した複数headを置ける一般graph。日本語正誤は定義しない。"""
    tokens = []
    parts = []
    offset = 0
    for words in groups:
        base = len(tokens)
        for slot, (word, upos, dep) in enumerate(
            zip(words, SHAPE.upos, ("obj", "advmod", "ROOT"), strict=True)
        ):
            parts.append(word)
            tokens.append(
                Token(
                    base + slot,
                    offset,
                    offset + len(word),
                    word,
                    word,
                    upos,
                    upos,
                    "",
                    base + 2,
                    dep,
                )
            )
            offset += len(word) + 1
    text = " ".join(parts)
    return Document(
        source_id, text, tuple(tokens), (Sentence(0, 0, len(tokens), 0, len(text)),), META
    )


def builder(path, expected_documents):
    return UsageIndexBuilder(
        path,
        metadata=META,
        source_identity=IDENTITY,
        source_sha256=SOURCE_SHA,
        expected_documents=expected_documents,
        max_fragments_per_sentence=1000,
    )


def open_index(path, **kwargs):
    return UsageIndex(
        path, expected_metadata=META, source_identity=IDENTITY, source_sha256=SOURCE_SHA, **kwargs
    )


def test_joint_is_same_token_occurrence_and_citation_roundtrip(tmp_path):
    # 同じ文中にx-b-vとy-a-vがあっても、x-a-vの共同出現ではない。
    doc = document("非連続offset", (("x", "b", "v"), ("y", "a", "v")))
    with builder(tmp_path, 1) as build:
        build.add_document(doc, article_id="記事A")
        build.finish(input_complete=True)
    with open_index(tmp_path) as index:
        counts = index.lookup(SHAPE, ("x", "a", "v"), target_slot=0)
        assert (counts.c, counts.r, counts.m, counts.N) == (0, 1, 1, 2)
        assert counts.contingency == (0, 1, 1, 0)
        # 候補metadataだけの列挙で原文正本を読まないことを実DBで確認する。
        index._sources.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
        candidates = list(index.candidates(SHAPE, ("x", "a", "v"), target_slot=0))
        index._sources.set_authorizer(None)
        assert [(c.lemma, c.lemmas, c.count, c.articles) for c in candidates] == [
            ("y", ("y", "a", "v"), 1, 1),
        ]
        citation = index.citation(SHAPE, candidates[0].lemmas)
        assert citation.source_id == doc.source_id
        assert citation.node_ids == (3, 4, 5)
        assert citation.surfaces == ("y", "a", "v")
        assert tuple(doc.text[s:e] for s, e in citation.spans) == citation.surfaces
        assert index.citation(SHAPE, ("y", "a", "v")) == citation
        pair_shape = FragmentShape(("NOUN", "VERB"), (DependencyEdge(1, 0, "obj"),))
        pair = index.citation(pair_shape, ("x", "v"))
        assert pair.node_ids == (0, 2)
        assert pair.spans[0][1] < pair.spans[1][0]
        assert pair.surfaces == ("x", "v")
        assert isinstance(
            index.values[next(i for i, v in enumerate(index.values) if "f" in v)]["f"], np.memmap
        )


def test_marginals_article_dedup_order_and_original_separation(tmp_path):
    # Aの二つのpassageがBを挟んでもdistinct記事数は増えない。
    rows = [
        ("p1", "A", ("x", "a", "v")),
        ("p2", "B", ("y", "a", "v")),
        ("p3", "A", ("x", "a", "v")),
        ("p4", "C", ("x", "b", "w")),
    ]
    with builder(tmp_path, len(rows)) as build:
        for passage, article, words in rows:
            build.add_document(document(passage, (words,)), article_id=article)
        build.finish(input_complete=True)
    with open_index(tmp_path) as index:
        counts = index.lookup(SHAPE, ("x", "a", "v"), target_slot=0)
        # mはtarget以外の全slotを自由にする。wも含む。
        assert (counts.c, counts.r, counts.m, counts.N) == (2, 3, 3, 4)
        assert (counts.c_articles, counts.r_articles) == (1, 2)
        assert counts.contingency == (2, 1, 1, 0)
        assert [c.lemma for c in index.candidates(SHAPE, ("x", "a", "v"), target_slot=0)] == ["y"]
        all_candidates = list(
            index.candidates(SHAPE, ("x", "a", "v"), target_slot=0, include_original=True)
        )
        assert {c.lemma: (c.count, c.articles) for c in all_candidates} == {
            "x": (2, 1),
            "y": (1, 1),
        }
        # 未見原語Fでもqは存在する。別の共同条件だけが未見の場合も区別する。
        unseen = index.lookup(SHAPE, ("未登録", "a", "v"), target_slot=0)
        assert (unseen.c, unseen.r, unseen.m, unseen.N, unseen.status) == (0, 3, 0, 4, "observed")
        missing_q = index.lookup(SHAPE, ("x", "未登録", "v"), target_slot=0)
        assert (missing_q.c, missing_q.r, missing_q.m, missing_q.N, missing_q.status) == (
            0,
            0,
            3,
            4,
            "q_unseen",
        )
        missing_shape = replace(SHAPE, upos=("NOUN", "NOUN", "VERB"))
        empty = index.lookup(missing_shape, ("x", "a", "v"), target_slot=0)
        assert (empty.c, empty.r, empty.m, empty.N, empty.status) == (0, 0, 0, 0, "shape_unseen")
        # 上の4出現から手で数えたslot別の期待値。照会結果の恒等式で代用しない。
        expected = (
            ((2, 3, 3, 4), (0, 3, 0, 4), (0, 3, 0, 4), (0, 3, 0, 4)),
            ((0, 2, 0, 4), (2, 2, 3, 4), (0, 2, 0, 4), (0, 2, 0, 4)),
            ((0, 2, 0, 4), (0, 2, 0, 4), (2, 2, 3, 4), (0, 2, 0, 4)),
        )
        for slot in range(3):
            for original, counts in zip(("x", "a", "v", "未登録"), expected[slot], strict=True):
                words = ["x", "a", "v"]
                words[slot] = original
                result = index.lookup(SHAPE, tuple(words), target_slot=slot)
                assert (result.c, result.r, result.m, result.N) == counts


def test_incomplete_version_corruption_and_budget_do_not_become_zero(tmp_path):
    with builder(tmp_path, 1) as build:
        with pytest.raises(IndexUnavailableError, match="未完了"):
            build.finish(input_complete=False)
        with pytest.raises(IndexUnavailableError, match="未完了"):
            open_index(tmp_path)
        build.add_document(document("p", (("x", "a", "v"),)), article_id="A")
        build.finish(input_complete=True)
    with pytest.raises(IndexUnavailableError, match="一致"):
        UsageIndex(
            tmp_path,
            expected_metadata=replace(META, parser_version="2"),
            source_identity=IDENTITY,
            source_sha256=SOURCE_SHA,
        )
    target = tmp_path / "lemma.ids.npy"
    data = target.read_bytes()
    target.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(IndexUnavailableError, match="SHA"):
        open_index(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    for malformed in ([], None, "破損"):
        manifest_path.write_text(json.dumps(malformed))
        with pytest.raises(IndexUnavailableError, match="JSON object"):
            open_index(tmp_path)
    other = tmp_path / "budget"
    with UsageIndexBuilder(
        other,
        metadata=META,
        source_identity=IDENTITY,
        source_sha256=SOURCE_SHA,
        expected_documents=1,
        max_fragments_per_sentence=0,
    ) as build:
        with pytest.raises(FragmentBudgetExceeded):
            build.add_document(document("p", (("x", "a", "v"),)), article_id="A")
        assert build.documents == 0
        with pytest.raises(IndexUnavailableError, match="予定文書数"):
            build.finish(input_complete=True)
    assert json.loads((other / "manifest.json").read_bytes())["complete"] is False


def test_empty_completed_snapshot_and_query_bounds(tmp_path):
    with builder(tmp_path, 0) as build:
        build.finish(input_complete=True)
    with open_index(tmp_path) as index:
        assert index.lookup(SHAPE, ("x", "a", "v"), target_slot=0).status == "shape_unseen"
        assert list(index.candidates(SHAPE, ("x", "a", "v"), target_slot=0)) == []
        with pytest.raises(ValueError, match="範囲外"):
            index.lookup(SHAPE, ("x", "a", "v"), target_slot=3)
        with pytest.raises(ValueError, match="2〜4"):
            index.lookup(FragmentShape(("NOUN",), ()), ("x",), target_slot=0)


def test_four_node_all_slots_and_duplicate_source_rejection(tmp_path):
    words = ("x", "a", "b", "v")
    positions = ("NOUN", "ADJ", "ADV", "VERB")
    heads = (3, 3, 1, 3)
    deps = ("obj", "advmod", "advmod", "ROOT")
    tokens = tuple(
        Token(i, 2 * i, 2 * i + 1, word, word, positions[i], "", "", heads[i], deps[i])
        for i, word in enumerate(words)
    )
    doc = Document("4node", "x a b v", tokens, (Sentence(0, 0, 4, 0, 7),), META)
    shape = FragmentShape(
        positions,
        (
            DependencyEdge(1, 2, "advmod"),
            DependencyEdge(3, 0, "obj"),
            DependencyEdge(3, 1, "advmod"),
        ),
    )
    with builder(tmp_path, 2) as build:
        build.add_document(doc, article_id="A")
        # 同一passageを再投入しても、既存集約へ足さずtransactionを取り消す。
        with pytest.raises(sqlite3.IntegrityError):
            build.add_document(doc, article_id="B")
        build.add_document(replace(doc, source_id="別passage"), article_id="A")
        build.finish(input_complete=True)
    with open_index(tmp_path) as index:
        for slot in range(4):
            result = index.lookup(shape, words, target_slot=slot)
            assert (result.c, result.r, result.m, result.N) == (2, 2, 2, 2)
            assert (result.c_articles, result.r_articles) == (1, 1)
            assert list(index.candidates(shape, words, target_slot=slot)) == []
        assert index.citation(shape, words).node_ids == (0, 1, 2, 3)


def test_relative_builder_path_stays_at_initial_directory(tmp_path, monkeypatch):
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    with builder(second / "index", 1) as existing:
        existing.add_document(document("p", (("x", "a", "v"),)), article_id="A")
        existing.finish(input_complete=True)
    original = {p.name: p.read_bytes() for p in (second / "index").iterdir()}
    monkeypatch.chdir(first)
    with builder("index", 0) as build:
        monkeypatch.chdir(second)
        build.finish(input_complete=True)
    assert {p.name: p.read_bytes() for p in (second / "index").iterdir()} == original
    with open_index(first / "index") as empty:
        assert empty.manifest["documents"] == 0
    with open_index(second / "index") as existing:
        assert existing.lookup(SHAPE, ("x", "a", "v"), target_slot=0).c == 1
