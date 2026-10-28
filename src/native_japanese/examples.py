"""全文と公開Docの正本を保持し、静的viewの索引を構築する。"""

import base64
import binascii
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

import srsly

from .models import (
    NORMALIZATION_IDENTITY,
    PARSER_VERSIONS,
    Analysis,
    PredicateGraph,
    PredicateNode,
    Query,
)
from .vocabulary import (
    VocabularyGeneration,
    VocabularyResources,
    VocabularySnapshot,
    restore_vocabulary,
)

SCHEMA_VERSION = 4
APPLICATION_ID = 0x4E4A4558
SOURCE_FIELDS = ("source_id", "observation_id", "source_url")
FIELDS = {
    "schema_version",
    "normalization_identity",
    "parser_versions",
    "text",
    "doc_base64",
    "vocabulary_resource_id",
    "vocabulary_resource_base64",
    "vocabulary_state_base64",
    "vocabulary_generation_id",
    "vocabulary_generation_base64",
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
            predicate_value(query.related_predicate) if query.related_predicate else None,
            query.related_attachment,
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
            predicate_value(query.related_predicate) if query.related_predicate else None,
            query.related_attachment,
        ]
    )


def parse_query_key(key: str, *, group=False) -> Query:
    value = json.loads(key)
    if (
        not isinstance(value, list)
        or len(value) != (7 if group else 8)
        or value[0] != NORMALIZATION_IDENTITY
        or canonical(value) != key
    ):
        raise ValueError("照会索引の解析契約が不正です。" + _REEXTRACT)
    predicate = parse_predicate(value[1])
    attachment, dep = value[2:4]
    noun, role = ([], value[4]) if group else (value[4], value[5])
    related_value, related_attachment = value[-2:]
    related = parse_predicate(related_value) if related_value is not None else None
    if (
        type(attachment) is not int
        or not 0 <= attachment < len(predicate.nodes)
        or not isinstance(dep, str)
        or not dep.strip()
        or role not in {"head", "dependent"}
        or not isinstance(noun, list)
        or any(not isinstance(x, str) or not x for x in noun)
        or (related is None and related_attachment is not None)
        or (
            related is not None
            and (
                type(related_attachment) is not int
                or not 0 <= related_attachment < len(related.nodes)
                or noun
            )
        )
    ):
        raise ValueError("関係照会の形式が不正です。")
    return Query(predicate, attachment, dep, tuple(noun), role, related, related_attachment)


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
    resource_id = record["vocabulary_resource_id"]
    if (
        not isinstance(resource_id, str)
        or len(resource_id) != 64
        or any(c not in "0123456789abcdef" for c in resource_id)
        or not isinstance(record["vocabulary_state_base64"], str)
        or (
            record["vocabulary_resource_base64"] is not None
            and not isinstance(record["vocabulary_resource_base64"], str)
        )
    ):
        raise ValueError("語彙資源のIDまたは文書状態の形式が不正です。")
    generation_id, generation_payload = (
        record["vocabulary_generation_id"],
        record["vocabulary_generation_base64"],
    )
    if (
        generation_id is not None
        and (
            not isinstance(generation_id, str)
            or len(generation_id) != 64
            or any(c not in "0123456789abcdef" for c in generation_id)
        )
        or (
            generation_payload is not None
            and (not isinstance(generation_payload, str) or generation_id is None)
        )
    ):
        raise ValueError("語彙世代のIDまたは共有状態の形式が不正です。")
    return record


def _generation_payloads(encoded: str) -> tuple[bytes, ...]:
    values = srsly.msgpack_loads(base64.b64decode(encoded, validate=True))
    if not isinstance(values, (list, tuple)) or any(
        not isinstance(value, bytes) for value in values
    ):
        raise ValueError("共有語彙世代の形式が不正です。")
    return tuple(values)


def _vocabulary_resource(payload: bytes, resource_id: str) -> VocabularyResources:
    resource = VocabularyResources.from_bytes(payload)
    if resource.resource_id != resource_id:
        raise ValueError("語彙資源のIDと内容が一致しません。")
    return resource


def analysis_from_record(
    record: dict, resources: dict | None = None, generations: dict | None = None
) -> Analysis:
    validate_record(record)
    resources = resources if resources is not None else {}
    resource_id = record["vocabulary_resource_id"]
    payload = record["vocabulary_resource_base64"]
    first_resource = resource_id not in resources
    if payload is not None:
        resource = _vocabulary_resource(base64.b64decode(payload, validate=True), resource_id)
        if resource_id not in resources:
            # 資源全体の公開形式を初出時に一度検証し、文書ごとに複製しない。
            resources[resource_id] = resource
    if resource_id not in resources:
        raise ValueError("文書に対応する共有語彙資源がありません。")
    if resources[resource_id].resource_id != resource_id:
        raise ValueError("語彙資源のIDと内容が一致しません。")
    generations = generations if generations is not None else {}
    if record["vocabulary_generation_base64"] is not None:
        for payload in _generation_payloads(record["vocabulary_generation_base64"]):
            base_id, parent_id, generation_id = VocabularyGeneration.references(payload)
            if base_id != resource_id or (parent_id is not None and parent_id not in generations):
                raise ValueError("共有語彙世代の基底または親がありません。")
            parent = generations.get(parent_id)
            generation = VocabularyGeneration.from_bytes(
                resources[resource_id], payload, parent=parent
            )
            if generation.generation_id != generation_id:
                raise ValueError("語彙世代のIDと内容が一致しません。")
            generations[generation_id] = generation
    generation_id = record["vocabulary_generation_id"]
    generation = generations.get(generation_id)
    if generation_id is not None and (
        generation is None
        or generation.generation_id != generation_id
        or generation.resource_id != resource_id
    ):
        raise ValueError("語彙世代のIDと内容が一致しないか、対応する世代がありません。")
    state = base64.b64decode(record["vocabulary_state_base64"], validate=True)
    vocabulary = VocabularySnapshot.from_bytes(resources[resource_id], state, generation=generation)
    if first_resource:
        restore_vocabulary(vocabulary)
    analysis = Analysis(
        record["text"], base64.b64decode(record["doc_base64"], validate=True), vocabulary
    )
    # 全資源を持つAnalysisを保持し、Docの位置整合は用法用の局所構造viewで検証する。
    _ = analysis.usage_structure.document
    return analysis


def extract_records(
    analysis: Analysis,
    source_id: str,
    source_url: str,
    *,
    resources_seen: set[str] | None = None,
    generations_seen: set[str] | None = None,
) -> Iterator[dict]:
    resource = analysis.vocabulary.resources
    include = resources_seen is None or resource.resource_id not in resources_seen
    generation = analysis.vocabulary.generation
    pending = generation.ancestors(generations_seen) if generation is not None else ()
    record = validate_record(
        {
            "schema_version": SCHEMA_VERSION,
            "normalization_identity": NORMALIZATION_IDENTITY,
            "parser_versions": PARSER_VERSIONS,
            "text": analysis.text,
            "doc_base64": base64.b64encode(analysis.snapshot).decode("ascii"),
            "vocabulary_resource_id": resource.resource_id,
            "vocabulary_resource_base64": base64.b64encode(resource.to_bytes()).decode("ascii")
            if include
            else None,
            "vocabulary_state_base64": base64.b64encode(analysis.vocabulary.to_bytes()).decode(
                "ascii"
            ),
            "vocabulary_generation_id": generation.generation_id if generation else None,
            "vocabulary_generation_base64": base64.b64encode(
                srsly.msgpack_dumps(tuple(g.to_bytes() for g in pending))
            ).decode("ascii")
            if pending
            else None,
            "source_id": source_id,
            "observation_id": hashlib.sha256(analysis.text.encode("utf-8")).hexdigest(),
            "source_url": source_url,
        }
    )
    if resources_seen is not None:
        resources_seen.add(resource.resource_id)
    if generations_seen is not None:
        generations_seen.update(g.generation_id for g in pending)
    yield record


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
            self._vocabularies = {}
            self._generations = {}
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
                CREATE TABLE vocabularies (id TEXT PRIMARY KEY, snapshot BLOB NOT NULL) WITHOUT ROWID;
                CREATE TABLE vocabulary_generations (
                    id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES vocabularies(id),
                    parent_id TEXT REFERENCES vocabulary_generations(id), snapshot BLOB NOT NULL
                ) WITHOUT ROWID;
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, snapshot BLOB NOT NULL,
                    vocabulary_id TEXT NOT NULL REFERENCES vocabularies(id), vocabulary_state BLOB NOT NULL,
                    generation_id TEXT REFERENCES vocabulary_generations(id)
                ) WITHOUT ROWID;
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
        # 取り消した取込の未保存資源を、次の取込で既存資源と扱わない。
        self._vocabularies.clear()
        self._generations.clear()
        stored_generations = {
            row[0] for row in self.connection.execute("SELECT id FROM vocabulary_generations")
        }
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
                    resource_id = record["vocabulary_resource_id"]
                    if resource_id not in self._vocabularies:
                        row = self.connection.execute(
                            "SELECT snapshot FROM vocabularies WHERE id=?", (resource_id,)
                        ).fetchone()
                        if row is not None:
                            self._vocabularies[resource_id] = _vocabulary_resource(
                                row[0], resource_id
                            )
                    generation_id = record["vocabulary_generation_id"]
                    if record["vocabulary_generation_base64"] is not None:
                        for payload in _generation_payloads(record["vocabulary_generation_base64"]):
                            _, parent_id, _ = VocabularyGeneration.references(payload)
                            if parent_id is not None and parent_id not in self._generations:
                                self._load_generation(parent_id, required=False)
                    elif generation_id is not None:
                        self._load_generation(generation_id)
                    analysis = analysis_from_record(record, self._vocabularies, self._generations)
                    comparison = analysis.comparison
                except (ValueError, TypeError, KeyError, binascii.Error) as error:
                    raise ValueError(f"用例JSONLの{number}行目: {error}") from error
                resource = analysis.vocabulary.resources
                if (
                    self.connection.execute(
                        "SELECT 1 FROM vocabularies WHERE id=?", (resource.resource_id,)
                    ).fetchone()
                    is None
                ):
                    self.connection.execute(
                        "INSERT INTO vocabularies VALUES (?,?)",
                        (resource.resource_id, resource.to_bytes()),
                    )
                generation = analysis.vocabulary.generation
                if generation is not None:
                    for item in generation.ancestors(stored_generations):
                        self.connection.execute(
                            "INSERT INTO vocabulary_generations VALUES (?,?,?,?)",
                            (item.generation_id, item.resource_id, item.parent_id, item.to_bytes()),
                        )
                        stored_generations.add(item.generation_id)
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?)",
                    (
                        analysis.document_id,
                        analysis.text,
                        analysis.snapshot,
                        resource.resource_id,
                        analysis.vocabulary.to_bytes(),
                        generation.generation_id if generation is not None else None,
                    ),
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

    def _load_generation(self, generation_id: str, *, required=True):
        pending = []
        pending_ids = set()
        current = generation_id
        while current is not None and current not in self._generations:
            row = self.connection.execute(
                "SELECT resource_id,parent_id,snapshot FROM vocabulary_generations WHERE id=?",
                (current,),
            ).fetchone()
            if row is None:
                if required:
                    raise ValueError("共有語彙世代がありません。")
                return
            pending.append((current, row))
            pending_ids.add(current)
            current = row["parent_id"]
            if current in pending_ids:
                raise ValueError("共有語彙世代の親参照が循環しています。")
        for key, row in reversed(pending):
            resource_id = row["resource_id"]
            if resource_id not in self._vocabularies:
                payload = self.connection.execute(
                    "SELECT snapshot FROM vocabularies WHERE id=?", (resource_id,)
                ).fetchone()
                if payload is None:
                    raise ValueError("共有語彙資源がありません。")
                self._vocabularies[resource_id] = _vocabulary_resource(payload[0], resource_id)
            generation = VocabularyGeneration.from_bytes(
                self._vocabularies[resource_id],
                row["snapshot"],
                parent=self._generations.get(row["parent_id"]),
            )
            if generation.generation_id != key:
                raise ValueError("語彙世代のIDと内容が一致しません。")
            self._generations[key] = generation
        return self._generations[generation_id]

    def iter_records(self) -> Iterator[dict]:
        resources_seen, generations_seen = set(), set()
        for row in self.connection.execute(
            "SELECT d.text,d.snapshot,d.vocabulary_id,d.vocabulary_state,d.generation_id,"
            "o.source_id,o.observation_id,o.source_url FROM documents d "
            "JOIN observations o ON o.document_id=d.id ORDER BY d.id,o.source_id,o.observation_id,o.source_url"
        ):
            resource_id = row["vocabulary_id"]
            if resource_id not in self._vocabularies:
                payload = self.connection.execute(
                    "SELECT snapshot FROM vocabularies WHERE id=?", (resource_id,)
                ).fetchone()[0]
                self._vocabularies[resource_id] = _vocabulary_resource(payload, resource_id)
            if self._vocabularies[resource_id].resource_id != resource_id:
                raise ValueError("語彙資源のIDと内容が一致しません。")
            generation = (
                self._load_generation(row["generation_id"])
                if row["generation_id"] is not None
                else None
            )
            if generation is not None and generation.resource_id != resource_id:
                raise ValueError("語彙世代の基底資源が一致しません。")
            vocabulary = VocabularySnapshot.from_bytes(
                self._vocabularies[resource_id], row["vocabulary_state"], generation=generation
            )
            record = next(
                extract_records(
                    Analysis(row["text"], row["snapshot"], vocabulary),
                    row["source_id"],
                    row["source_url"],
                    resources_seen=resources_seen,
                    generations_seen=generations_seen,
                )
            )
            yield {**record, "observation_id": row["observation_id"]}
