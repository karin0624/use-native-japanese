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


def test_identifier_projection_preserves_external_usages_and_original_doc(parser):
    name = "textlint-rule-preset-native-japanese-0.1.0.tgz"
    cases = [
        (
            "npm registryには未公開です。配布された"
            f"{name}を利用先のプロジェクトに置き、そのルートで導入・実行します。",
            name,
            name,
            (name,),
            {("配布", "acl", "head"), ("置く", "obj", "dependent")},
        ),
        (
            "Node.js 20.18以上を使います。他のプロジェクトでルールを利用する場合と、"
            "このリポジトリをcloneして開発・検証する場合で、準備が異なります。",
            "Node.js",
            "Node.js 20.18以上",
            ("Node.js", "20.18", "以上"),
            {("使う", "obj", "dependent")},
        ),
        (
            "bunsetu_phrase_spanを呼び出します。",
            "bunsetu_phrase_span",
            "bunsetu_phrase_span",
            ("bunsetu_phrase_span",),
            {("呼び出す", "obj", "dependent")},
        ),
        (
            "sample_module-v2.3.zipを配置します。",
            "sample_module-v2.3.zip",
            "sample_module-v2.3.zip",
            ("sample_module-v2.3.zip",),
            {("配置", "obj", "dependent")},
        ),
        (
            "以下を順に実行します。cd use-native-japaneseで入るリポジトリのルート"
            "（package.jsonと.textlintrc.cjsのあるディレクトリ）で、"
            "Git LFSの資源実体と開発依存を準備します。",
            "use-native-japanese",
            "cd use-native-japanese",
            ("CD", "use-native-japanese"),
            {("入る", "obl", "dependent")},
        ),
        (
            "同じrecordの時間内訳では、用例token化は0.187秒、"
            "読取り・decode・項目分割は1.251秒だった。token化が主要因とは断定しない。"
            "残り全文をtokenごとにcopyする実装は、標準re.finditerで元文字列の位置を進める処理へ"
            "簡素化できる。区切り・空token・末尾＊を含む出力同一を確認した範囲だけ変更し、"
            "別の索引・採点・日本語規則は追加しない。根拠は同scratchのminimal-connection-proposal.md。",
            "re.finditer",
            "標準re.finditer",
            ("標準", "re.finditer"),
            {("進める", "obl", "dependent")},
        ),
    ]
    batched = list(parser.analyze_many(text for text, *_ in cases))
    for (text, identifier, phrase, noun_lemmas, expected), analysis in zip(
        cases, batched, strict=True
    ):
        doc = parser._require_nlp()(text)
        original = doc.to_bytes()
        assert adapt_doc(doc, text) == analysis
        assert doc.to_bytes() == original
        name_start = text.index(identifier)
        name_end = name_start + len(identifier)
        related = []
        for row in analysis.relations:
            assert all(
                end <= name_start or name_end <= start for start, end in row.predicate.ranges
            )
            if any(start < name_end and name_start < end for start, end in row.argument.ranges):
                assert row.argument.text == phrase
                phrase_start = text.index(phrase)
                assert row.argument.ranges == ((phrase_start, phrase_start + len(phrase)),)
                related.extend(row.queries)
        assert len(related) == len(expected)
        assert set(related) == {
            Query((predicate,), "VERB", dependency, noun_lemmas, "NOUN", role)
            for predicate, dependency, role in expected
        }


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
                    ("保存",), "VERB", "nsubj", ("構築", "・", "監査", "用", "正本"), "NOUN", "dependent"
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


def test_identifier_root_does_not_replace_original_endpoint_type(parser):
    text = (
        "textlint 15.8.0と、公式ルールでも使われるtextlint-util-to-string 3.3.4を使い、"
        "抽出・位置変換を既存APIへ委ねる。StringSourceは最終リリースが2023-11-25で、"
        "readonly対応PR #25は未merge。保守が活発とはみなさない。"
    )
    result = parser.analyze(text)
    query = Query(("使う",), "VERB", "acl", ("textlint-util-to-string",), "NOUN", "head")
    usages = [row for row in result.relations if query in row.queries]
    assert len(usages) == 1
    start = text.index("textlint-util-to-string")
    assert usages[0].argument.text == "textlint-util-to-string"
    assert usages[0].argument.ranges == ((start, start + len("textlint-util-to-string")),)


def test_projected_relation_is_unique_at_each_distinct_occurrence(parser):
    # 日本語Wikipedia「T-34 (航空機)」著者、2019-02-01版、CC BY-SA 3.0。
    # https://ja.wikipedia.org/?curid=160755 （自然参照160755#5から一文を引用）
    sentence = (
        "ターボプロップエンジンへの換装により性能が向上したほか、兵器訓練型のT-34C-1では"
        "主翼下に4箇所のハードポイントが設けられ、COIN機としての運用も可能だった。"
    )
    result = parser.analyze(sentence + sentence)
    query = Query(("設ける",), "VERB", "obl", ("T-34C-1",), "NOUN", "dependent")
    usages = [row for row in result.relations if query in row.queries]
    assert len(usages) == 2
    start = sentence.index("T-34C-1")
    predicate_start = sentence.index("設け")
    for row, offset in zip(usages, (0, len(sentence)), strict=True):
        assert row.argument.ranges == ((offset + start, offset + start + len("T-34C-1")),)
        assert row.predicate.ranges == ((offset + predicate_start, offset + predicate_start + 2),)


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
        assert endpoint.ranges == ((start, start + len(surface)),)
    assert doc.to_bytes() == original


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
    assert tuple(token.text for token in ginza.bunsetu_phrase_tokens(particle)) == ("お", "皿")
    assert ginza.bunsetu_phrase_tokens(particle, use_token_head=True) == (particle,)
    noun = particle.head
    assert ginza.bunsetu_phrase_span(noun, use_token_head=True).text == "お皿"


def test_phrase_selection_excludes_gap_tokens_from_query_but_preserves_display():
    # 選択集合の穴と識別子の投影重複を公開Docで再現し、接続を確かめる。
    doc = Doc(
        Vocab(),
        words=["保持", "し", "た", "<", "your", "-", "node", "-", "name", ">", "の"],
        spaces=[False] * 11,
        pos=["VERB", "AUX", "AUX", "PUNCT", "PROPN", "SYM", "NOUN", "SYM", "NOUN", "SYM", "ADP"],
        heads=[8, 0, 0, 0, 0, 0, 0, 0, 8, 8, 8],
        deps=["acl", "aux", "aux", "punct", "compound", "compound", "compound", "dep", "ROOT", "punct", "case"],
        lemmas=["保持", "する", "た", "<", "Your", "-", "node", "-", "name", ">", "の"],
    )
    doc.user_data["bunsetu_heads"] = [0, 8]
    doc.user_data["bunsetu_bi_labels"] = ["B", *(["I"] * 7), "B", "I", "I"]
    original = doc.to_bytes()
    selected = ginza.bunsetu_phrase_tokens(doc[0], use_token_head=True)
    assert selected == (doc[0], doc[4], doc[5], doc[6])
    assert ginza.bunsetu_phrase_tokens(doc[0], (), use_token_head=True) == (doc[0],)
    assert ginza.bunsetu_phrase_span(doc[0], use_token_head=True).text == "保持した<your-node"
    analysis = adapt_doc(doc, doc.text)
    assert len(analysis.relations) == 1
    relation = analysis.relations[0]
    # 句選択の正否を修正したことにはせず、非選択語の混入と両端の重複投影を防ぐ。
    assert relation.queries == (
        Query(("保持", "Your", "-", "node"), "VERB", "acl", ("name",), "NOUN", "head"),
    )
    assert relation.predicate.text == "保持した<your-node"
    assert relation.predicate.ranges == ((0, 14),)
    assert relation.argument.text == "name"
    assert relation.argument.ranges == ((15, 19),)
    assert doc.to_bytes() == original


@pytest.mark.parametrize("api", ["bunsetu_phrase_span", "bunsetu_phrase_tokens"])
def test_phrase_contract_violation_is_a_system_error(parser, monkeypatch, api):
    # 依存の契約違反を通常の照合結果へ変換しない。
    monkeypatch.setattr(ginza, api, lambda token, **kwargs: token.doc[0:1])
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
