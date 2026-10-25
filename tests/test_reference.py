"""実SQLiteの構築・固定群比較・安全な配布読込を確認する。"""

import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest
from test_examples import lines, record
from test_support import vector_model

from native_japanese import reference
from native_japanese.examples import Examples, extract_records, group_key
from native_japanese.models import Query
from native_japanese.parser import Parser

QUERY = Query(("比べる",), "VERB", "obj", ("東",), "NOUN", "dependent")


@pytest.fixture
def setup_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(
        reference.resources, "load_fixed_vector", lambda assets: vector_model().model
    )
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(
            lines(
                record(),
                record(replace(QUERY, noun=("北",))),
                record(replace(QUERY, noun=("未収録",))),
            )
        )
    return source, output, tmp_path


def test_compile_indexed_and_generalization_without_archive(setup_reference, monkeypatch):
    source, output, assets = setup_reference
    result = reference.build_reference(source, output, assets)
    assert (result["usages"], result["groups"], result["comparable"]) == (3, 1, 2)
    assert result["bytes"] == output.stat().st_size
    assert result["membership_bytes"] > 0
    # 配布済みDBに保存された旧文言も、DBを更新せずに読みやすく返す。
    with sqlite3.connect(output) as db:
        db.execute("UPDATE groups SET reason=?", ("群内の一部名詞列は比較不能です",))
    source.unlink()
    with reference.Reference(output, assets) as runtime:
        # 直接の索引陽性には、検査時にvector資源を開く必要がない。
        monkeypatch.setattr(
            reference.resources, "load_fixed_vector", lambda _: pytest.fail("不要load")
        )
        direct = runtime.assess(QUERY)
        assert direct.indexed and direct.score is None
        assert direct.evidence[0]["source"] == "compiled-reference"
        assert "observation_id" not in direct.evidence[0]
        monkeypatch.setattr(
            reference.resources, "load_fixed_vector", lambda _: vector_model().model
        )
        support = runtime.assess(replace(QUERY, noun=("西",)))
        assert not support.indexed and support.score == pytest.approx(-1 / np.sqrt(2))
        assert (support.comparable, support.total) == (2, 3)
        assert support.reason == "収録用例の一部は比較に使えません。"
        unavailable = runtime.assess(replace(QUERY, noun=("零",)))
        assert unavailable.score is None
        assert unavailable.reason == (
            support.reason + "「零」を比較するための情報が、付属の単語辞書にありません。"
        )
        assert runtime.connection.execute("SELECT reason FROM groups").fetchone()[0] == (
            "群内の一部名詞列は比較不能です"
        )


def test_every_non_noun_condition_stays_fixed(setup_reference):
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    with reference.Reference(output, assets) as runtime:
        for changed in (
            {"predicate": ("較べる",)},
            {"predicate_type": "ADJ"},
            {"dependency": "nsubj"},
            {"noun_type": "PROPN"},
            {"noun_role": "head"},
        ):
            support = runtime.assess(replace(QUERY, noun=("西",), **changed))
            assert not support.indexed and support.score is None and support.total == 0
            assert "この使い方の比較に必要な用例が、辞書に収録されていません" in support.reason


def test_additions_change_mean_but_repeated_sources_do_not(setup_reference):
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    probe = replace(QUERY, noun=("西", "西"))
    with reference.Reference(output, assets) as runtime:
        before = runtime.assess(probe)
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(*(record(source=f"重複{i}") for i in range(20))))
    reference.build_reference(source, output, assets)
    with reference.Reference(output, assets) as runtime:
        assert runtime.assess(probe) == before
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(record(replace(QUERY, noun=("西",)))))
    reference.build_reference(source, output, assets)
    with reference.Reference(output, assets) as runtime:
        after = runtime.assess(probe)
        assert after.score == pytest.approx(0, abs=1e-14)
        assert (after.comparable, after.total) == (3, 4)
        assert runtime.assess(replace(QUERY, noun=("西",))).indexed


def test_empty_and_zero_mean_are_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True):
        pass
    assert reference.build_reference(source, output, tmp_path)["usages"] == 0
    with reference.Reference(output, tmp_path) as runtime:
        assert runtime.assess(QUERY).score is None
        assert runtime.identity["groups"] == 0
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(lines(record(), record(replace(QUERY, noun=("西",)))))
    reference.build_reference(source, output, tmp_path)
    with sqlite3.connect(output) as db:
        db.execute("UPDATE groups SET reason=?", ("群の平均が厳密にゼロです",))
    with reference.Reference(output, tmp_path) as runtime:
        support = runtime.assess(replace(QUERY, noun=("北",)))
        assert support.score is None and "ゼロ" in support.reason
        assert "群の平均" not in support.reason


def test_failed_build_preserves_prior_output_and_never_overwrites_archive(
    setup_reference, monkeypatch
):
    source, output, assets = setup_reference
    with pytest.raises(ValueError, match="上書き"):
        reference.build_reference(source, source, assets)
    reference.build_reference(source, output, assets)
    previous = output.read_bytes()
    monkeypatch.setattr(
        reference.resources, "load_fixed_vector", lambda _: (_ for _ in ()).throw(OSError("失敗"))
    )
    with pytest.raises(OSError, match="失敗"):
        reference.build_reference(source, output, assets)
    assert output.read_bytes() == previous
    assert not list(output.parent.glob("*.part"))
    with Examples(source) as examples:
        assert len(list(examples.iter_usages())) == 3


@pytest.mark.parametrize(
    "damage", ["identity", "nan", "dimension", "counts", "missing", "bloom", "nonfinite_reason"]
)
def test_corrupt_reference_is_rejected(setup_reference, damage):
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    with sqlite3.connect(output) as db:
        if damage == "identity":
            identity = json.loads(
                db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]
            )
            identity["normalization_identity"] = "別契約"
            db.execute("UPDATE metadata SET value=? WHERE key='identity'", (json.dumps(identity),))
        elif damage == "nan":
            db.execute("UPDATE groups SET mean=?", (np.full(300, np.nan).astype("<f8").tobytes(),))
        elif damage == "dimension":
            db.execute("UPDATE groups SET mean=?", (b"short",))
        elif damage == "counts":
            db.execute("UPDATE groups SET comparable=total+1")
        elif damage == "missing":
            db.execute("DELETE FROM groups WHERE key=?", (group_key(QUERY),))
        elif damage == "nonfinite_reason":
            # 古いDBで計算異常が理由だけに保存されていても、正常な不足へ変えない。
            db.execute(
                "UPDATE groups SET mean=NULL, comparable=0, reason=?", ("群の平均が非有限です",)
            )
        else:
            db.execute("UPDATE membership SET payload=zeroblob(length(payload))")
    with pytest.raises(ValueError):
        reference.Reference(output, assets)


def test_public_phrase_whitespace_lemma_survives_the_reference_pipeline(tmp_path, monkeypatch):
    # 公開phraseが含む改行lemmaを削除せず、解析から配布照会まで同じキーで保持する。
    text = "重複を畳む。\n重複を畳む。"
    parser = Parser()
    try:
        analysis = parser.analyze(text)
    finally:
        parser.close()
    queries = [query for relation in analysis.relations for query in relation.queries]
    assert any("\n" in query.noun for query in queries)
    source, output = tmp_path / "正本.sqlite", tmp_path / "配布.sqlite"
    with Examples(source, writable=True) as examples:
        examples.import_jsonl(
            lines(*extract_records(analysis, "改行を含む出典", "https://example.test"))
        )
        assert set(examples.iter_usages()) == set(queries)
    monkeypatch.setattr(reference.resources, "load_fixed_vector", lambda _: vector_model().model)
    reference.build_reference(source, output, tmp_path)
    with reference.Reference(output, tmp_path) as runtime:
        assert all(runtime.assess(query).indexed for query in queries)
