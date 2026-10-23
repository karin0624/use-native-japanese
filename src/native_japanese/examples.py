"""出典付きのlemma列を、再解析せず正本SQLiteへ追加する。"""

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import NORMALIZATION_IDENTITY, Analysis, Query

SCHEMA_VERSION = 2
QUERY_FIELDS = ("predicate", "predicate_type", "dependency", "noun", "noun_type", "noun_role")
SOURCE_FIELDS = ("source_id", "observation_id", "source_url")
FIELDS = {"schema_version", "normalization_identity", *QUERY_FIELDS, *SOURCE_FIELDS}
APPLICATION_ID = 0x4E4A4558


def canonical(value) -> str:
    """区切りを含むlemmaも曖昧にならないJSONを共通のキーにする。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def query_key(query: Query) -> str:
    return canonical([NORMALIZATION_IDENTITY, *[getattr(query, key) for key in QUERY_FIELDS]])


def group_key(query: Query) -> str:
    return canonical(
        [NORMALIZATION_IDENTITY, *[getattr(query, key) for key in QUERY_FIELDS if key != "noun"]]
    )


def validate_record(record: object) -> dict:
    """lemmaを分解・補正せず、交換形式だけを検査する。"""
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise ValueError("用例の項目が所定のJSONL形式と一致しません。")
    if type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("対応していない用例のschema_versionです。")
    if record["normalization_identity"] != NORMALIZATION_IDENTITY:
        raise ValueError("用例の正規化契約が一致しません。")
    for key in ("predicate", "noun"):
        value = record[key]
        if (
            not isinstance(value, (list, tuple))
            or not value
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise ValueError("predicateとnounには空でないlemma配列が必要です。")
    for key in (*SOURCE_FIELDS, "predicate_type", "dependency", "noun_type", "noun_role"):
        if not isinstance(record[key], str) or not record[key].strip():
            raise ValueError("用例のラベルと出典には空でない文字列が必要です。")
    if (
        record["predicate_type"] not in ("VERB", "ADJ")
        or record["noun_type"] not in ("NOUN", "PROPN")
        or record["noun_role"] not in ("head", "dependent")
    ):
        raise ValueError("用例の品詞または元方向が契約と一致しません。")
    return record


def record_query(record: dict) -> Query:
    values = {key: record[key] for key in QUERY_FIELDS}
    values.update(predicate=tuple(values["predicate"]), noun=tuple(values["noun"]))
    return Query(**values)


def extract_records(analysis: Analysis, source_id: str, source_url: str) -> Iterator[dict]:
    """解析が返したlemma列と出現位置をそのまま書き出す。"""
    digest = hashlib.sha256(analysis.text.encode("utf-8")).hexdigest()
    for relation in analysis.relations:
        occurrence = [
            digest,
            relation.predicate.ranges,
            relation.argument.ranges if relation.argument else None,
        ]
        observation_id = hashlib.sha256(canonical(occurrence).encode("utf-8")).hexdigest()
        for query in dict.fromkeys(relation.queries):
            record = {
                "schema_version": SCHEMA_VERSION,
                "normalization_identity": NORMALIZATION_IDENTITY,
                **{
                    key: list(getattr(query, key))
                    if key in ("predicate", "noun")
                    else getattr(query, key)
                    for key in QUERY_FIELDS
                },
                "source_id": source_id,
                "observation_id": observation_id,
                "source_url": source_url,
            }
            yield validate_record(record)


class Examples:
    """用法を重複排除し、異なる全出典との対応を保持する正本。"""

    def __init__(self, path: str | Path, *, writable: bool = False):
        self.writable = writable
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        self.connection = sqlite3.connect(path) if writable else sqlite3.connect(uri, uri=True)
        try:
            self._initialize(writable)
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.row_factory = sqlite3.Row
        except BaseException:
            self.connection.close()
            raise

    def _initialize(self, writable):
        application = self.connection.execute("PRAGMA application_id").fetchone()[0]
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        empty = not self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone()
        if writable and application == 0 and version == 0 and empty:
            self.connection.executescript("""
                CREATE TABLE usages (
                    key TEXT PRIMARY KEY,
                    group_key TEXT NOT NULL,
                    noun TEXT NOT NULL,
                    UNIQUE(group_key, noun)
                ) WITHOUT ROWID;
                CREATE TABLE observations (
                    usage_key TEXT NOT NULL REFERENCES usages(key),
                    source_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    PRIMARY KEY(usage_key, source_id, observation_id, source_url)
                ) WITHOUT ROWID;
            """)
            self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif application != APPLICATION_ID or version != SCHEMA_VERSION:
            raise ValueError("用例DBの種類または版が一致しません。")
        self.connection.execute("SELECT key,group_key,noun FROM usages LIMIT 0")
        self.connection.execute(
            "SELECT usage_key,source_id,observation_id,source_url FROM observations LIMIT 0"
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
                query = record_query(record)
                key = query_key(query)
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO usages VALUES (?,?,?)",
                    (key, group_key(query), canonical(query.noun)),
                )
                usages += cursor.rowcount
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO observations VALUES (?,?,?,?)",
                    (key, *(record[field] for field in SOURCE_FIELDS)),
                )
                observations += cursor.rowcount
                records += 1
        return {"records": records, "usages_added": usages, "observations_added": observations}

    def lookup(self, query: Query) -> tuple[dict, ...]:
        """正確一致した用法の代表出典一件を返す。全出典はDB内に残す。"""
        rows = self.connection.execute(
            "SELECT source_id,observation_id,source_url FROM observations "
            "WHERE usage_key=? LIMIT 1",
            (query_key(query),),
        )
        return tuple(dict(row) for row in rows)

    def iter_usages(self) -> Iterator[Query]:
        """同じ群を連続させ、全distinct照会を逐次返す。"""
        for row in self.connection.execute("SELECT key FROM usages ORDER BY group_key,noun"):
            identity, *values = json.loads(row[0])
            if identity != NORMALIZATION_IDENTITY or len(values) != len(QUERY_FIELDS):
                raise ValueError("正本の照会契約が一致しません。")
            record = dict(zip(QUERY_FIELDS, values, strict=True))
            validate_record(
                {
                    "schema_version": SCHEMA_VERSION,
                    "normalization_identity": identity,
                    **record,
                    "source_id": "検証",
                    "observation_id": "検証",
                    "source_url": "検証",
                }
            )
            yield record_query(record)
