"""公開Docと実モデルで原語・関係・位置と未対応の扱いを確かめる。"""

import ginza
import pytest
import spacy
from spacy.tokens import Doc
from spacy.vocab import Vocab

from native_japanese.models import PARSER_VERSIONS, Query
from native_japanese.parser import (
    Parser,
    _comparison_tokens,
    _endpoint,
    _merge_identifiers,
    adapt_doc,
)


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
                assert text[endpoint.ranges[0][0] : endpoint.ranges[-1][1]] == endpoint.text
    expected_noun = texts[0].rindex("重複")
    assert any(
        row.argument.text.endswith("重複")
        and row.argument.ranges[0][0] <= expected_noun
        and row.argument.ranges[0][1] == expected_noun + 2
        for row in batched[0].relations
    )


@pytest.mark.parametrize(
    "text,identifier,predicate,dependency",
    [
        ("sample12moduleは値を返す。", "sample12module", "返す", "nsubj"),
        ("3sample12moduleは値を返す。", "3sample12module", "返す", "nsubj"),
        ("sample_module-v2.3.zipを配置します。", "sample_module-v2.3.zip", "配置", "obj"),
        ("Node.jsを使う。", "Node.js", "使う", "obj"),
    ],
)
def test_identifiers_are_single_tokens_before_parsing_and_keep_external_edges(
    parser, text, identifier, predicate, dependency
):
    pipeline = parser._require_nlp()
    doc = pipeline.make_doc(text)
    original_doc = doc
    start = text.index(identifier)
    end = start + len(identifier)
    original_span = doc.char_span(start, end, alignment_mode="strict")
    assert original_span is not None and len(original_span) > 1
    for name, component in pipeline.pipeline:
        if name in {"tok2vec", "parser", "morphologizer"}:
            span = doc.char_span(start, end, alignment_mode="strict")
            assert span is not None and len(span) == 1
            assert span[0].text == span[0].lemma_ == identifier
            assert "sub_tokens" not in doc.user_data
        doc = component(doc)
        assert doc is original_doc
        assert doc.text == text
    token = doc.char_span(start, end, alignment_mode="strict")[0]
    assert token.text == token.lemma_ == identifier
    assert token.dep_ == dependency
    assert token.head.lemma_ == predicate
    original = doc.to_bytes()
    analysis = adapt_doc(doc, text)
    assert doc.to_bytes() == original
    assert list(parser.analyze_many([text])) == [analysis] == [parser.analyze(text)]
    matching = [row for row in analysis.relations if row.argument.text == identifier]
    assert len(matching) == 1
    assert matching[0].argument.ranges == ((start, end),)
    assert matching[0].queries == (
        Query((predicate,), token.head.pos_, dependency, (identifier,), token.pos_, "dependent"),
    )
    for row in analysis.relations:
        for endpoint in (row.predicate, row.argument):
            assert text[endpoint.ranges[0][0] : endpoint.ranges[-1][1]] == endpoint.text


def test_identifier_merge_preserves_partial_tokens_single_lemmas_and_numeric_punctuation():
    # 部分token・一tokenのlemma・数字開始の記号境界は変更しない。
    words = [
        "alpha_beta日本語",
        "npm",
        "20.18",
        "2023",
        "-",
        "11",
        "-",
        "25",
        "1",
        "-",
        "2",
        "3",
        "alpha",
        "42",
        "beta",
        "alpha",
        "42",
        "beta",
    ]
    doc = Doc(
        Vocab(),
        words=words,
        spaces=[index in {0, 1, 2, 7, 10, 14} for index in range(len(words))],
        lemmas=["NPM" if word == "npm" else word for word in words],
    )
    original = doc.text
    assert _merge_identifiers(doc) is doc
    assert [token.text for token in doc] == words[:11] + ["3alpha42beta", "alpha42beta"]
    assert doc[1].lemma_ == "NPM"
    assert [token.lemma_ for token in doc[-2:]] == ["3alpha42beta", "alpha42beta"]
    assert doc.text == original


def test_identifier_pipeline_does_not_change_ordinary_japanese(parser):
    baseline = spacy.load("ja_ginza", exclude=["ner"])
    texts = [
        "家でお皿を洗います。",
        "本を読む人",
        "重複を畳んでいる。",
        "npmでgit cloneを実行する。",
    ]
    for text in texts:
        original = baseline(text)
        actual = parser._require_nlp()(text)
        assert [
            (token.text, token.idx, token.lemma_, token.pos_, token.tag_, token.dep_, token.head.i)
            for token in actual
        ] == [
            (token.text, token.idx, token.lemma_, token.pos_, token.tag_, token.dep_, token.head.i)
            for token in original
        ]
        assert adapt_doc(actual, text) == adapt_doc(original, text)


def test_clause_modifier_is_not_reinterpreted_as_a_nominal_argument(parser):
    # 元advclを名詞の項関係と読み替えない。誤った元句・依存は修復しない。
    text = (
        "続く技術文では、保持した<your-node-name>の>がKNPの格解析素性にも未escapeで出現し、"
        "rhoknpが公開形式を読めず処理失敗になりました。"
    )
    doc = parser._require_nlp()(text)
    predicate = next(token for token in doc if token.lemma_ == "保持")
    assert predicate.dep_ == "advcl"
    original = doc.to_bytes()
    result = adapt_doc(doc, text)
    assert all(query.dependency != "advcl" for row in result.relations for query in row.queries)
    assert doc.to_bytes() == original


@pytest.mark.parametrize(
    "text,modifier,expected",
    [
        (
            "新たな句の変化も記録し、局所改善を全体の精度向上と読み替えない。",
            "advcl",
            {
                Query(("記録",), "VERB", "nsubj", ("変化",), "NOUN", "dependent"),
                Query(("読み替える",), "NOUN", "obj", ("局所", "改善"), "NOUN", "dependent"),
                Query(("読み替える",), "NOUN", "obl", ("精度", "向上"), "NOUN", "dependent"),
            },
        ),
        (
            "構築・監査用正本は組合せと全出典対応を保存する。",
            "nmod",
            {
                Query(
                    ("保存",), "VERB", "nsubj", ("構築", "監査", "用", "正本"), "NOUN", "dependent"
                ),
                Query(("保存",), "VERB", "obj", ("全", "出典", "対応"), "NOUN", "dependent"),
            },
        ),
    ],
)
def test_modifier_edges_do_not_create_argument_usages_but_clause_arguments_remain(
    parser, text, modifier, expected
):
    doc = parser._require_nlp()(text)
    assert any(
        token.dep_ == modifier and {token.pos_, token.head.pos_} == {"VERB", "NOUN"}
        for token in doc
    )
    original = doc.to_bytes()
    queries = {query for row in adapt_doc(doc, text).relations for query in row.queries}
    assert expected <= queries
    assert all(query.dependency != modifier for query in queries)
    assert doc.to_bytes() == original


def test_identifier_occurrences_keep_separate_original_positions(parser):
    sentence = "sample_moduleを読む。"
    text = sentence + sentence
    analysis = parser.analyze(text)
    usages = [row for row in analysis.relations if row.argument.text == "sample_module"]
    assert len(usages) == 2
    assert [row.argument.ranges for row in usages] == [
        ((0, len("sample_module")),),
        ((len(sentence), len(sentence) + len("sample_module")),),
    ]


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
    endpoint = _endpoint(tuple(doc[1:3]), doc.text)
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


def test_nominal_subject_relation_is_read_without_promoting_unrelated_noun_pairs(parser):
    # 名詞述語を動作と認定せず、元nsubjが示す両端と品詞をそのまま読む。
    result = parser.analyze("私のねこはとしよりです。")
    assert len(result.relations) == 1
    assert result.relations[0].queries == (
        Query(("としより",), "NOUN", "nsubj", ("ねこ",), "NOUN", "dependent"),
    )
    assert not parser.analyze("処理の再利用").relations


@pytest.mark.parametrize(
    "text,query,predicate_surface,noun_surface",
    [
        (
            "既存の読込み経路を再利用する。",
            Query(("再利用",), "NOUN", "obj", ("読込む", "経路"), "NOUN", "dependent"),
            "再利用",
            "読込み経路",
        ),
        (
            "既成辞書と位置対応は再利用する。",
            Query(("再利用",), "NOUN", "nsubj", ("位置", "対応"), "NOUN", "dependent"),
            "再利用",
            "位置対応",
        ),
        (
            "完成ファイルは再利用し、不完全な取得を完成扱いにしない。",
            Query(("再利用",), "NOUN", "nsubj", ("完成", "ファイル"), "NOUN", "dependent"),
            "再利用",
            "完成ファイル",
        ),
        (
            "次回から再利用します。",
            Query(("再利用",), "NOUN", "obl", ("次回",), "NOUN", "dependent"),
            "再利用",
            "次回",
        ),
        (
            "基本形を返す。",
            Query(("返す",), "VERB", "obj", ("基本形",), "ADJ", "dependent"),
            "返す",
            "基本形",
        ),
    ],
)
def test_explicit_roles_preserve_content_raw_types_and_positions(
    parser, text, query, predicate_surface, noun_surface
):
    # 再利用の既存obj/nsubj/oblと、ADJで返された基本形の目的語役割を接続する。
    doc = parser._require_nlp()(text)
    original = doc.to_bytes()
    result = adapt_doc(doc, text)
    relation = next(row for row in result.relations if query in row.queries)
    for endpoint, surface in (
        (relation.predicate, predicate_surface),
        (relation.argument, noun_surface),
    ):
        start = text.index(surface)
        assert endpoint.text == surface
        assert endpoint.ranges[0][0] == start
        assert endpoint.ranges[-1][1] == start + len(surface)
        assert "".join(text[a:b] for a, b in endpoint.ranges) == surface
    assert doc.to_bytes() == original


@pytest.mark.parametrize(
    "plain,inflected,predicate,noun,predicate_ranges",
    [
        ("値を対応付ける。", "値を対応付けた。", ("対応", "付ける"), ("値",), ((2, 6),)),
        ("違いを同一視する。", "違いを同一視しない。", ("同一", "視"), ("違い",), ((3, 6),)),
        ("記録をつける。", "記録をつけています。", ("つける",), ("記録",), ((3, 5),)),
    ],
)
def test_composition_is_kept_and_functional_inflection_is_shared(
    parser, plain, inflected, predicate, noun, predicate_ranges
):
    expected = Query(predicate, "VERB", "obj", noun, "NOUN", "dependent")
    for text in (plain, inflected):
        doc = parser._require_nlp()(text)
        anchor = next(token for token in doc if token.dep_ == "obj")
        assert anchor.lemma_ == noun[0]
        assert anchor.head.lemma_ in predicate
        original = doc.to_bytes()
        analysis = adapt_doc(doc, text)
        assert len(analysis.relations) == 1
        assert analysis.relations[0].queries == (expected,)
        assert doc.to_bytes() == original
        if text == inflected:
            assert analysis.relations[0].predicate.ranges == predicate_ranges
    if noun == ("記録",):
        # 公開fixedが相成分を機能語へ結び、原述語に混ぜない。
        doc = parser._require_nlp()(inflected)
        aspect = next(token for token in doc if token.lemma_ == "いる")
        assert aspect.dep_ == "fixed"
        assert ginza.bunsetu_position_type(aspect.head) == "SYN_HEAD"


def test_explicit_subject_and_object_keep_original_anchors(parser):
    text = "彼が本を読む。"
    doc = parser._require_nlp()(text)
    subject, noun = doc[0], doc[2]
    assert subject.dep_ == "nsubj" and noun.dep_ == "obj"
    assert subject.head == noun.head == doc[4]
    assert {query for row in adapt_doc(doc, text).relations for query in row.queries} == {
        Query(("読む",), "VERB", "nsubj", ("彼",), "PRON", "dependent"),
        Query(("読む",), "VERB", "obj", ("本",), "NOUN", "dependent"),
    }


def _classified_doc():
    # 分類を入力として扱う形式変換の対照。日本語文法のgold例は作らない。
    doc = Doc(
        Vocab(),
        words=["前語", "で", "いる", "さ", "を", "読む"],
        spaces=[False] * 6,
        pos=["NOUN", "SCONJ", "VERB", "PART", "ADP", "VERB"],
        heads=[5, 0, 1, 0, 0, 5],
        deps=["obj", "case", "fixed", "mark", "case", "ROOT"],
        lemmas=["前語", "で", "いる", "さ", "を", "読む"],
    )
    doc.user_data["bunsetu_heads"] = [0, 5]
    doc.user_data["bunsetu_bi_labels"] = ["B", "I", "I", "I", "I", "B"]
    doc.user_data["bunsetu_position_types"] = [
        "NO_HEAD",
        "CONT",
        "CONT",
        "CONT",
        "FUNC",
        "ROOT",
    ]
    return doc


@pytest.mark.parametrize("functional_dependency", ["aux", "case", "cop"])
def test_functional_edges_and_fixed_are_removed_without_losing_content_or_mark(
    functional_dependency,
):
    doc = _classified_doc()
    doc[1].dep_ = functional_dependency
    original = doc.to_bytes()
    analysis = adapt_doc(doc, doc.text)
    assert len(analysis.relations) == 1
    relation = analysis.relations[0]
    assert relation.queries == (
        Query(("読む",), "VERB", "obj", ("前語", "さ"), "NOUN", "dependent"),
    )
    assert relation.argument.text == "前語でいるさ"
    assert relation.argument.ranges == ((0, 6),)
    assert relation.predicate.ranges == ((7, 9),)
    assert doc.to_bytes() == original


def test_functional_anchor_is_not_replaced_by_other_words_in_the_bunsetu():
    doc = _classified_doc()
    doc.user_data["bunsetu_position_types"][0] = "FUNC"
    assert not adapt_doc(doc, doc.text).relations


@pytest.mark.parametrize(
    "words,pos,dependency",
    [(["読む", "#"], ["VERB", "NOUN"], "acl"), (["本", "#"], ["NOUN", "VERB"], "obj")],
)
def test_punctuation_anchors_are_not_lexical_candidates(
    parser, monkeypatch, words, pos, dependency
):
    doc = Doc(
        parser._require_nlp().vocab,
        words=words,
        spaces=[False, False],
        pos=pos,
        heads=[1, 1],
        deps=[dependency, "ROOT"],
        lemmas=words,
    )
    assert doc[1].is_punct
    monkeypatch.setattr(ginza, "bunsetu_head_tokens", lambda document: [document[1]])
    monkeypatch.setattr(
        ginza, "sub_phrases", lambda callback: lambda token: [(dependency, callback(doc[0]))]
    )
    assert not adapt_doc(doc, doc.text).relations


def test_bunsetu_contract_violation_is_a_system_error(parser, monkeypatch):
    doc = parser._require_nlp()("重複を畳む。")
    monkeypatch.setattr(ginza, "bunsetu_span", lambda token: token.doc[0:1])
    with pytest.raises(ValueError, match="中心となる語が含まれていません"):
        _comparison_tokens(doc[2])


def test_missing_public_position_classification_is_not_silently_discarded():
    doc = _classified_doc()
    del doc.user_data["bunsetu_position_types"]
    with pytest.raises(KeyError, match="bunsetu_position_types"):
        adapt_doc(doc, doc.text)


def test_surface_changes_and_model_failure_are_not_unobserved():
    doc = Doc(Vocab(), words=["重複"], spaces=[False])
    with pytest.raises(ValueError, match="原文全文"):
        adapt_doc(doc, "別の本文")

    def fail(_text):
        raise RuntimeError("解析失敗")

    instance = Parser()
    instance._nlp = fail
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
