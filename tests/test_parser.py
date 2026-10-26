"""公開Docと実モデルで原語・関係・位置と未対応の扱いを確かめる。"""

import ginza
import pytest
from spacy.tokens import Doc
from spacy.vocab import Vocab

from native_japanese.models import PARSER_VERSIONS, Query
from native_japanese.parser import Parser, _endpoint, adapt_doc


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


def test_auxiliary_stem_is_not_a_lexical_noun_but_quoted_usage_is_checked(parser):
    text = "「重複を畳む」のような語の使い方を用例に照らし、確認が必要な名詞・述語を返す。"
    result = parser.analyze(text)
    folding = [row for row in result.relations if row.predicate.text == "畳む"]
    assert len(folding) == 1
    assert folding[0].queries == (Query(("畳む",), "VERB", "obj", ("重複",), "NOUN", "dependent"),)
    assert folding[0].predicate.ranges == ((4, 6),)
    assert folding[0].argument.ranges == ((1, 3),)


def test_ordinary_noun_modifier_and_object_relations_are_preserved(parser):
    reading = parser.analyze("本を読む人")
    assert {query for row in reading.relations for query in row.queries} == {
        Query(("読む",), "VERB", "obj", ("本",), "NOUN", "dependent"),
        Query(("読む",), "VERB", "acl", ("人",), "NOUN", "head"),
    }
    deleting = parser.analyze("ファイルを削除する")
    assert any(
        Query(("削除",), "VERB", "obj", ("ファイル",), "NOUN", "dependent") in row.queries
        for row in deleting.relations
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


def test_whitespace_is_not_a_lemma_and_internal_surface_spaces_are_preserved(parser):
    text = "精度を食べる。\n精度を食べる。"
    result = parser.analyze(text)
    assert len(result.relations) == 2
    first, second = result.relations
    assert first.queries == second.queries
    assert second.queries[0].noun == ("精度",)
    assert second.argument.text == "精度"
    assert second.argument.ranges == ((8, 10),)
    # 公開Docの内部空白は、語彙照会用tokenを選ぶ処理で消さない。
    doc = Doc(
        parser._require_nlp().vocab,
        words=["\n", "New", "York", "\n"],
        spaces=[False, True, False, False],
    )
    endpoint = _endpoint(doc[:], doc.text)
    assert endpoint.text == "New York"
    assert endpoint.ranges == ((1, 9),)


@pytest.mark.parametrize(
    "words,pos,dependency",
    [
        (["考える", "\n"], ["VERB", "NOUN"], "acl"),
        (["精度", "\n"], ["NOUN", "VERB"], "obj"),
    ],
)
def test_whitespace_heads_are_not_lexical_candidates(parser, monkeypatch, words, pos, dependency):
    # 参照701226#14で観測した、改行がNOUNの主辞になる公開属性を再現する。
    # 粗い品詞が述語側に付いた場合も、同じis_space情報で語彙候補から外す。
    doc = Doc(
        parser._require_nlp().vocab,
        words=words,
        spaces=[False, False],
        pos=pos,
        heads=[1, 1],
        deps=[dependency, "ROOT"],
        lemmas=words,
    )
    assert doc[1].is_space
    monkeypatch.setattr(ginza, "bunsetu_head_tokens", lambda document: [document[1]])
    monkeypatch.setattr(
        ginza, "sub_phrases", lambda callback: lambda token: [(dependency, callback(doc[0]))]
    )
    assert not adapt_doc(doc, doc.text).relations


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
