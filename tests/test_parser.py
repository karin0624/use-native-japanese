"""KWJAの全情報、原文との対応、ネイティブ構成の照会契約を確認する。"""

from dataclasses import replace

import pytest
from rhoknp import Document
from test_examples import make_analysis

from native_japanese.models import PARSER_VERSIONS
from native_japanese.parser import (
    Parser,
    _normalized_source,
    _word_window,
    adapt_doc,
    document_offsets,
)


@pytest.fixture(scope="module")
def parser():
    instance = Parser()
    yield instance
    instance.close()


def test_native_snapshot_is_complete_independent_and_lossless():
    analysis = make_analysis()
    document = analysis.restore_doc()
    document.base_phrases[0].features["意味情報"] = "保持する"
    document.morphemes[0].semantics["代表表記"] = "東/ひがし"
    analysis = adapt_doc(document, analysis.text)
    original = document.to_knp()
    assert analysis.snapshot.decode("utf-8") == original
    assert analysis.restore_doc().to_knp() == original
    restored = analysis.restore_doc()
    restored.morphemes[0].lemma = "変更"
    restored.base_phrases[0].features["意味情報"] = "変更"
    assert analysis.restore_doc().to_knp() == original == document.to_knp()
    assert analysis.comparison == adapt_doc(document, analysis.text).comparison


@pytest.mark.parametrize(
    "text,normalized,spans",
    [
        ("ＡAＡA", "AAAA", ((0, 1), (1, 2), (2, 3), (3, 4))),
        ("ｶﾞガｶﾞ", "ガガガ", ((0, 2), (2, 3), (3, 5))),
        ("か\u3099がか\u3099", "ががが", ((0, 2), (2, 3), (3, 5))),
        ("①1①", "111", ((0, 1), (1, 2), (2, 3))),
        ("ﬃffi", "ffiffi", ((0, 1),) * 3 + ((1, 2), (2, 3), (3, 4))),
        ("😀 \t花\r\n", "😀 花", ((0, 1), (1, 2), (3, 4))),
        ('"#▁', "”＃▂", ((0, 1), (1, 2), (2, 3))),
        ("각각", "각각", ((0, 3), (3, 4))),
    ],
)
def test_official_normalization_keeps_adjacent_original_character_ranges(text, normalized, spans):
    # Unicode NFKCの同値とKWJAの公開文字表から期待を導く。反復文字を遠隔対応しない。
    assert _normalized_source(text) == (normalized, spans)


def test_original_offsets_are_validated_without_rewriting_native_surface():
    analysis = make_analysis()
    text = "\r\n" + analysis.text + "\r\n"
    original = analysis.restore_doc()
    adjusted = adapt_doc(original, text)
    assert adjusted.text == text
    assert adjusted.snapshot == analysis.snapshot
    assert adjusted.offsets == tuple((a + 2, b + 2) for a, b in analysis.offsets)
    assert adjusted.restore_doc().text == original.text
    with pytest.raises(ValueError, match="位置"):
        replace(adjusted, offsets=analysis.offsets).restore_doc()
    with pytest.raises(ValueError, match="原文"):
        adapt_doc(original, "別の原文")


def test_native_pas_case_type_attachment_and_all_morphemes_are_preserved():
    analysis = make_analysis()
    document = analysis.restore_doc()
    query = analysis.relations[0].queries[0]
    assert query.dependency == "N-P" and query.noun_role == "noun"
    assert analysis.comparison.targets[0].dependency == "PAS:ヲ"
    assert analysis.comparison.targets[0].noun_role == "C"
    assert analysis.relations[0].view.target_edge == (2, 0, "PAS:ヲ")
    assert query.predicate.nodes[0].inflection == ("母音動詞", "基本形")
    assert all(node.head is None and node.dependency is None for node in query.predicate.nodes)
    assert len(document.morphemes) == 3
    assert analysis.relations[0].view.noun_case_nodes == (1,)
    assert query.noun == ("東/*",)


def test_unmarked_nominal_basic_phrase_does_not_become_a_predicate():
    original = make_analysis()
    document = original.restore_doc()
    del document.base_phrases[1].features["用言"]
    assert not adapt_doc(document, original.text).predicates
    assert not adapt_doc(document, original.text).relations


def test_empty_and_whitespace_have_no_candidates_without_losing_original_text():
    for text in ("", " \r\n\t"):
        analysis = adapt_doc(Document.from_knp(""), text)
        assert analysis.text == text and analysis.offsets == ()
        assert not analysis.relations


def test_original_eod_line_is_data_and_pipeline_is_closed_once():
    class Pipeline:
        calls = []
        closed = 0

        def run(self, text):
            self.calls.append(text)
            surface = text.replace("\n", "")
            return Document.from_knp(
                "# S-ID:input\n* -1D\n+ -1D <体言>\n"
                + f"{surface} * {surface} 名詞 6 普通名詞 1 * 0 * 0 <基本句-主辞>\nEOS\n"
            )

        def close(self):
            self.closed += 1

    instance = Parser.__new__(Parser)
    pipeline = instance._pipeline = Pipeline()
    instance._closed = False
    text = "本文\nEOD\n続き"
    analysis = instance.analyze(text)
    assert analysis.text == text
    assert analysis.restore_doc().text == "本文EOD続き"
    assert pipeline.calls == [text]
    instance.close()
    instance.close()
    assert pipeline.closed == 1
    with pytest.raises(RuntimeError, match="終了"):
        instance.analyze("本文")
    with pytest.raises(RuntimeError, match="終了"):
        list(instance.analyze_many(["本文"]))


def test_different_dependency_version_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "native_japanese.parser.version",
        lambda name: "別の版" if name == "kwja" else PARSER_VERSIONS[name],
    )
    with pytest.raises(RuntimeError, match="バージョン.*kwja"):
        Parser()


@pytest.mark.parametrize(
    "plain,causative",
    [("母が布を畳む。", "母が布を畳ませる。"), ("先生が字を書く。", "先生が字を書かせる。")],
)
def test_causative_requires_its_own_native_composition(parser, plain, causative):
    first, second = [parser.analyze(text) for text in (plain, causative)]
    p, q = [
        next(
            r.queries[0]
            for r in a.relations
            if r.view is not None and r.view.target_edge[2] == "PAS:ヲ"
        )
        for a in (first, second)
    ]
    assert p.noun == q.noun
    assert p.predicate != q.predicate
    assert (
        first.restore_doc().morphemes[first.predicates[0].graph.root].lemma
        == second.restore_doc().morphemes[second.predicates[0].graph.root].lemma
    )
    assert len(q.predicate.nodes) > len(p.predicate.nodes)


@pytest.mark.parametrize(
    "text",
    [
        "😀 説明にもっともらしさを与える。\r\n",
        "ＡＰＩを使う。",
        "ｶﾞﾗｽを洗う。",
        "か\u3099らすを洗う。",
    ],
)
def test_original_unicode_and_entire_native_result_roundtrip(parser, text):
    analysis = parser.analyze(text)
    assert analysis.text == text
    document = analysis.restore_doc()
    assert document.to_knp().encode("utf-8") == analysis.snapshot
    assert analysis.offsets == document_offsets(document, text)
    for relation in analysis.relations:
        for endpoint in (relation.predicate, relation.argument):
            if endpoint is not None:
                assert text[endpoint.ranges[0][0] : endpoint.ranges[-1][1]] == endpoint.text


def test_repeated_occurrences_keep_distinct_native_ids_and_original_ranges(parser):
    sentence = "本を読む。"
    analysis = parser.analyze(sentence * 2)
    rows = [
        r for r in analysis.relations if r.view is not None and r.view.target_edge[2] == "PAS:ヲ"
    ]
    assert len(rows) == 2 and rows[0].queries == rows[1].queries
    assert rows[0].view.noun.root != rows[1].view.noun.root
    assert rows[1].argument.ranges[0][0] == len(sentence)


def test_nominalized_clause_keeps_inner_predicate_and_outer_nominal_role(parser):
    analysis = parser.analyze("走るのが好きだ。")
    assert len(analysis.predicates) == 2
    outer = next(
        r for r in analysis.relations if r.view is not None and r.view.target_edge[2] == "PAS:ガ"
    )
    assert outer.queries[0].related_predicate is None
    assert "走る/はしる" in outer.queries[0].noun
    assert outer.view.noun_predicate_roots


def test_pas_relative_clause_excludes_the_evaluated_predicate_only_from_noun_features():
    document = Document.from_knp(
        "# S-ID:relative\n"
        "* 1D\n"
        '+ 1D <用言:動><rel type="ヲ" target="ライブラリ" sid="relative" id="1"/>\n'
        '使用 しよう 使用 名詞 6 サ変名詞 2 * 0 * 0 "代表表記:使用/しよう" <基本句-主辞>\n'
        'する する する 動詞 2 * 0 サ変動詞 16 基本形 2 "代表表記:する/する"\n'
        "* -1D\n"
        "+ -1D <体言>\n"
        'ライブラリ ライブラリ ライブラリ 名詞 6 普通名詞 1 * 0 * 0 "代表表記:ライブラリ/ライブラリ" <基本句-主辞>\n'
        "を を を 助詞 9 格助詞 1 * 0 * 0\n"
        "EOS\n"
    )
    analysis = adapt_doc(document, document.text)
    relation = analysis.relations[0]
    assert relation.queries[0].dependency == "N-P"
    assert relation.view.target_edge[2] == "PAS:ヲ"
    assert relation.queries[0].noun_role == "noun"
    assert analysis.comparison.targets[0].noun_role == "N"
    assert relation.view.noun.nodes == (0, 1, 2, 3)
    assert relation.view.noun_feature_nodes == (2,)
    assert relation.queries[0].noun == ("ライブラリ/ライブラリ",)
    assert not set(relation.view.predicate.nodes) & set(relation.view.noun_feature_nodes)


@pytest.mark.parametrize("prefix,reading", [("再", "さい"), ("高", "こう")])
@pytest.mark.parametrize("nominal", [False, True])
def test_native_prefix_phrase_edges_and_full_composition_survive_archive(prefix, reading, nominal):
    from native_japanese.examples import parse_predicate_key, predicate_key

    # 語法のgoldではなく、公開の品詞・文節所属・元基本句辺から決まる参照範囲の対照。
    knp = (
        "# S-ID:prefix\n"
        "* -1D\n"
        + ("+ 1D <体言>\n" if nominal else "+ 1D\n")
        + f'{prefix} {reading} {prefix} 接頭辞 13 名詞接頭辞 1 * 0 * 0 "代表表記:{prefix}/{reading}" <基本句-主辞>\n'
        "+ -1D <用言:判><体言>\n"
        '利用 りよう 利用 名詞 6 サ変名詞 2 * 0 * 0 "代表表記:利用/りよう" <基本句-主辞>\n'
        "EOS\n"
    )
    document = Document.from_knp(knp)
    analysis = adapt_doc(document, document.text)
    occurrence = analysis.predicates[0]
    assert occurrence.graph.nodes == (0, 1)
    assert occurrence.graph.root == 1
    assert occurrence.graph.edges == ((1, 0, "基本句:D"),)
    query = occurrence.query.predicate
    assert tuple(node.lemma for node in query.nodes) == (f"{prefix}/{reading}", "利用/りよう")
    assert query.nodes[0].head == 1 and query.nodes[0].dependency == "基本句:D"
    assert query.nodes[1].head is None
    assert parse_predicate_key(predicate_key(query)) == query
    restored = analysis.restore_doc()
    assert restored.base_phrases[0].parent == restored.base_phrases[1]
    assert all(relation.view is None for relation in analysis.relations)
    if nominal:
        assert analysis.comparison.targets[0].scope == "composition"
    bare = Document.from_knp(
        "# S-ID:bare\n* -1D\n+ -1D <用言:判><体言>\n"
        '利用 りよう 利用 名詞 6 サ変名詞 2 * 0 * 0 "代表表記:利用/りよう" <基本句-主辞>\nEOS\n'
    )
    assert adapt_doc(bare, bare.text).predicates[0].query.predicate != query


@pytest.mark.parametrize("separate_phrase,noun", [(True, False), (False, True)])
def test_prefix_view_does_not_absorb_other_phrases_or_ordinary_noun_units(separate_phrase, noun):
    first_phrase = "* 1D\n" if separate_phrase else "* -1D\n"
    second_phrase = "* -1D\n" if separate_phrase else ""
    morpheme = (
        "の の の 名詞 6 形式名詞 8 * 0 * 0 <基本句-主辞>\n"
        if noun
        else "再 さい 再 接頭辞 13 名詞接頭辞 1 * 0 * 0 <基本句-主辞>\n"
    )
    document = Document.from_knp(
        "# S-ID:counterexample\n"
        + first_phrase
        + "+ 1D\n"
        + morpheme
        + second_phrase
        + "+ -1D <用言:動>\n"
        + "読む よむ 読む 動詞 2 * 0 子音動詞マ行 9 基本形 2 <基本句-主辞>\nEOS\n"
    )
    analysis = adapt_doc(document, document.text)
    assert analysis.predicates[0].graph.nodes == (1,)
    assert analysis.predicates[0].graph.edges == ()


@pytest.mark.parametrize(
    "word,subpos,tail", [("に", "格助詞", ""), ("から", "格助詞", ""), ("など", "副助詞", "の")]
)
@pytest.mark.parametrize("pas", [False, True])
@pytest.mark.parametrize("nominal_feature", ["体言", "準体言"])
def test_native_nominal_head_is_not_removed_as_its_own_case_marker(
    word, subpos, tail, pas, nominal_feature
):
    # 共通契約: 原文・native認定を変更せず、体言主辞自身とその後の格標識を区別する。
    # 文頭で断片化した入力でも、名詞を補充したり認識済み対象を消したりしない。
    relation = '<rel type="時間" target="' + word + '" sid="particle-head" id="0"/>' if pas else ""
    document = Document.from_knp(
        f"# S-ID:particle-head\n* 1D\n+ 1D <{nominal_feature}>\n"
        + f'{word} {word} {word} 助詞 9 {subpos} 1 * 0 * 0 "代表表記:{word}/{word}" <基本句-主辞>\n'
        + ('の の の 助詞 9 接続助詞 3 * 0 * 0 "代表表記:の/の"\n' if tail else "")
        + "* -1D\n+ -1D <用言:動>"
        + relation
        + "\n"
        + '入団 にゅうだん 入団 名詞 6 サ変名詞 2 * 0 * 0 "代表表記:入団/にゅうだん" <基本句-主辞>\nEOS\n'
    )
    analysis = adapt_doc(document, document.text)
    original = analysis.snapshot
    analysis.validate_comparison()
    observed = analysis.relations[0]
    assert observed.queries[0].noun == (f"{word}/{word}",)
    assert observed.view.noun_feature_nodes == (0,)
    assert observed.view.noun_case_nodes == ((1,) if tail else ())
    assert observed.argument.text == word + tail
    assert observed.view.target_edge[2] == ("PAS:時間" if pas else "基本句:D")
    assert len(analysis.comparison.targets) == 1
    assert analysis.comparison.targets[0].scope == "relation"
    assert analysis.snapshot == original == analysis.restore_doc().to_knp().encode("utf-8")


@pytest.mark.parametrize(
    "length,expected", [(248, 256), (249, 256), (250, 512), (504, 512), (505, 512)]
)
def test_word_window_matches_official_loader_special_token_boundary(length, expected):
    from types import SimpleNamespace

    class Tokenizer:
        def __call__(self, tokens, **kwargs):
            assert kwargs == {"is_split_into_words": True, "padding": False, "truncation": False}
            return SimpleNamespace(input_ids=list(range(int(tokens[0]))))

    def document(*lengths):
        return SimpleNamespace(
            sentences=[
                SimpleNamespace(morphemes=[SimpleNamespace(text=str(size))]) for size in lengths
            ]
        )

    tokenizer = Tokenizer()
    assert _word_window(document(length), tokenizer, 256, 512, 7) == expected
    # 文書全体の長さではなく、元文境界の各文の長さで選ぶ。
    assert _word_window(document(249, 249, 249), tokenizer, 256, 512, 7) == 256
    assert [_word_window(document(size), tokenizer, 256, 512, 7) for size in (249, 250, 249)] == [
        256,
        512,
        256,
    ]
    with pytest.raises(ValueError, match="513 tokens.*512"):
        _word_window(document(506), tokenizer, 256, 512, 7)


def test_direct_pipeline_runs_char_once_and_restores_the_default_word_window(tmp_path):
    from threading import Lock
    from types import SimpleNamespace

    from native_japanese.parser import _KwjaPipeline

    original = make_analysis()
    native = original.restore_doc()
    state = SimpleNamespace(length=249)
    char_config, word_config = SimpleNamespace(texts=[]), SimpleNamespace(max_seq_length=256)
    destination = tmp_path / "char.jumanpp"

    class Char:
        calls = 0
        output = native.to_jumanpp()
        processors = [
            SimpleNamespace(
                module=SimpleNamespace(
                    hparams=SimpleNamespace(datamodule=SimpleNamespace(predict=char_config))
                ),
                destination=destination,
            )
        ]

        def refresh(self):
            pass

        def run(self, documents, *, interactive):
            assert interactive and [d.text for d in documents] == [original.text]
            assert char_config.texts == [original.text]
            self.calls += 1
            destination.write_text(self.output, encoding="utf-8")

    class Word:
        windows = []

        def refresh(self):
            pass

        def run(self, documents, *, interactive):
            assert interactive and [d.text for d in documents] == [original.text]
            self.windows.append(word_config.max_seq_length)
            return native.to_knp()

    pipeline = _KwjaPipeline.__new__(_KwjaPipeline)
    pipeline._lock = Lock()
    pipeline._char, pipeline._word = Char(), Word()
    pipeline._word_config = word_config
    pipeline._normalize_text = lambda text: text
    pipeline._tokenizer = lambda *args, **kwargs: SimpleNamespace(input_ids=range(state.length))
    pipeline._default_window, pipeline._encoder_window, pipeline._special_token_count = 256, 512, 7
    for length in (249, 250, 249):
        state.length = length
        assert pipeline.run(original.text).to_knp() == native.to_knp()
    assert pipeline._char.calls == 3
    assert pipeline._word.windows == [256, 512, 256]
    state.length = 506
    with pytest.raises(ValueError, match="513 tokens.*512"):
        pipeline.run(original.text)
    assert pipeline._char.calls == 4 and pipeline._word.windows == [256, 512, 256]
    pipeline._char.output = native.to_jumanpp().replace("東", "別")
    with pytest.raises(ValueError, match="原文"):
        pipeline.run(original.text)
    assert pipeline._char.calls == 5 and pipeline._word.windows == [256, 512, 256]


def test_failed_native_writer_is_discarded_before_the_next_input(monkeypatch):
    native = make_analysis()
    pipelines = []

    class Pipeline:
        def __init__(self):
            self.partial = []
            self.closed = False
            pipelines.append(self)

        def run(self, text):
            if len(pipelines) == 1:
                self.partial.append("未完了の文")
                raise RuntimeError("公式writerの途中失敗")
            assert not self.partial
            return native.restore_doc()

        def close(self):
            self.closed = True
            self.partial.clear()

    monkeypatch.setattr("native_japanese.parser._KwjaPipeline", Pipeline)
    parser = Parser()
    with pytest.raises(RuntimeError, match="途中失敗"):
        parser.analyze(native.text)
    assert len(pipelines) == 1 and pipelines[0].closed
    analysis = parser.analyze(native.text)
    assert len(pipelines) == 2 and analysis == native
    assert not pipelines[1].partial and not pipelines[1].closed
    parser.close()
    assert pipelines[1].closed
