"""公開出力の小さい人工fixtureで、接続・位置・不能を確かめる。"""

from functools import partial

import pytest
from rhoknp import Document, Sentence

from native_japanese.models import Query
from native_japanese.parser import (
    LiteralSenter,
    Parser,
    adapt_sentence,
    original_ranges,
    position_map,
    predicate_candidates,
)


def native_sentence(
    sid="native-0", warning="", noun_feature=True, malformed=False, symbols="", surface="重複"
):
    """語の正誤や言語性能を検証するためのfixtureではない。"""
    noun = "<主辞代表表記:重複/ちょうふく>" if noun_feature else ""
    case = "不正な格解析" if malformed else f"畳む/たたむ:動1:ヲ/C/重複/0/0/{sid}"
    return Sentence.from_knp(
        f"# S-ID:{sid}{' ' + warning if warning else ''}\n"
        "* 1D\n"
        f"+ 1D {noun}\n"
        f'{surface} ちょうふく 重複 名詞 6 サ変名詞 2 * 0 * 0 "代表表記:重複/ちょうふく"\n'
        f"{symbols}"
        'を を を 助詞 9 格助詞 1 * 0 * 0 "代表表記:を/を"\n'
        "* -1D\n"
        "+ -1D <用言:動><用言代表表記:畳む/たたむ><正規化代表表記:畳む/たたむ>"
        f"<非CV用言見出候補:畳む/たたむ><格解析結果:{case}>\n"
        '畳む たたむ 畳む 動詞 2 * 0 子音動詞マ行 9 基本形 2 "代表表記:畳む/たたむ"\n'
        '。 。 。 特殊 1 句点 1 * 0 * 0 "代表表記:。/。"\n'
        "EOS\n"
    )


def install_processors(
    monkeypatch, *, warnings=(), failure=None, morphology=native_sentence, parsed=native_sentence
):
    instances = {"jumanpp": [], "knp": []}

    class Morphology:
        def __init__(self, **kwargs):
            self.calls = 0
            instances["jumanpp"].append(self)

        def is_available(self):
            return True

        def apply_to_sentence(self, sentence, timeout):
            self.calls += 1
            return Sentence.from_jumanpp(morphology(sentence.sid).to_jumanpp())

    class CaseParser:
        def __init__(self, **kwargs):
            self.calls = 0
            instances["knp"].append(self)

        def is_available(self):
            return True

        def apply_to_sentence(self, sentence, timeout):
            self.calls += 1
            if failure is not None:
                raise failure
            warning = warnings[self.calls - 1] if self.calls <= len(warnings) else ""
            return parsed(sentence.sid, warning=warning)

    monkeypatch.setattr("native_japanese.parser.Jumanpp", Morphology)
    monkeypatch.setattr("native_japanese.parser.KNP", CaseParser)
    return instances


def test_literal_and_codepoint_positions():
    text = "  # 本文です。\n🍣 次です。\n"
    document = LiteralSenter().apply_to_document(Document(text=text))
    assert [sentence.text for sentence in document.sentences] == ["# 本文です。", "🍣 次です。"]
    mapping = position_map(document.text, text)
    emoji = document.text.index("🍣")
    assert original_ranges((emoji, emoji + 1), mapping) == (
        (text.index("🍣"), text.index("🍣") + 1),
    )
    assert original_ranges((0, len(document.text)), mapping) == ((2, 9), (10, 16))
    with pytest.raises(ValueError, match="非空白"):
        position_map("本文", "元の本文")
    with pytest.raises(ValueError, match="全被覆"):
        position_map("本文です", "本文")


def test_same_occurrence_candidate_contract():
    features = {
        "用言": "動",
        "用言代表表記": "全体/ぜんたい?別候補/べつこうほ",
        "正規化代表表記": "基底/きてい",
        "標準用言代表表記": "全体/ぜんたい",
        "非CV用言見出候補": "基底/きてい",
    }
    assert predicate_candidates(features) == ("全体/ぜんたい", "別候補/べつこうほ", "基底/きてい")
    assert predicate_candidates({**features, "態": "受動"}) == (
        "全体/ぜんたい",
        "別候補/べつこうほ",
    )
    assert predicate_candidates({**features, "非CV用言見出候補": "別語/べつご"}) == (
        "全体/ぜんたい",
        "別候補/べつこうほ",
    )
    assert predicate_candidates({"非用言格解析": "動", "正規化代表表記": "紹介/しょうかい"}) == (
        "紹介/しょうかい",
    )


def test_public_pas_preserves_endpoints_and_query():
    text = "  重複を\n畳む。"
    relations, unavailable = adapt_sentence(
        native_sentence(), text, position_map("重複を畳む。", text)
    )
    assert not unavailable
    assert len(relations) == 1
    relation = relations[0]
    assert relation.queries == (Query("畳む/たたむ", "動", "ヲ", "重複/ちょうふく"),)
    assert relation.argument.text == "重複を"
    assert relation.argument.ranges == ((2, 5),)
    assert relation.predicate.text == "畳む。"
    assert relation.predicate.ranges == ((6, 9),)
    assert ("非CV用言見出候補", "畳む/たたむ") in relation.representations


def test_missing_public_noun_is_not_fabricated():
    text = "重複を畳む。"
    relations, _ = adapt_sentence(
        native_sentence(noun_feature=False), text, tuple(range(len(text)))
    )
    assert not relations[0].queries
    assert relations[0].argument.text == "重複を"
    assert "元項の公開表記" in relations[0].unavailable_reasons[0]


def test_fallback_keeps_other_sentences_and_reuses_processors(monkeypatch):
    instances = install_processors(
        monkeypatch, warnings=("WARNING:Fell back to dependency analysis",)
    )
    parser = Parser({"prefix": "/人工runtime", "options": ["-case", "-tab"]})
    text = "重複を畳む。\n重複を畳む。"
    result = parser.analyze(text)
    assert len(result.relations) == 1
    assert result.relations[0].argument.ranges == ((7, 10),)
    assert len(result.unavailable) == 1
    assert result.unavailable[0].ranges == ((0, 6),)
    assert "fallback" in result.unavailable[0].reason
    assert parser.analyze("重複を畳む。").relations
    assert parser.analyze(" \n ").relations == ()
    assert len(instances["jumanpp"]) == len(instances["knp"]) == 1
    assert instances["knp"][0].calls == 3
    parser.close()
    parser.close()
    with pytest.raises(RuntimeError, match="終了"):
        parser.analyze("重複を畳む。")


def test_native_morpheme_merge_preserves_surface_and_original_endpoints(monkeypatch):
    symbols = '" " " 特殊 1 記号 5 * 0 * 0 NIL\n） ） ） 特殊 1 括弧終 4 * 0 * 0 NIL\n'
    merged = (
        '"） "） "） 特殊 1 記号 5 * 0 * 0 NIL '
        '<形態素連結-顔文字><顔文字:自動認識>\n'
    )
    morphology = partial(native_sentence, symbols=symbols)
    parsed = partial(native_sentence, symbols=merged)
    assert morphology().text == parsed().text == '重複"）を畳む。'
    assert len(morphology().morphemes) == len(parsed().morphemes) + 1
    install_processors(monkeypatch, morphology=morphology, parsed=parsed)
    parser = Parser({"prefix": "/人工runtime", "options": ["-case", "-tab"]})
    text = '  重複"）を 畳む。\n'
    result = parser.analyze(text)
    assert not result.unavailable
    assert len(result.relations) == 1
    relation = result.relations[0]
    assert relation.queries == (Query("畳む/たたむ", "動", "ヲ", "重複/ちょうふく"),)
    assert relation.argument.text == '重複"）を'
    assert relation.argument.ranges == ((2, 7),)
    assert relation.predicate.text == "畳む。"
    assert relation.predicate.ranges == ((8, 11),)
    for endpoint in (relation.argument, relation.predicate):
        assert "".join(text[start:end] for start, end in endpoint.ranges) == endpoint.text


@pytest.mark.parametrize(
    ("sid", "surface"),
    [
        ("native-1", "重複"),
        ("native-0", "複"),
        ("native-0", "複重"),
        ("native-0", "重複\u3000"),
    ],
)
def test_native_sentence_id_and_surface_changes_are_errors(monkeypatch, sid, surface):
    def parsed(_sid, warning):
        return native_sentence(sid, warning=warning, surface=surface)

    install_processors(monkeypatch, parsed=parsed)
    parser = Parser({"prefix": "/人工runtime", "options": ["-case", "-tab"]})
    with pytest.raises(ValueError, match="文ID・本文表層"):
        parser.analyze("重複を畳む。")


@pytest.mark.parametrize("failure", [TimeoutError("期限超過"), RuntimeError("異常終了")])
def test_processor_failure_is_not_unobserved(monkeypatch, failure):
    install_processors(monkeypatch, failure=failure)
    parser = Parser({"prefix": "/人工runtime", "options": ["-case", "-tab"]})
    with pytest.raises(type(failure), match=str(failure)):
        parser.analyze("重複を畳む。")


def test_broken_public_pas_and_unknown_warning_are_errors():
    text = "重複を畳む。"
    for sentence in (
        native_sentence(malformed=True),
        native_sentence(warning="WARNING:未知の警告"),
    ):
        with pytest.raises(ValueError):
            adapt_sentence(sentence, text, tuple(range(len(text))))
