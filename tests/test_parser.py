"""公開spaCy Docの保持と、batch入力の出現元対応を点検する。"""

import pytest
import spacy
from spacy.tokens import Doc

from native_japanese import ParseMetadata
from native_japanese.parser import GinzaParser, from_spacy

METADATA = ParseMetadata("fixture", "1", "fixture", "1", "1", "fixture", "1", "1")


def test_public_doc_fields_unicode_and_sentence_boundaries_are_preserved():
    doc = Doc(
        spacy.blank("xx").vocab,
        words=["🧪", "e\u0301", "Ａ", "次", "文"],
        spaces=[True, True, True, False, False],
        heads=[2, 2, 2, 4, 4],
        deps=["obj", "obl", "ROOT", "dep", "ROOT"],
        pos=["NOUN", "NOUN", "VERB", "NOUN", "VERB"],
        tags=["名詞", "名詞", "動詞", "名詞", "動詞"],
        lemmas=["🧪", "e\u0301", "a", "次", "文"],
        morphs=["", "Case=Gen", "Tense=Past", "", ""],
    )
    result = from_spacy(doc, source_id="unicode", metadata=METADATA)
    assert result.text == "🧪 e\u0301 Ａ 次文"
    assert [
        (t.id, t.start, t.end, t.surface, t.lemma, t.upos, t.tag, t.morph, t.head, t.dep)
        for t in result.tokens
    ] == [
        (
            t.i,
            t.idx,
            t.idx + len(t.text),
            t.text,
            t.lemma_,
            t.pos_,
            t.tag_,
            str(t.morph),
            t.head.i,
            t.dep_,
        )
        for t in doc
    ]
    assert [(s.token_start, s.token_end, s.start, s.end) for s in result.sentences] == [
        (s.start, s.end, s.start_char, s.end_char) for s in doc.sents
    ]
    assert len(result.sentences) == 2


def test_pipe_batch_preserves_source_identity_and_empty_documents():
    vocab = spacy.blank("xx").vocab
    docs = {
        "A": Doc(
            vocab,
            words=["A"],
            spaces=[False],
            heads=[0],
            deps=["ROOT"],
            lemmas=["A"],
            pos=["NOUN"],
        ),
        "": Doc(vocab),
    }

    class SavedPipeline:
        def pipe(self, records, *, as_tuples, batch_size):
            assert as_tuples is True
            assert batch_size == 2
            for text, context in records:
                yield docs[text], context

    parser = GinzaParser.__new__(GinzaParser)
    parser._nlp = SavedPipeline()
    parser.metadata = METADATA
    results = list(
        parser.parse_many((("first", "A"), ("empty", ""), ("second", "A")), batch_size=2)
    )
    assert [(r.source_id, r.text) for r in results] == [
        ("first", "A"),
        ("empty", ""),
        ("second", "A"),
    ]
    assert results[1].tokens == results[1].sentences == ()


@pytest.mark.parametrize(
    "field, annotation", [("deps", "DEP"), ("lemmas", "LEMMA"), ("pos", "POS")]
)
def test_partial_required_annotations_are_rejected_while_optional_empty_values_are_allowed(
    field, annotation
):
    vocab = spacy.blank("xx").vocab
    attributes = {"deps": ["ROOT", "dep"], "lemmas": ["A", "B"], "pos": ["NOUN", "NOUN"]}
    complete = Doc(vocab, words=["A", "B"], heads=[0, 0], **attributes)
    converted = from_spacy(complete, source_id="complete", metadata=METADATA)
    assert all(t.tag == t.morph == "" for t in converted.tokens)

    attributes[field][1] = ""
    partial = Doc(vocab, words=["A", "B"], heads=[0, 0], **attributes)
    with pytest.raises(ValueError, match=annotation):
        from_spacy(partial, source_id="partial", metadata=METADATA)
