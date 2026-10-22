"""配布CDBの公開形式を読み、alias逆引きと群の派生cacheを保持する。"""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from cdblib import Reader

from . import resources
from .models import Query


class FormatError(ValueError):
    """公開sourceと対応しない現物形式。推測で補わない。"""


def ipal_tokens(raw):
    """case_ipal.c:extract_ipal_str の区切り（ASCII slashは区切らない）。"""
    cursor = 0
    for match in re.finditer(r"[ ／，、＊]", raw):
        if cursor == len(raw) - 1 and raw[cursor] == "＊":
            return
        yield raw[cursor : match.start()]
        cursor = match.end()
    if cursor < len(raw):
        yield raw[cursor:]


def example(raw):
    """一般化属性・flag・実語IDを区別し、元tokenを保持する。"""
    if raw.startswith(("<TH:", "<NE:", "<CT:", "<CL:")):
        match = re.fullmatch(r"(<(?:TH|NE|CT|CL):[^:>]+>)(?::([0-9]+(?:\.[0-9]+)?))?", raw)
        if not match:
            raise FormatError(f"一般化属性の未対応形式: {raw!r}")
        return {
            "kind": "generalized",
            "raw": raw,
            "value": match[1],
            "frequency": float(match[2]) if match[2] else 1,
        }
    value, sep, freq = raw.partition(":")
    if sep and not re.fullmatch(r"[0-9]+", freq):
        raise FormatError(f"用例頻度の未対応形式: {raw!r}")
    result = {"raw": raw, "value": value, "frequency": int(freq) if sep else 1}
    if value == "<主体準>":  # _make_ipal_cframe_ex のflag。実語実績ではない。
        return {**result, "kind": "flag"}
    if not value:
        return {**result, "kind": "empty"}
    if not re.fullmatch(r"[0-9]+(?:[+?][0-9]+)*", value):
        raise FormatError(f"実語IDの未対応形式: {raw!r}")
    return {**result, "kind": "lexical_id"}


def slot_case(raw):
    """既知末尾注釈だけを外し、単一labelを解釈せず返す。複数格は未対応。"""
    label = raw
    if label.endswith(("*", "＠")):
        label = label[:-1]
    if label.endswith("＊"):
        label = label[:-1]
    return label if label and not re.search(r"[ ／，、]", label) else None


def record_fields(data):
    """ipal.c:write_data / get_ipal_frame のNUL4ヘッダ＋三項組。"""
    if not data.endswith(b"\0"):
        raise FormatError("record末尾のNUL欠損")
    fields = data[:-1].decode("utf-8", errors="strict").split("\0")
    if len(fields) < 4 or (len(fields) - 4) % 3:
        raise FormatError(f"NUL項目構造が不一致: {len(fields)}項目")
    if not fields[0] or ":" not in fields[0]:
        raise FormatError("frame IDが未対応形式")
    return fields[:4], [fields[i : i + 3] for i in range(4, len(fields), 3)]


@dataclass(frozen=True)
class Frame:
    """同じ原述語のrecord。型と明示和flagはheaderだけから読む。"""

    frame_id: str
    predicate_type: str
    is_sum: bool
    offset: int
    length: int
    sha256: str
    slots: tuple[tuple[str, str, str], ...]

    def units(self, case: str) -> tuple[str, ...]:
        # 完全ID列の重複だけを一票にし、未接続?はID列のまま残す。
        return tuple(
            dict.fromkeys(
                token["value"]
                for raw_slot, examples, _ in self.slots
                if slot_case(raw_slot) == case
                for raw in ipal_tokens(examples)
                if (token := example(raw))["kind"] == "lexical_id"
            )
        )


METHOD = "same-predicate-frame-direction-alias-mean-id-mean-v1"
TYPES = ("動", "形", "判", "名")


class Reference:
    """read-only CDBと版付きSQLite派生cache。同じinstanceを常駐照会に使う。"""

    def __init__(self, runtime: dict, cache_path: Path):
        self.runtime = runtime
        self.cache_path = Path(cache_path)
        self.directory = Path(runtime["prefix"]) / "share/knp/dict/ebcf"
        self._vectors = None

    def __enter__(self):
        self.stack = ExitStack()
        try:
            identity = self._identity()
            self.cf = self.stack.enter_context(Reader.from_file_path(str(self.directory / "cf.db")))
            self.mrph = self.stack.enter_context(
                Reader.from_file_path(str(self.directory / "mrph2id.db"))
            )
            self.fd = os.open(self.directory / "cf.dat", os.O_RDONLY)
            self.stack.callback(os.close, self.fd)
            self.size = os.fstat(self.fd).st_size
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache = sqlite3.connect(self.cache_path)
            self.stack.callback(self.cache.close)
            self.cache.execute("PRAGMA journal_mode=DELETE")
            with self.cache:
                self.cache.execute(
                    "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                previous = self.cache.execute(
                    "SELECT value FROM metadata WHERE key='identity'"
                ).fetchone()
                encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True)
                if previous and previous[0] != encoded:
                    raise ValueError("資源・モデル・方式identityの異なる派生cacheは混用できません")
                if not previous:
                    if self.cache.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name!='metadata'"
                    ).fetchone():
                        raise ValueError("identityのない既存DBを派生cacheとして使えません")
                    self.cache.execute("INSERT INTO metadata VALUES (?,?)", ("identity", encoded))
                self.cache.execute(
                    "CREATE TABLE IF NOT EXISTS aliases (raw_id BLOB NOT NULL, raw_alias BLOB NOT NULL)"
                )
                self.cache.execute("CREATE INDEX IF NOT EXISTS aliases_id ON aliases(raw_id)")
                self.cache.execute("""CREATE TABLE IF NOT EXISTS groups (
                    offset INTEGER NOT NULL, length INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    predicate_type TEXT NOT NULL, case_key TEXT NOT NULL,
                    mean BLOB, comparable INTEGER NOT NULL, total INTEGER NOT NULL, reason TEXT,
                    PRIMARY KEY(offset,length,sha256,predicate_type,case_key))""")
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def _identity(self):
        if self.runtime["contract"] != "knp-standard-cv-head-v1":
            raise ValueError("未対応の正規化契約です")
        source = {}
        records = {row["path"]: row for row in self.runtime["files"]}
        for name in ("cf.db", "cf.dat", "mrph2id.db"):
            relative = "share/knp/dict/ebcf/" + name
            row = records[relative]
            stat = (self.directory / name).stat()
            # 数GBのhash再計算はsetupの起動前検証へ委ねる。
            actual = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino}
            if actual != {key: row[key] for key in actual}:
                raise ValueError(f"辞書identity変更。起動前の検証が必要です: {name}")
            source[name] = {"sha256": row["sha256"], **actual}
        metadata = resources.metadata()
        model = next(item for item in metadata["assets"] if item["id"] == "fasttext-ja-mini")
        return {
            "schema": 1,
            "contract": self.runtime["contract"],
            "source": source,
            "model_sha256": model["sha256"],
            "vector_python": metadata["vector_python"],
            "method": METHOD,
            "vector_size": 300,
        }

    @property
    def prepared(self) -> bool:
        return (
            self.cache.execute("SELECT value FROM metadata WHERE key='aliases_ready'").fetchone()
            is not None
        )

    def prepare(self) -> dict:
        """全aliasを一回だけstream保存する。重複/復号不能も原bytesで残す。"""
        if self.prepared:
            row = self.cache.execute(
                "SELECT value FROM metadata WHERE key='alias_count'"
            ).fetchone()
            return {"aliases": int(row[0]), "reused": True}
        count = 0

        def rows():
            nonlocal count
            for raw_alias, raw_id in self.mrph.iteritems():
                count += 1
                yield raw_id, raw_alias

        with self.cache:
            # 失敗時はtransaction全体を取消し、未完了をreadyにしない。
            self.cache.execute("DELETE FROM aliases")
            self.cache.executemany("INSERT INTO aliases VALUES (?,?)", rows())
            self.cache.executemany(
                "INSERT INTO metadata VALUES (?,?)",
                [("aliases_ready", "1"), ("alias_count", str(count))],
            )
        return {"aliases": count, "reused": False}

    def aliases(self, identifier: str):
        """順序と重複を保つ。複合product用にID単位でだけmaterializeする。"""
        return tuple(
            row[0]
            for row in self.cache.execute(
                "SELECT raw_alias FROM aliases WHERE raw_id=? ORDER BY rowid",
                (identifier.encode("ascii"),),
            )
        )

    def candidates(self, noun: str) -> tuple[tuple[str, str | None], ...]:
        output = []
        for representation in noun.split("?"):
            ids = []
            for component in representation.split("+"):
                raw_id = self.mrph.get(component.encode()) if component else None
                if raw_id is not None and not re.fullmatch(rb"[0-9]+", raw_id):
                    raise FormatError("mrph2id値が数字IDではありません")
                ids.append(raw_id.decode("ascii") if raw_id is not None else None)
            output.append((representation, "+".join(ids) if None not in ids else None))
        return tuple(output)

    def frames(self, predicate: str) -> tuple[Frame, ...]:
        frames, seen = [], set()
        for value in self.cf.gets(predicate.encode()):
            for address in value.decode("ascii").split("/"):
                if not re.fullmatch(r"[0-9]+:[0-9]+", address):
                    raise FormatError("cf.dbのaddress形式が不正です")
                offset, length = map(int, address.split(":"))
                if (offset, length) in seen:
                    continue
                seen.add((offset, length))
                if length <= 0 or offset + length > self.size:
                    raise FormatError("cf.dbのaddressがcf.datの範囲外です")
                data = os.pread(self.fd, length, offset)
                if len(data) != length:
                    raise FormatError("cf.datのrecord読取りが短縮しました")
                header, slots = record_fields(data)
                key, type_suffix = header[0].split(":", 1)
                if key != predicate:
                    raise FormatError("frame IDの原述語が索引と不一致です")
                predicate_type = type_suffix[:1]
                if predicate_type not in TYPES:
                    raise FormatError("frameの用言型が不正です")
                frames.append(
                    Frame(
                        header[0],
                        predicate_type,
                        "和フレーム" in header[3].split(),
                        offset,
                        length,
                        hashlib.sha256(data).hexdigest(),
                        tuple(tuple(s) for s in slots),
                    )
                )
        return tuple(frames)

    def group(self, frame: Frame, case: str):
        return self.cache.execute(
            """SELECT mean,comparable,total,reason FROM groups
            WHERE offset=? AND length=? AND sha256=? AND predicate_type=? AND case_key=?""",
            (frame.offset, frame.length, frame.sha256, frame.predicate_type, case),
        ).fetchone()

    def save_group(self, frame: Frame, case: str, mean, comparable: int, total: int, reason):
        with self.cache:
            self.cache.execute(
                """INSERT INTO groups VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(offset,length,sha256,predicate_type,case_key) DO NOTHING""",
                (
                    frame.offset,
                    frame.length,
                    frame.sha256,
                    frame.predicate_type,
                    case,
                    mean.astype("<f8", copy=False).tobytes() if mean is not None else None,
                    comparable,
                    total,
                    reason,
                ),
            )

    @property
    def vectors(self):
        if self._vectors is None:
            from .support import Vectors

            self._vectors = Vectors(resources.load_fixed_vector(Path(self.runtime["assets_dir"])))
        return self._vectors

    def observed(self, query: Query):
        """候補全体の直接観測を、prepare/modelなしで先に確認する。"""
        from .support import observed_reference

        return observed_reference(self, query)

    def assess(self, query: Query):
        """直接観測を先に判定し、必要なときだけ群の方向へ比較する。"""
        from .support import assess_reference

        return assess_reference(self, query)
