"""公開Docと実モデルで原語・関係・位置と未対応の扱いを確かめる。"""

import pytest
from spacy.tokens import Doc
from spacy.vocab import Vocab

from native_japanese.models import PARSER_VERSIONS, Query
from native_japanese.parser import Parser, adapt_doc


@pytest.fixture(scope="module")
def parser():
    instance = Parser()
    yield instance
    instance.close()


def test_same_basic_form_and_original_phrase_positions(parser):
    plain = parser.analyze("重複を畳む。")
    inflected = parser.analyze("重複を畳んでいる。")
    query = Query(("畳む",), "VERB", "obj", ("重複",), "NOUN", "dependent")
    assert plain.relations[0].queries == inflected.relations[0].queries == (query,)
    assert inflected.relations[0].predicate.text == "畳ん"
    assert inflected.relations[0].predicate.ranges == ((3, 5),)
    assert inflected.relations[0].argument.text == "重複"
    assert inflected.relations[0].argument.ranges == ((0, 2),)


def test_full_noun_phrase_and_adjective_in_reverse_direction(parser):
    washing = parser.analyze("家でお皿を洗います。")
    assert any(
        Query(("洗う",), "VERB", "obj", ("お", "皿"), "NOUN", "dependent") in row.queries
        and row.argument.text == "お皿"
        for row in washing.relations
    )
    hit = parser.analyze("世界的な大ヒットとなった。")
    assert any(
        Query(("世界的",), "ADJ", "acl", ("大ヒット",), "NOUN", "head") in row.queries
        and row.predicate.text == "世界的"
        and row.argument.text == "大ヒット"
        for row in hit.relations
    )


def test_literal_text_and_batch_results_preserve_original_positions(parser):
    texts = ["😀 `重複`を`畳む`。\n重複を畳んでいる。\n", "", " \n ", "電気を消す。"]
    batched = list(parser.analyze_many(iter(texts)))
    assert batched == [parser.analyze(text) for text in texts]
    assert [result.text for result in batched] == texts
    for text, result in zip(texts, batched, strict=True):
        for relation in result.relations:
            for endpoint in (relation.predicate, relation.argument):
                assert "".join(text[start:end] for start, end in endpoint.ranges) == endpoint.text
    expected_noun = texts[0].rindex("重複")
    assert any(
        row.argument.text.endswith("重複")
        and row.argument.ranges[0][0] <= expected_noun
        and row.argument.ranges[0][1] == expected_noun + 2
        for row in batched[0].relations
    )


def test_noun_predicates_do_not_become_comparison_failures(parser):
    result = parser.analyze("私のねこはとしよりです。")
    assert not result.relations
    assert not result.unavailable


def test_phrase_excluding_its_source_token_is_unavailable():
    # 複数の外部依存を持つ文節で、公式phraseのrootと元Tokenが異なる場合。
    doc = Doc(
        Vocab(),
        words=["場合", "インターネット", "を", "使う", "。"],
        spaces=[False] * 5,
        heads=[3, 3, 1, 3, 3],
        deps=["obl", "obj", "case", "ROOT", "punct"],
        pos=["NOUN", "NOUN", "ADP", "VERB", "PUNCT"],
        lemmas=["場合", "インターネット", "を", "使う", "。"],
    )
    doc.user_data["bunsetu_heads"] = (0, 1, 3)
    doc.user_data["bunsetu_bi_labels"] = ["B", "I", "I", "B", "I"]
    result = adapt_doc(doc, doc.text)
    assert len(result.unavailable) == 1
    assert result.unavailable[0].ranges == ((2, 9),)
    assert "主辞トークンを含みません" in result.unavailable[0].reason
    assert all(query.dependency != "obj" for row in result.relations for query in row.queries)
    assert any(query.dependency == "obl" for row in result.relations for query in row.queries)


def test_surface_changes_and_model_failure_are_not_unobserved(monkeypatch):
    doc = Doc(Vocab(), words=["重複"], spaces=[False])
    with pytest.raises(ValueError, match="原文全文"):
        adapt_doc(doc, "別の本文")

    def fail(_text):
        raise RuntimeError("解析失敗")

    monkeypatch.setattr("native_japanese.parser.spacy.load", lambda *args, **kwargs: fail)
    instance = Parser()
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
    with pytest.raises(RuntimeError, match="解析契約と異なる版.*spacy"):
        Parser()
