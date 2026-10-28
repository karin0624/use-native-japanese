"""語彙資源と文書Lexemeを失わず、判定側の変更を隔離する契約を確かめる。"""

import numpy as np
import pytest
import spacy
import srsly
from spacy.tokens import Doc
from spacy.vocab import Vocab

from native_japanese.vocabulary import (
    VocabularyGeneration,
    VocabularyRecorder,
    VocabularyResources,
    VocabularySnapshot,
    capture_vocabulary_resources,
    restore_lexeme_view,
    restore_vocabulary,
    snapshot_vocabulary,
)


def test_document_vocabulary_keeps_complete_resources_and_local_lexeme_values():
    # 本文外の固定語彙も、解析時の値で復元できることを確かめる。
    vocab = Vocab()
    vocab.set_vector("本文", np.array([1.0, 2.0, 3.0], dtype="f"))
    vocab.set_vector("固定語彙", np.array([4.0, 5.0, 6.0], dtype="f"))
    vocab["固定語彙"].is_stop = True
    resources = capture_vocabulary_resources(vocab)
    doc = Doc(vocab, words=["本文"])
    lexeme = doc[0].lex
    lexeme.norm_ = "独自の正規形"
    lexeme.lower_ = "独自の小文字形"
    lexeme.shape_ = "独自の字形"
    lexeme.prefix_ = "独自の接頭辞"
    lexeme.suffix_ = "独自の接尾辞"
    lexeme.lang_ = "ja"
    lexeme.flags = 2**63 + 7
    lexeme.rank = 42
    vocab.lookups.add_table("lexeme_prob", {lexeme.orth: -2.5})
    vocab.lookups.add_table("lexeme_sentiment", {lexeme.orth: 0.75})
    vocab.lookups.add_table("lexeme_cluster", {lexeme.orth: 17})
    original = doc.to_bytes()
    state = snapshot_vocabulary(doc, resources)

    saved_resources = VocabularyResources.from_bytes(resources.to_bytes())
    generations = {}
    for item in state.generation.ancestors():
        generations[item.generation_id] = VocabularyGeneration.from_bytes(
            saved_resources, item.to_bytes(), parent=generations.get(item.parent_id)
        )
    saved_state = VocabularySnapshot.from_bytes(
        saved_resources, state.to_bytes(), generation=generations[state.generation.generation_id]
    )
    assert saved_state == state
    assert hash(saved_state) == hash(state)
    restored = Doc(restore_vocabulary(saved_state)).from_bytes(original)

    assert doc.to_bytes() == restored.to_bytes() == original
    assert saved_resources.resource_id == resources.resource_id
    assert restored.vocab.vectors.shape == vocab.vectors.shape
    assert restored.vocab.vectors.key2row == vocab.vectors.key2row
    assert set(restored.vocab.strings) == set(vocab.strings)
    assert np.array_equal(restored.vocab.vectors.data, vocab.vectors.data)
    assert restored.vocab["固定語彙"].is_stop
    assert np.array_equal(restored[0].vector, np.array([1.0, 2.0, 3.0], dtype="f"))
    assert restored[0].has_vector == doc[0].has_vector
    assert restored[0].vector_norm == doc[0].vector_norm
    for name in ("norm_", "lower_", "shape_", "prefix_", "suffix_", "lang_", "flags", "rank"):
        assert getattr(restored[0].lex, name) == getattr(lexeme, name)
    assert restored[0].lex.prob == -2.5
    assert restored[0].lex.sentiment == 0.75
    assert restored[0].lex.cluster == 17


def test_restored_vocabulary_changes_do_not_reach_source_or_other_restorations():
    # 複数の判定機は、語彙のキー・値・Lexeme属性を書き換えても互いに干渉しない。
    vocab = Vocab()
    vocab.set_vector("本文", np.array([1.0, 2.0, 3.0], dtype="f"))
    vocab.set_vector("固定語彙", np.array([4.0, 5.0, 6.0], dtype="f"))
    doc = Doc(vocab, words=["本文"])
    state = snapshot_vocabulary(doc)
    first, second = restore_vocabulary(state), restore_vocabulary(state)

    first.vectors.data[:] = -1
    first.vectors.key2row[doc[0].orth] = first.vectors.key2row[vocab.strings["固定語彙"]]
    del first.vectors.key2row[vocab.strings["固定語彙"]]
    first["本文"].is_punct = True
    first.strings.add("判定機が追加した文字列")
    first.lookups.add_table("判定機が追加した表", {"変更": True})

    for restored in (second, restore_vocabulary(state)):
        assert np.array_equal(restored.vectors.data, vocab.vectors.data)
        assert restored.vectors.key2row == vocab.vectors.key2row
        assert not restored["本文"].is_punct
        assert "判定機が追加した文字列" not in restored.strings
        assert "判定機が追加した表" not in restored.lookups
    assert not vocab["本文"].is_punct


def test_documents_share_fixed_resource_without_copying_it_into_each_state():
    vocab = Vocab()
    vocab.set_vector("固定語彙", np.ones(300, dtype="f"))
    resources = capture_vocabulary_resources(vocab)
    first = snapshot_vocabulary(Doc(vocab, words=["第一文"]), resources)
    second = snapshot_vocabulary(Doc(vocab, words=["第二文"]), resources)

    assert first.resources is second.resources is resources
    assert len(first.to_bytes()) < len(resources.snapshot)
    assert len(second.to_bytes()) < len(resources.snapshot)
    assert restore_vocabulary(first).vectors_length == 300
    assert restore_vocabulary(second).vectors_length == 300


def test_empty_synthetic_vocabulary_does_not_gain_fixed_model_vectors():
    state = snapshot_vocabulary(Doc(Vocab(), words=["本文"]))
    assert restore_vocabulary(state).vectors_length == 0


def test_lexeme_view_reads_local_values_while_analysis_keeps_complete_resources():
    vocab = Vocab()
    vocab.set_vector("本文", np.array([1.0, 2.0, 3.0], dtype="f"))
    resources = capture_vocabulary_resources(vocab)
    doc = Doc(vocab, words=["本文"])
    doc[0].lex.is_punct = True
    doc[0].lex.norm_ = "解析時の正規形"
    state = snapshot_vocabulary(doc, resources)
    saved_resources = resources.snapshot

    view = Doc(restore_lexeme_view(state)).from_bytes(doc.to_bytes())

    assert view[0].is_punct
    assert view[0].lex.norm_ == "解析時の正規形"
    view[0].lex.is_punct = False
    assert resources.snapshot == saved_resources
    complete = Doc(restore_vocabulary(state)).from_bytes(doc.to_bytes())
    assert complete[0].is_punct
    assert np.array_equal(complete[0].vector, np.array([1.0, 2.0, 3.0], dtype="f"))
def test_merged_vector_and_previous_document_state_survive_shared_generations():
    # retokenize.mergeが実際に追加するvectorを保存し、次の文書からも参照可能にする。
    vocab = Vocab()
    vocab.reset_vectors(shape=(2, 3))
    vocab.set_vector("first", np.array([1.0, 2.0, 3.0], dtype="f"))
    vocab.set_vector("second", np.array([4.0, 5.0, 6.0], dtype="f"))
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    first = Doc(vocab, words=["first", "second"])
    with first.retokenize() as retokenizer:
        retokenizer.merge(first[:])
    first_state = snapshot_vocabulary(first, resources, recorder=recorder)
    first_values = first[0].vector.copy()
    second = Doc(vocab, words=["next"])
    # 解析済みの他文書に含まれるLexemeへの変更も保存する。
    vocab["first second"].is_stop = True
    vocab.strings.add("本文で使われない文字列")
    second_state = snapshot_vocabulary(second, resources, recorder=recorder)
    complete = restore_vocabulary(second_state)

    assert second_state.generation.parent is first_state.generation
    assert complete.vectors.shape == vocab.vectors.shape
    assert complete.vectors.key2row == vocab.vectors.key2row
    assert np.array_equal(complete.vectors.data, vocab.vectors.data)
    assert set(complete.strings) == set(vocab.strings)
    assert "本文で使われない文字列" in complete.strings
    key = vocab.strings["first second"]
    assert complete.strings[key] == "first second"
    assert complete["first second"].rank == vocab["first second"].rank
    assert complete["first second"].is_stop
    assert np.array_equal(complete.get_vector(key), first_values)
    assert not restore_vocabulary(first_state)["first second"].is_stop
    complete.vectors.data[:] = -1
    complete.vectors.key2row.clear()
    complete["first second"].is_stop = False
    later = restore_vocabulary(second_state)
    assert np.array_equal(later.get_vector(key), first_values)
    assert later["first second"].is_stop


def test_generation_keeps_changed_existing_vector_rows_and_key_mappings():
    vocab = Vocab()
    vocab.set_vector("本文", np.array([1.0, 2.0, 3.0], dtype="f"))
    vocab.set_vector("本文外", np.array([4.0, 5.0, 6.0], dtype="f"))
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    outside = vocab.vectors.key2row[vocab.strings["本文外"]]
    vocab.vectors.data[outside] = [7.0, 8.0, 9.0]
    vocab.vectors.key2row[vocab.strings["本文"]] = outside
    del vocab.vectors.key2row[vocab.strings["本文外"]]
    state = recorder.capture(Doc(vocab, words=["本文"]))
    restored = restore_vocabulary(state)

    assert restored.vectors.key2row == vocab.vectors.key2row
    assert np.array_equal(restored.vectors.data, vocab.vectors.data)
    assert np.array_equal(restored.get_vector("本文"), [7.0, 8.0, 9.0])


def test_generation_keeps_present_zero_vector_distinct_from_missing_key():
    vocab = Vocab()
    vocab.reset_vectors(shape=(2, 3))
    vocab.set_vector("first", np.zeros(3, dtype="f"))
    vocab.set_vector("second", np.zeros(3, dtype="f"))
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    doc = Doc(vocab, words=["first", "second"])
    with doc.retokenize() as retokenizer:
        retokenizer.merge(doc[:])
    state = recorder.capture(doc)
    restored = Doc(restore_vocabulary(state)).from_bytes(doc.to_bytes())

    assert restored[0].has_vector == doc[0].has_vector is True
    assert restored[0].vector_norm == doc[0].vector_norm == 0
    assert restored.vocab.vectors.key2row == vocab.vectors.key2row
    assert np.array_equal(restored[0].vector, doc[0].vector)


def test_long_generation_chain_roundtrip_uses_no_python_recursion():
    vocab = Vocab()
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    for index in range(1100):
        state = recorder.capture(Doc(vocab, words=[f"word{index}"]))
    ancestors = state.generation.ancestors()
    assert len(ancestors) == 1100
    assert state.generation.ancestors({ancestors[-2].generation_id}) == (ancestors[-1],)
    saved = {}
    for generation in ancestors:
        data = generation.to_bytes()
        resource_id, parent_id, identity = VocabularyGeneration.references(data)
        assert resource_id == resources.resource_id
        saved[identity] = VocabularyGeneration.from_bytes(
            resources, data, parent=saved.get(parent_id)
        )
    generation = saved[state.generation.generation_id]
    restored_state = VocabularySnapshot.from_bytes(
        resources, state.to_bytes(), generation=generation
    )
    restored = restore_vocabulary(restored_state)
    assert set(restored.strings) == set(vocab.strings)
    assert len(restored) == len(vocab)


def test_generation_rejects_missing_parent_and_content_identity_mismatch():
    vocab = Vocab()
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    first = recorder.capture(Doc(vocab, words=["第一文"]))
    second = recorder.capture(Doc(vocab, words=["第二文"]))
    with pytest.raises(ValueError, match="親"):
        VocabularyGeneration.from_bytes(resources, second.generation.to_bytes())
    data = srsly.msgpack_loads(second.generation.to_bytes())
    data["generation_id"] = "0" * 64
    with pytest.raises(ValueError, match="内容とID"):
        VocabularyGeneration.from_bytes(
            resources, srsly.msgpack_dumps(data), parent=first.generation
        )


def test_morphology_keeps_previous_documents_and_features_already_in_strings():
    vocab = Vocab()
    original_key = vocab.morphology.add("Case=Nom")
    # StringStoreに既存でも、Morphologyへの登録は後から起き得る。
    vocab.strings.add("Case=Dat")
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    first = Doc(vocab, words=["第一文"])
    first[0].set_morph("Case=Dat")
    added_key = first[0].morph.key
    recorder.capture(first)
    state = recorder.capture(Doc(vocab, words=["第二文"]))
    restored = restore_vocabulary(state)

    assert restored.morphology.get(original_key) == "Case=Nom"
    assert restored.morphology.get(added_key) == "Case=Dat"
    assert restored.morphology.__reduce__()[1][1] == vocab.morphology.__reduce__()[1][1]


def test_capture_many_keeps_one_shared_source_moment_and_each_document_values():
    vocab = Vocab()
    vocab.set_vector("第一文", np.array([1.0, 2.0, 3.0], dtype="f"))
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    first = Doc(vocab, words=["第一文"])
    first[0].set_morph("Case=Nom")
    second = Doc(vocab, words=["第二文"])
    second[0].lex.norm_ = "第二文の正規形"
    originals = (first.to_bytes(), second.to_bytes())
    states = recorder.capture_many((first, second))

    assert states[0].generation is states[1].generation
    for doc, original, state in zip((first, second), originals, states, strict=True):
        restored = Doc(restore_vocabulary(state)).from_bytes(original)
        assert doc.to_bytes() == restored.to_bytes() == original
        assert tuple(restored.vocab.strings) == tuple(vocab.strings)
        assert restored.vocab.vectors.key2row == vocab.vectors.key2row
        assert np.array_equal(restored.vocab.vectors.data, vocab.vectors.data)
        assert restored[0].lex.norm_ == doc[0].lex.norm_
        assert restored[0].morph == doc[0].morph


def test_vector_public_metadata_survives_baseline_and_generation_roundtrips():
    # spaCyのnative形式が省くnameとdefaultモードのcfgも元の公開値を保つ。
    names = ("name", "mode", "minn", "maxn", "hash_count", "hash_seed", "bow", "eow", "attr")
    vocab = Vocab()
    vocab.vectors.name = "固定語彙名"
    first = Doc(vocab, words=["第一文"])
    initial = snapshot_vocabulary(first)
    saved_resources = VocabularyResources.from_bytes(initial.resources.to_bytes())
    saved = VocabularySnapshot.from_bytes(saved_resources, initial.to_bytes())
    restored = restore_vocabulary(saved)
    assert tuple(getattr(restored.vectors, name) for name in names) == tuple(
        getattr(vocab.vectors, name) for name in names
    )

    recorder = VocabularyRecorder(initial.resources)
    vocab.vectors.name = "解析時に変わった語彙名"
    state = recorder.capture(Doc(vocab, words=["第二文"]))
    generation = VocabularyGeneration.from_bytes(initial.resources, state.generation.to_bytes())
    saved = VocabularySnapshot.from_bytes(
        initial.resources, state.to_bytes(), generation=generation
    )
    restored = restore_vocabulary(saved)
    assert tuple(getattr(restored.vectors, name) for name in names) == tuple(
        getattr(vocab.vectors, name) for name in names
    )
    assert restore_vocabulary(initial).vectors.name == "固定語彙名"


@pytest.mark.parametrize("has_norm_table", [False, True])
def test_lexeme_restoration_keeps_lookup_tables_exactly_before_and_after_doc_load(has_norm_table):
    vocab = spacy.blank("ja").vocab
    doc = Doc(vocab, words=["本", "。"])
    if has_norm_table:
        doc[0].lex.norm_ = "登録した正規形"
    original_lookups = vocab.lookups.to_bytes()
    original = doc.to_bytes()
    resources = capture_vocabulary_resources(vocab)
    recorder = VocabularyRecorder(resources)
    state = recorder.capture(doc)

    for restore in (restore_vocabulary, restore_lexeme_view):
        restored_vocab = restore(state)
        assert restored_vocab.lookups.to_bytes() == original_lookups
        restored = Doc(restored_vocab).from_bytes(original)
        assert restored.vocab.lookups.to_bytes() == original_lookups
        assert [token.lex.norm_ for token in restored] == [token.lex.norm_ for token in doc]
        assert vocab.lookups.to_bytes() == original_lookups
