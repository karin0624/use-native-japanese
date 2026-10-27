"""全文と公開Docの正本を保持し、静的viewの索引を構築する。"""

import base64
import binascii
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import (
    NORMALIZATION_IDENTITY,
    PARSER_VERSIONS,
    Analysis,
    PredicateGraph,
    PredicateNode,
    Query,
)

SCHEMA_VERSION = 3
APPLICATION_ID = 0x4E4A4558
SOURCE_FIELDS = ("source_id", "observation_id", "source_url")
FIELDS = {
    "schema_version",
    "normalization_identity",
    "parser_versions",
    "text",
    "doc_base64",
    *SOURCE_FIELDS,
}
_REEXTRACT = "旧形式や異なる解析契約の用例は原文から再抽出してください。"


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def predicate_value(predicate: PredicateGraph) -> list:
    return [
        predicate.root,
        [[n.lemma, n.xpos, list(n.inflection), n.head, n.dependency] for n in predicate.nodes],
    ]


def parse_predicate(value) -> PredicateGraph:
    error = "述語構成の形式または元依存木が不正です。"
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(error)
    root, nodes = value
    if (
        type(root) is not int
        or not isinstance(nodes, list)
        or not nodes
        or not 0 <= root < len(nodes)
    ):
        raise ValueError(error)
    parsed = []
    for i, node in enumerate(nodes):
        if not isinstance(node, list) or len(node) != 5:
            raise ValueError(error)
        lemma, xpos, inflection, head, dep = node
        if (
            not isinstance(lemma, str)
            or not lemma
            or not isinstance(xpos, str)
            or not isinstance(inflection, list)
            or any(not isinstance(x, str) or not x for x in inflection)
        ):
            raise ValueError(error)
        if i == root:
            if head is not None or dep is not None:
                raise ValueError(error)
        elif (
            type(head) is not int
            or not 0 <= head < len(nodes)
            or head == i
            or not isinstance(dep, str)
            or not dep.strip()
        ):
            raise ValueError(error)
        parsed.append(PredicateNode(lemma, xpos, tuple(inflection), head, dep))
    for i in range(len(nodes)):
        visited = set()
        while i != root:
            if i in visited:
                raise ValueError(error)
            visited.add(i)
            i = parsed[i].head
    return PredicateGraph(root, tuple(parsed))


def predicate_key(predicate: PredicateGraph) -> str:
    return canonical([NORMALIZATION_IDENTITY, predicate_value(predicate)])


def parse_predicate_key(key: str) -> PredicateGraph:
    value = json.loads(key)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or value[0] != NORMALIZATION_IDENTITY
        or canonical(value) != key
    ):
        raise ValueError("述語索引の解析契約が不正です。" + _REEXTRACT)
    return parse_predicate(value[1])


def query_key(query: Query) -> str:
    return canonical(
        [
            NORMALIZATION_IDENTITY,
            predicate_value(query.predicate),
            query.attachment,
            query.dependency,
            query.noun,
            query.noun_role,
        ]
    )


def group_key(query: Query) -> str:
    return canonical(
        [
            NORMALIZATION_IDENTITY,
            predicate_value(query.predicate),
            query.attachment,
            query.dependency,
            query.noun_role,
        ]
    )


def parse_query_key(key: str, *, group=False) -> Query:
    value = json.loads(key)
    if (
        not isinstance(value, list)
        or len(value) != (5 if group else 6)
        or value[0] != NORMALIZATION_IDENTITY
        or canonical(value) != key
    ):
        raise ValueError("照会索引の解析契約が不正です。" + _REEXTRACT)
    predicate = parse_predicate(value[1])
    attachment, dep = value[2:4]
    noun, role = ([], value[4]) if group else (value[4], value[5])
    if (
        type(attachment) is not int
        or not 0 <= attachment < len(predicate.nodes)
        or not isinstance(dep, str)
        or not dep.strip()
        or role not in {"head", "dependent"}
        or not isinstance(noun, list)
        or any(not isinstance(x, str) or not x for x in noun)
    ):
        raise ValueError("関係照会の形式が不正です。")
    return Query(predicate, attachment, dep, tuple(noun), role)


def validate_record(record: object) -> dict:
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise ValueError("完全な原解析を持つ文書JSONLが必要です。" + _REEXTRACT)
    if (
        type(record["schema_version"]) is not int
        or record["schema_version"] != SCHEMA_VERSION
        or record["normalization_identity"] != NORMALIZATION_IDENTITY
        or record["parser_versions"] != PARSER_VERSIONS
    ):
        raise ValueError("用例の解析契約が一致しません。" + _REEXTRACT)
    for field in SOURCE_FIELDS:
        if not isinstance(record[field], str) or not record[field].strip():
            raise ValueError("出典ID・出現ID・出典URLには空でない文字列が必要です。")
    if not isinstance(record["text"], str) or not isinstance(record["doc_base64"], str):
        raise ValueError("文書の原文と原解析の形式が不正です。")
    try:
        raw = base64.b64decode(record["doc_base64"], validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("原解析のbase64形式が不正です。") from error
    if not raw:
        raise ValueError("原解析snapshotがありません。")
    return record


def analysis_from_record(record: dict) -> Analysis:
    validate_record(record)
    analysis = Analysis(record["text"], base64.b64decode(record["doc_base64"], validate=True))
    analysis.restore_doc()
    return analysis


def extract_records(analysis: Analysis, source_id: str, source_url: str) -> Iterator[dict]:
    yield validate_record(
        {
            "schema_version": SCHEMA_VERSION,
            "normalization_identity": NORMALIZATION_IDENTITY,
            "parser_versions": PARSER_VERSIONS,
            "text": analysis.text,
            "doc_base64": base64.b64encode(analysis.snapshot).decode("ascii"),
            "source_id": source_id,
            "observation_id": hashlib.sha256(analysis.text.encode("utf-8")).hexdigest(),
            "source_url": source_url,
        }
    )


class Examples:
    """文書の完全正本・全出典と、再構築可能な用法索引を保持する。"""

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
        if writable and application == version == 0 and empty:
            self.connection.executescript("""
                CREATE TABLE documents (id TEXT PRIMARY KEY, text TEXT NOT NULL, snapshot BLOB NOT NULL) WITHOUT ROWID;
                CREATE TABLE observations (
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    source_id TEXT NOT NULL, observation_id TEXT NOT NULL, source_url TEXT NOT NULL,
                    PRIMARY KEY(document_id, source_id, observation_id, source_url)
                ) WITHOUT ROWID;
                CREATE TABLE predicates (key TEXT PRIMARY KEY) WITHOUT ROWID;
                CREATE TABLE usages (key TEXT PRIMARY KEY, group_key TEXT NOT NULL, noun TEXT NOT NULL) WITHOUT ROWID;
                CREATE INDEX usages_by_group ON usages(group_key, noun);
                CREATE TABLE occurrences (
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    usage_key TEXT NOT NULL REFERENCES usages(key),
                    predicate_attachment INTEGER NOT NULL, noun_head INTEGER NOT NULL,
                    target_child INTEGER NOT NULL, PRIMARY KEY(document_id, target_child)
                ) WITHOUT ROWID;
                CREATE INDEX occurrences_by_usage ON occurrences(usage_key);
            """)
            self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif application != APPLICATION_ID or version != SCHEMA_VERSION:
            raise ValueError("完全正本DBの種類または版が一致しません。" + _REEXTRACT)
        self.connection.execute("SELECT id,text,snapshot FROM documents LIMIT 0")
        self.connection.execute("SELECT key FROM predicates LIMIT 0")
        self.connection.execute("SELECT key,group_key,noun FROM usages LIMIT 0")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.connection.close()

    def import_jsonl(self, lines: Iterable[str]) -> dict:
        if not self.writable:
            raise ValueError("正本DBは読み取り専用です。")
        counts = dict.fromkeys(
            (
                "records",
                "documents_added",
                "predicates_added",
                "usages_added",
                "observations_added",
            ),
            0,
        )
        with self.connection:
            for number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    record = validate_record(json.loads(line))
                    analysis = analysis_from_record(record)
                    comparison = analysis.comparison
                except (ValueError, TypeError, KeyError, binascii.Error) as error:
                    raise ValueError(f"用例JSONLの{number}行目: {error}") from error
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO documents VALUES (?,?,?)",
                    (analysis.document_id, analysis.text, analysis.snapshot),
                )
                counts["documents_added"] += cursor.rowcount
                for predicate in comparison.predicates:
                    cursor = self.connection.execute(
                        "INSERT OR IGNORE INTO predicates VALUES (?)",
                        (predicate_key(predicate.query.predicate),),
                    )
                    counts["predicates_added"] += cursor.rowcount
                for relation in comparison.relations:
                    if relation.view is None:
                        continue
                    query = relation.queries[0]
                    key = query_key(query)
                    cursor = self.connection.execute(
                        "INSERT OR IGNORE INTO usages VALUES (?,?,?)",
                        (key, group_key(query), canonical(query.noun)),
                    )
                    counts["usages_added"] += cursor.rowcount
                    view = relation.view
                    self.connection.execute(
                        "INSERT OR IGNORE INTO occurrences VALUES (?,?,?,?,?)",
                        (
                            analysis.document_id,
                            key,
                            view.predicate_attachment,
                            view.noun.root,
                            view.target_edge[1],
                        ),
                    )
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO observations VALUES (?,?,?,?)",
                    (analysis.document_id, *(record[field] for field in SOURCE_FIELDS)),
                )
                counts["observations_added"] += cursor.rowcount
                counts["records"] += 1
        return counts

    def lookup(self, query: Query) -> tuple[dict, ...]:
        rows = self.connection.execute(
            "SELECT o.*,c.predicate_attachment,c.noun_head,c.target_child FROM occurrences c "
            "JOIN observations o ON o.document_id=c.document_id WHERE c.usage_key=? LIMIT 1",
            (query_key(query),),
        )
        return tuple(dict(row) for row in rows)

    def iter_usages(self) -> Iterator[Query]:
        for row in self.connection.execute("SELECT key FROM usages ORDER BY group_key,noun"):
            yield parse_query_key(row[0])

    def iter_predicates(self) -> Iterator[PredicateGraph]:
        for row in self.connection.execute("SELECT key FROM predicates ORDER BY key"):
            yield parse_predicate_key(row[0])

    def iter_records(self) -> Iterator[dict]:
        for row in self.connection.execute(
            "SELECT d.text,d.snapshot,o.source_id,o.observation_id,o.source_url FROM documents d "
            "JOIN observations o ON o.document_id=d.id ORDER BY d.id,o.source_id,o.observation_id,o.source_url"
        ):
            record = next(
                extract_records(
                    Analysis(row["text"], row["snapshot"]), row["source_id"], row["source_url"]
                )
            )
            yield {**record, "observation_id": row["observation_id"]}
