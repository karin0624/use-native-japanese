"""公開Docと実モデルで原語・関係・位置と未対応の扱いを確かめる。"""

import ginza
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


def test_original_head_is_preserved_when_bunsetu_root_is_another_token(parser):
    # 原因再現のため一文だけ引用。文節spanのrootは別の「後漢」になる。
    # 日本語Wikipedia「後漢書」著者、2019-02-01版、CC BY-SA 3.0。
    # https://ja.wikipedia.org/?curid=160967 （自然参照160967#0）
    text = "『後漢書』（ごかんじょ）は、中国後漢朝について書かれた歴史書。"
    result = parser.analyze(text)
    expected = Query(("書く",), "VERB", "obl", ("中国", "後漢", "朝"), "NOUN", "dependent")
    relation = next(row for row in result.relations if expected in row.queries)
    assert relation.argument.text == "中国後漢朝"
    assert relation.argument.ranges == ((14, 19),)
    assert relation.predicate.text == "書か"
    assert relation.predicate.ranges == ((23, 25),)


def test_phrase_api_default_and_non_head_behavior_are_preserved(parser):
    doc = parser._require_nlp()("家でお皿を洗います。")
    particle = next(token for token in doc if token.text == "を")
    assert ginza.bunsetu_phrase_span(particle).text == "お皿"
    noun = particle.head
    assert ginza.bunsetu_phrase_span(noun, use_token_head=True).text == "お皿"


def test_phrase_contract_violation_is_a_system_error(parser, monkeypatch):
    # 依存の契約違反を通常の照合結果へ変換しない。
    monkeypatch.setattr(ginza, "bunsetu_phrase_span", lambda token, **kwargs: token.doc[0:1])
    with pytest.raises(ValueError, match="中心となる語が含まれていません"):
        parser.analyze("重複を畳む。")


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
    with pytest.raises(RuntimeError, match="解析に必要なバージョンと異なります.*spacy"):
        Parser()
