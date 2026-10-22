"""出典付きの正規化用例を、原語の完全一致で追加する。"""

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import Analysis, Query

# DBの版1も、この正規化契約と対応する。契約変更時は同じDB版を再利用しない。
SCHEMA_VERSION = 1
NORMALIZATION_IDENTITY = "knp-standard-cv-head-v1"
QUERY_FIELDS = ("predicate", "predicate_type", "case", "noun")
SOURCE_FIELDS = ("source_id", "observation_id", "source_url")
FIELDS = {"schema_version", "normalization_identity", *QUERY_FIELDS, *SOURCE_FIELDS}
APPLICATION_ID = 0x4E4A4558


def validate_record(record: object) -> dict:
    """文字列を分解・補正せず、交換形式の契約だけを検査する。"""
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise ValueError("用例の項目が所定のJSONL形式と一致しません。")
    if type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("対応していない用例のschema_versionです。")
    if record["normalization_identity"] != NORMALIZATION_IDENTITY:
        raise ValueError("用例の正規化契約が一致しません。")
    if any(
        not isinstance(record[key], str) or not record[key].strip()
        for key in (*QUERY_FIELDS, *SOURCE_FIELDS)
    ):
        raise ValueError("用例の語と出典には空でない文字列が必要です。")
    return record


def extract_records(analysis: Analysis, source_id: str, source_url: str) -> Iterator[dict]:
    """既存解析が返した同じ出現の候補を、そのまま書き出す。"""
    digest = hashlib.sha256(analysis.text.encode("utf-8")).hexdigest()
    for relation in analysis.relations:
        occurrence = [
            digest,
            relation.predicate.ranges,
            relation.argument.ranges if relation.argument else None,
        ]
        observation_id = hashlib.sha256(
            json.dumps(occurrence, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for query in dict.fromkeys(relation.queries):
            record = {
                "schema_version": SCHEMA_VERSION,
                "normalization_identity": NORMALIZATION_IDENTITY,
                **{key: getattr(query, key) for key in QUERY_FIELDS},
                "source_id": source_id,
                "observation_id": observation_id,
                "source_url": source_url,
            }
            yield validate_record(record)


class Examples:
    """用法キーを一度だけ保存し、出典との対応を別表に保つ。"""

    def __init__(self, path: str | Path, *, writable: bool = False):
        self.writable = writable
        if writable:
            self.connection = sqlite3.connect(path)
        else:
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
        try:
            self._initialize(writable)
        except BaseException:
            self.connection.close()
            raise
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.row_factory = sqlite3.Row

    def _initialize(self, writable):
        application = self.connection.execute("PRAGMA application_id").fetchone()[0]
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        empty = not self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone()
        if writable and application == 0 and version == 0 and empty:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS usages (
                    id INTEGER PRIMARY KEY,
                    predicate TEXT NOT NULL,
                    predicate_type TEXT NOT NULL,
                    case_name TEXT NOT NULL,
                    noun TEXT NOT NULL,
                    UNIQUE(predicate, predicate_type, case_name, noun)
                );
                CREATE TABLE IF NOT EXISTS observations (
                    usage_id INTEGER NOT NULL REFERENCES usages(id),
                    source_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    PRIMARY KEY(usage_id, source_id, observation_id, source_url)
                ) WITHOUT ROWID;
            """)
            self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif application != APPLICATION_ID or version != SCHEMA_VERSION:
            raise ValueError("追加用例DBの種類または版が一致しません。")
        # 壊れたDBを要求処理まで持ち越さず、起動時に失敗させる。
        self.connection.execute(
            "SELECT id,predicate,predicate_type,case_name,noun FROM usages LIMIT 0"
        )
        self.connection.execute(
            "SELECT usage_id,source_id,observation_id,source_url FROM observations LIMIT 0"
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.connection.close()

    def import_jsonl(self, lines: Iterable[str]) -> dict:
        """全行を同じtransactionへ流し、不正な途中行では全件を戻す。"""
        if not self.writable:
            raise ValueError("この用例DBは読み取り専用です。")
        records = usages = observations = 0
        with self.connection:
            for line_number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    record = validate_record(json.loads(line))
                except (ValueError, TypeError) as error:
                    raise ValueError(f"用例JSONLの{line_number}行目: {error}") from error
                key = tuple(record[field] for field in QUERY_FIELDS)
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO usages(predicate,predicate_type,case_name,noun) "
                    "VALUES (?,?,?,?)",
                    key,
                )
                usages += cursor.rowcount
                usage_id = self.connection.execute(
                    "SELECT id FROM usages WHERE predicate=? AND predicate_type=? "
                    "AND case_name=? AND noun=?",
                    key,
                ).fetchone()[0]
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO observations VALUES (?,?,?,?)",
                    (usage_id, *(record[field] for field in SOURCE_FIELDS)),
                )
                observations += cursor.rowcount
                records += 1
        return {"records": records, "usages_added": usages, "observations_added": observations}

    def lookup(self, query: Query) -> tuple[dict, ...]:
        """存在確認と代表出典一件を返す。全出典はDB内に保持する。"""
        rows = self.connection.execute(
            "SELECT o.source_id,o.observation_id,o.source_url FROM usages u "
            "JOIN observations o ON o.usage_id=u.id "
            "WHERE u.predicate=? AND u.predicate_type=? AND u.case_name=? AND u.noun=? LIMIT 1",
            tuple(getattr(query, key) for key in QUERY_FIELDS),
        )
        return tuple(dict(row) for row in rows)
