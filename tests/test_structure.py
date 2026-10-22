"""日本語規則を使わない、出現単位・原文順・予算の契約を確認する。"""

from dataclasses import replace

import pytest

from native_japanese import (
    DependencyEdge,
    Document,
    FragmentBudgetExceeded,
    ParseMetadata,
    Sentence,
    Token,
    enumerate_fragments,
)

METADATA = ParseMetadata("fixture", "1", "fixture", "1", "1", "fixture", "1", "1")


def make_document(heads, *, lemmas=None, words=None, sentence_ends=None):
    words = words or [chr(65 + i) for i in range(len(heads))]
    lemmas = lemmas or words
    text = " ".join(words)
    tokens = []
    start = 0
    for i, (head, word, lemma) in enumerate(zip(heads, words, lemmas, strict=True)):
        tokens.append(Token(i, start, start + len(word), word, lemma, "X", "", "", head, "rel"))
        start += len(word) + 1
    sentences = []
    first = 0
    for i, end in enumerate(sentence_ends or [len(tokens)]):
        sentences.append(Sentence(i, first, end, tokens[first].start, tokens[end - 1].end))
        first = end
    return Document("fixture", text, tuple(tokens), tuple(sentences), METADATA)


def test_branch_joint_preserves_same_head_and_induced_edges():
    document = make_document([3, 3, 3, 3])
    fragments = enumerate_fragments(document, sentence_id=0, max_fragments=7)
    assert [f.node_ids for f in fragments] == [
        (0, 3),
        (1, 3),
        (2, 3),
        (0, 1, 3),
        (0, 2, 3),
        (1, 2, 3),
        (0, 1, 2, 3),
    ]
    joint = fragments[-1]
    assert joint.lemmas == ("A", "B", "C", "D")
    assert joint.shape.edges == (
        DependencyEdge(3, 0, "rel"),
        DependencyEdge(3, 1, "rel"),
        DependencyEdge(3, 2, "rel"),
    )
    # 文中の同じlemmaでも、異なるheadに付いた子をjointに混ぜない。
    repeated = make_document([2, 5, 2, 2, 5, 2], lemmas=["a", "b", "v", "x", "y", "v"])
    results = enumerate_fragments(repeated, sentence_id=0, max_fragments=100)
    assert (0, 1, 2) not in {f.node_ids for f in results}
    assert (0, 2, 3) in {f.node_ids for f in results}
    assert (1, 4, 5) in {f.node_ids for f in results}


def test_key_ignores_absolute_position_gap_surface_morph_but_retains_order_and_direction():
    first = make_document([2, 2, 2])
    shifted = make_document([0, 4, 4, 4, 4], lemmas=["unused", "A", "B", "unused", "C"])
    a = next(
        f
        for f in enumerate_fragments(first, sentence_id=0, max_fragments=20)
        if f.node_ids == (0, 1, 2)
    )
    b = next(
        f
        for f in enumerate_fragments(shifted, sentence_id=0, max_fragments=20)
        if f.node_ids == (1, 2, 4)
    )
    assert a.key == b.key
    changed_tokens = tuple(replace(t, morph="Case=Gen") for t in shifted.tokens)
    changed = replace(shifted, tokens=changed_tokens)
    assert b.key in {f.key for f in enumerate_fragments(changed, sentence_id=0, max_fragments=20)}
    reversed_head = make_document([0, 0, 0])
    assert a.key not in {
        f.key for f in enumerate_fragments(reversed_head, sentence_id=0, max_fragments=20)
    }


def test_budget_has_no_partial_success_and_exact_limit_is_allowed():
    document = make_document([3, 3, 3, 3])
    with pytest.raises(FragmentBudgetExceeded, match="未完了") as error:
        enumerate_fragments(document, sentence_id=0, max_fragments=6)
    assert error.value.max_fragments == 6
    assert len(enumerate_fragments(document, sentence_id=0, max_fragments=7)) == 7
    single = make_document([0])
    assert enumerate_fragments(single, sentence_id=0, max_fragments=0) == ()


def test_sentences_and_forest_components_do_not_mix():
    document = make_document([1, 1, 3, 3], sentence_ends=[2, 4])
    first = enumerate_fragments(document, sentence_id=0, max_fragments=1)
    second = enumerate_fragments(document, sentence_id=1, max_fragments=1)
    assert [f.node_ids for f in first + second] == [(0, 1), (2, 3)]
    assert second[0].source_id == document.source_id
    assert second[0].sentence == document.sentences[1]
    forest = make_document([1, 1, 3, 3])
    assert len(enumerate_fragments(forest, sentence_id=0, max_fragments=2)) == 2


@pytest.mark.parametrize("heads,ends", [([1, 0], None), ([2, 1], None), ([1, 1], [1, 2])])
def test_invalid_cycle_external_and_cross_sentence_heads_are_rejected(heads, ends):
    with pytest.raises(ValueError):
        make_document(heads, sentence_ends=ends)


def test_unicode_offsets_and_surface_must_match_original():
    document = make_document([2, 2, 2], words=["🧪", "e\u0301", "Ａ"])
    assert [(t.start, t.end) for t in document.tokens] == [(0, 1), (2, 4), (5, 6)]
    with pytest.raises(ValueError, match="表層"):
        replace(document, tokens=(replace(document.tokens[0], surface="x"), *document.tokens[1:]))
