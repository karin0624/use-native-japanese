"""小さい本物のCDB/SQLiteで、直接観測・派生cacheの再利用と分離を確かめる。"""

import hashlib
import sqlite3

import numpy as np
import pytest
from cdblib import Writer
from gensim.models import KeyedVectors

from native_japanese import caseframes
from native_japanese.models import Query


def make_runtime(tmp_path, records=None, aliases=None):
    directory = tmp_path / "share/knp/dict/ebcf"
    directory.mkdir(parents=True)
    records = records or [
        ("比べる/くらべる", "動1", "", [("ヲ", "1:100 2:1 2:8 4:1", "")]),
        ("比べる/くらべる", "形1", "", [("ヲ", "3:1", "")]),
        ("比べる/くらべる", "動0", "和フレーム", [("ヲ", "3:1", "")]),
    ]
    addresses = {}
    data = bytearray()
    for predicate, suffix, flags, slots in records:
        fields = [predicate + ":" + suffix, "", predicate, flags]
        for slot in slots:
            fields.extend(slot)
        payload = ("\0".join(fields) + "\0").encode()
        addresses.setdefault(predicate, []).append(f"{len(data)}:{len(payload)}")
        data.extend(payload)
    (directory / "cf.dat").write_bytes(data)
    with (directory / "cf.db").open("wb") as stream, Writer(stream) as writer:
        for predicate, values in addresses.items():
            writer.put(predicate.encode(), "/".join(values).encode())
    aliases = aliases or [
        (b"1", "東/ひがし".encode()),
        (b"1", "北/きた".encode()),
        (b"2", "西/にし".encode()),
        (b"3", "南/みなみ".encode()),
        (b"4", b"\xff"),
        (b"1", "東/ひがし".encode()),
    ]
    with (directory / "mrph2id.db").open("wb") as stream, Writer(stream) as writer:
        for identifier, alias in aliases:
            writer.put(alias, identifier)
    files = []
    for path in directory.iterdir():
        stat = path.stat()
        files.append(
            {
                "path": str(path.relative_to(tmp_path)),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "inode": stat.st_ino,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return {
        "prefix": str(tmp_path),
        "assets_dir": str(tmp_path / "assets"),
        "contract": "knp-standard-cv-head-v1",
        "files": files,
    }


def fixed_model():
    model = KeyedVectors(vector_size=300)
    values = np.zeros((5, 300), dtype=np.float32)
    values[0, :2] = (1, 0)
    values[1, :2] = (0, 1)
    values[2, :2] = (-1, 0)
    values[3, :2] = (0, -1)
    values[4, :2] = (1, 1)
    model.add_vectors(["東", "北", "西", "南", "入力"], values)
    return model


def test_prepare_preserves_duplicate_bytes_and_reuses_index(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    cache = tmp_path / "derived.sqlite"
    monkeypatch.setattr(caseframes.resources, "load_fixed_vector", lambda assets: fixed_model())
    with caseframes.Reference(runtime, cache) as reference:
        assert reference.prepare() == {"aliases": 6, "reused": False}
        assert reference.aliases("1") == tuple(
            a for a in ["東/ひがし".encode(), "北/きた".encode(), "東/ひがし".encode()]
        )
        assert reference.aliases("4") == (b"\xff",)
        result = reference.assess(Query("比べる/くらべる", "動", "ヲ", "入力/にゅうりょく"))
        assert not result.observed and result.score == pytest.approx(0, abs=1e-7)
        assert (result.comparable, result.total) == (2, 3)
        assert result.evidence[0]["frame_id"] == "比べる/くらべる:動1"
    with caseframes.Reference(runtime, cache) as reference:
        assert reference.prepare() == {"aliases": 6, "reused": True}
        # 再利用はCDB逆引き/alias列挙を呼ばずに同じ群を返す。
        monkeypatch.setattr(
            reference, "aliases", lambda value: pytest.fail("保存済み群を再構築した")
        )
        again = reference.assess(Query("比べる/くらべる", "動", "ヲ", "入力/にゅうりょく"))
        assert again == result
        assert reference.cache.execute("SELECT count(*) FROM groups").fetchone()[0] == 1


def test_direct_observation_needs_neither_prepare_nor_vectors(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    monkeypatch.setattr(
        caseframes.resources,
        "load_fixed_vector",
        lambda assets: pytest.fail("直接観測でmodel load"),
    )
    with caseframes.Reference(runtime, tmp_path / "derived.sqlite") as reference:
        query = Query("比べる/くらべる", "動", "ヲ", "南/みなみ")
        assert reference.observed(query).observed  # 明示和の直接観測も従来どおり保持。
        assert reference.assess(query).observed
        assert not reference.prepared
        with pytest.raises(RuntimeError, match="prepare"):
            reference.assess(Query("比べる/くらべる", "動", "ヲ", "入力/にゅうりょく"))


def test_type_and_case_keys_are_independent_and_no_sum_fallback(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    monkeypatch.setattr(caseframes.resources, "load_fixed_vector", lambda assets: fixed_model())
    with caseframes.Reference(runtime, tmp_path / "derived.sqlite") as reference:
        reference.prepare()
        first = reference.assess(Query("比べる/くらべる", "形", "ヲ", "入力/にゅうりょく"))
        second = reference.assess(Query("比べる/くらべる", "動", "ガ", "入力/にゅうりょく"))
        assert first.score == pytest.approx(-1 / np.sqrt(2))
        assert second.score is None and second.total == 0
        assert reference.cache.execute("SELECT count(*) FROM groups").fetchone()[0] == 2


def test_foreign_cache_and_invalid_dictionary_fail_explicitly(tmp_path):
    runtime = make_runtime(tmp_path)
    cache = tmp_path / "derived.sqlite"
    with caseframes.Reference(runtime, cache):
        pass
    wrong = dict(runtime, contract="別方式")
    with pytest.raises(ValueError, match="契約"):
        with caseframes.Reference(wrong, cache):
            pass
    with sqlite3.connect(cache) as db:
        db.execute("UPDATE metadata SET value='{}' WHERE key='identity'")
    with pytest.raises(ValueError, match="identity"):
        with caseframes.Reference(runtime, cache):
            pass
    directory = tmp_path / "share/knp/dict/ebcf"
    with (directory / "cf.db").open("wb") as stream, Writer(stream) as writer:
        writer.put("比べる/くらべる".encode(), b"9999999:30")
    row = next(r for r in runtime["files"] if r["path"].endswith("/cf.db"))
    stat = (directory / "cf.db").stat()
    row.update(bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
    with caseframes.Reference(runtime, tmp_path / "new.sqlite") as reference:
        with pytest.raises(caseframes.FormatError, match="範囲外"):
            reference.assess(Query("比べる/くらべる", "動", "ヲ", "東/ひがし"))


def test_all_candidate_and_ordinary_frame_maxima_are_retained(tmp_path, monkeypatch):
    records = [
        ("比べる/くらべる", "動2", "", [("ヲ", "3:1", "")]),
        ("比べる/くらべる", "動1", "", [("ヲ", "1:1 2:1", "")]),
    ]
    runtime = make_runtime(
        tmp_path,
        records,
        [
            (b"1", "東/ひがし".encode()),
            (b"2", "北/きた".encode()),
            (b"3", "南/みなみ".encode()),
        ],
    )
    monkeypatch.setattr(caseframes.resources, "load_fixed_vector", lambda assets: fixed_model())
    with caseframes.Reference(runtime, tmp_path / "derived.sqlite") as reference:
        reference.prepare()
        score = reference.assess(Query("比べる/くらべる", "動", "ヲ", "西/べつ?北/べつ"))
        assert score.score == pytest.approx(1 / np.sqrt(2))
        assert score.evidence[0]["frame_id"] == "比べる/くらべる:動1"
        assert score.evidence[0]["noun_candidate"] == "北/べつ"
        assert reference.cache.execute("SELECT count(*) FROM groups").fetchone()[0] == 2


def test_failed_alias_preparation_is_atomic(tmp_path):
    from types import SimpleNamespace

    runtime = make_runtime(tmp_path)
    with caseframes.Reference(runtime, tmp_path / "derived.sqlite") as reference:
        original = reference.mrph

        def broken():
            yield b"raw-alias", b"1"
            raise OSError("入力の読取り失敗")

        reference.mrph = SimpleNamespace(iteritems=broken)
        with pytest.raises(OSError, match="読取り失敗"):
            reference.prepare()
        assert not reference.prepared
        assert reference.cache.execute("SELECT count(*) FROM aliases").fetchone()[0] == 0
        reference.mrph = original
        assert reference.prepare() == {"aliases": 6, "reused": False}


def test_two_connections_can_save_the_same_unprepared_group(tmp_path):
    runtime = make_runtime(tmp_path)
    cache = tmp_path / "derived.sqlite"
    with (
        caseframes.Reference(runtime, cache) as first,
        caseframes.Reference(runtime, cache) as second,
    ):
        frame = first.frames("比べる/くらべる")[0]
        assert first.group(frame, "ヲ") is None
        assert second.group(frame, "ヲ") is None
        mean = np.zeros(300, dtype=np.float64)
        mean[0] = 1
        first.save_group(frame, "ヲ", mean, 2, 3, None)
        second.save_group(frame, "ヲ", mean, 2, 3, None)
        saved = first.group(frame, "ヲ")
        assert saved == second.group(frame, "ヲ")
        assert saved == (mean.astype("<f8").tobytes(), 2, 3, None)
        assert first.cache.execute("SELECT count(*) FROM groups").fetchone()[0] == 1
