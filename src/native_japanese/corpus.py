"""原文と公開解析を文書単位で保存し、同じsnapshotだけを再開する。"""

import hashlib
import json
import os
import sqlite3
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .parser import GinzaParser
from .structure import Document, ParseMetadata, Sentence, Token

CORPUS_SCHEMA = "native-japanese.parsed-corpus.v1"


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _document(compressed: bytes) -> Document:
    raw = json.loads(zlib.decompress(compressed))
    return Document(
        source_id=raw["source_id"],
        text=raw["text"],
        tokens=tuple(Token(**token) for token in raw["tokens"]),
        sentences=tuple(Sentence(**sentence) for sentence in raw["sentences"]),
        metadata=ParseMetadata(**raw["metadata"]),
    )


def _manifest(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT value FROM corpus_metadata WHERE id=1").fetchone()
    if row is None:
        raise ValueError("解析正本のmetadataがありません")
    result = json.loads(row[0])
    if result["schema"] != CORPUS_SCHEMA:
        raise ValueError("解析正本のschemaが一致しません")
    return result


def _save_manifest(db: sqlite3.Connection, manifest: dict) -> None:
    db.execute("INSERT OR REPLACE INTO corpus_metadata VALUES (1, ?)", (_json(manifest),))


def _check_boundary(db: sqlite3.Connection, manifest: dict) -> int:
    count, first, last = db.execute("SELECT count(*), min(id), max(id) FROM sources").fetchone()
    if count != manifest["document_count"] or (count and (first != 1 or last != count)):
        raise ValueError("保存済み文書の件数または連続境界が一致しません")
    return count


class ParsedCorpus:
    """完成した解析正本。各documents呼出は独立して逐次再読込できる。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        with closing(self._connect()) as db:
            manifest = _manifest(db)
            _check_boundary(db, manifest)
        if not manifest["complete"]:
            raise ValueError("解析正本は未完了です。保存入口から再開してください")
        if manifest["document_count"] != manifest["expected_documents"]:
            raise ValueError("完成境界と予定文書数が一致しません")
        self.manifest = manifest
        self.metadata = ParseMetadata(**manifest["metadata"])
        self.source_sha256: str = manifest["source_sha256"]
        self.document_count: int = manifest["document_count"]
        self.token_count: int = manifest["token_count"]
        self.vocabulary_size: int = manifest["vocabulary_size"]
        self.content_sha256: str = manifest["content_sha256"]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA cache_size=-8192")
        return db

    def documents(
        self, *, order: str = "input", seed: int = 20261021
    ) -> Iterator[tuple[str, Document]]:
        """入力順、記事ID/source ID辞書順、SHA256(seed:source_id)順を返す。"""
        if order not in ("input", "article", "seed"):
            raise ValueError("orderはinput/article/seedのいずれかです")
        if type(seed) is not int:
            raise ValueError("seedは整数で指定してください")
        db = self._connect()
        try:
            db.execute("BEGIN")
            manifest = _manifest(db)
            if not manifest["complete"] or manifest != self.manifest:
                raise ValueError("開いた後に解析正本のmetadataが変更されました")
            _check_boundary(db, manifest)
            db.create_function(
                "source_hash",
                1,
                lambda source_id: hashlib.sha256(f"{seed}:{source_id}".encode()).digest(),
                deterministic=True,
            )
            ordering = {
                "input": "id",
                "article": "article_id, source_id, id",
                "seed": "source_hash(source_id), id",
            }
            for article_id, compressed in db.execute(
                f"SELECT article_id, document FROM sources ORDER BY {ordering[order]}"
            ):
                document = _document(compressed)
                if document.metadata != self.metadata:
                    raise ValueError("保存済み文書の解析metadataが一致しません")
                yield article_id, document
        finally:
            db.close()


def parse_corpus(
    source_path: str | Path,
    output_path: str | Path,
    *,
    parser: GinzaParser,
    expected_source_sha256: str | None = None,
    expected_documents: int | None = None,
    limit: int | None = None,
    batch_size: int = 32,
    progress: Callable[[dict], None] | None = None,
) -> ParsedCorpus:
    """JSONLのdocid/article_id/textを保存する。limitは先頭限定の別snapshotとなる。

    入力全体を一度hash・検証してから、同じファイル記述子を巻き戻す。
    再開時は保存済みprefixのID・記事・原文を照合し、未保存部分だけを解析する。
    """
    for name, value in (("expected_documents", expected_documents), ("limit", limit)):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f"{name}は正の整数です")
    if type(batch_size) is not int or batch_size <= 0:
        raise ValueError("batch_sizeは正の整数です")
    if expected_source_sha256 is not None:
        if len(expected_source_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in expected_source_sha256.lower()
        ):
            raise ValueError("expected_source_sha256はSHA256の16進表記です")
        expected_source_sha256 = expected_source_sha256.lower()
    source_path, output_path = Path(source_path), Path(output_path)
    if source_path.resolve() == output_path.resolve():
        raise ValueError("原本と出力先は別のファイルを指定してください")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output_path)
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA cache_size=-8192")
    manifest = None
    try:
        with source_path.open("rb") as source:
            initial_stat = os.fstat(source.fileno())

            def unchanged() -> None:
                current = os.fstat(source.fileno())
                named = source_path.stat()
                fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(
                    getattr(initial_stat, field) != getattr(stat, field)
                    for stat in (current, named)
                    for field in fields
                ):
                    raise ValueError("処理中に入力原本が変更されました")

            # ID集合もSQLiteへ置き、全原本・語彙をRAMに蓄積しない。
            db.execute("CREATE TEMP TABLE input_ids(source_id TEXT PRIMARY KEY)")
            digest = hashlib.sha256()
            source_count = 0
            for line in source:
                digest.update(line)
                record = json.loads(line)
                for name in ("docid", "article_id", "text"):
                    if not isinstance(record.get(name), str) or (
                        name != "text" and not record[name]
                    ):
                        raise ValueError(f"入力行 {source_count + 1} の{name}が不正です")
                try:
                    db.execute("INSERT INTO input_ids VALUES (?)", (record["docid"],))
                except sqlite3.IntegrityError as error:
                    raise ValueError(f"入力のdocidが重複しています: {record['docid']!r}") from error
                source_count += 1
            db.execute("DROP TABLE input_ids")
            db.commit()
            unchanged()
            source_sha256 = digest.hexdigest()
            if expected_source_sha256 is not None and source_sha256 != expected_source_sha256:
                raise ValueError("入力原本のSHA256が一致しません")
            if limit is not None and source_count < limit:
                raise ValueError("入力文書数がlimitに足りません")
            selected_count = source_count if limit is None else limit
            if expected_documents is not None and selected_count != expected_documents:
                raise ValueError("入力の予定文書数が一致しません")
            fixed = {
                "schema": CORPUS_SCHEMA,
                "source_sha256": source_sha256,
                "metadata": asdict(parser.metadata),
                "source_documents": source_count,
                "expected_documents": selected_count,
                "limit": limit,
                "selection": "all" if limit is None else "input_prefix",
            }
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if tables:
                manifest = _manifest(db)
                if any(manifest.get(key) != value for key, value in fixed.items()):
                    raise ValueError("再開する原本・解析metadata・snapshotが一致しません")
            else:
                with db:
                    db.execute("BEGIN")
                    db.execute(
                        "CREATE TABLE sources(id INTEGER PRIMARY KEY, source_id TEXT UNIQUE NOT NULL, "
                        "article_id TEXT NOT NULL, document BLOB NOT NULL)"
                    )
                    db.execute("CREATE INDEX article_order ON sources(article_id, id)")
                    db.execute(
                        "CREATE TABLE token_types(lemma TEXT, upos TEXT, PRIMARY KEY(lemma, upos)) "
                        "WITHOUT ROWID"
                    )
                    db.execute(
                        "CREATE TABLE corpus_metadata(id INTEGER PRIMARY KEY CHECK(id=1), value BLOB)"
                    )
                    manifest = dict(
                        fixed,
                        complete=False,
                        document_count=0,
                        token_count=0,
                        vocabulary_size=0,
                        parse_seconds=0.0,
                        content_sha256=None,
                    )
                    _save_manifest(db, manifest)
            saved_count = _check_boundary(db, manifest)
            if saved_count > selected_count:
                raise ValueError("保存済み文書が予定件数を超えています")
            source.seek(0)
            saved_tokens = 0
            for ordinal in range(1, saved_count + 1):
                record = json.loads(next(source))
                row = db.execute(
                    "SELECT source_id, article_id, document FROM sources WHERE id=?", (ordinal,)
                ).fetchone()
                document = _document(row[2])
                saved_tokens += len(document.tokens)
                if (
                    row[:2] != (record["docid"], record["article_id"])
                    or document.source_id != record["docid"]
                    or document.text != record["text"]
                    or document.metadata != parser.metadata
                ):
                    raise ValueError(f"保存済みprefixの原文・ID・metadataが不一致です: {ordinal}")
            saved_types = db.execute("SELECT count(*) FROM token_types").fetchone()[0]
            if (
                saved_tokens != manifest["token_count"]
                or saved_types != manifest["vocabulary_size"]
            ):
                raise ValueError("保存済みtoken数または語彙数がmetadataと一致しません")
            if manifest["complete"]:
                if saved_count != selected_count:
                    raise ValueError("完成境界と予定文書数が一致しません")
                unchanged()
            else:
                # nlp.pipeの先読みはbatch内に限る。未commitの解析結果は再開時に再処理する。
                pending = deque()

                def records():
                    for _ in range(saved_count, selected_count):
                        record = json.loads(next(source))
                        pending.append(record)
                        yield record["docid"], record["text"]

                started = time.monotonic()
                elapsed = manifest["parse_seconds"]
                for document in parser.parse_many(records(), batch_size=batch_size):
                    if not pending:
                        raise ValueError("解析器が入力数を超える文書を返しました")
                    record = pending.popleft()
                    if (
                        document.source_id != record["docid"]
                        or document.text != record["text"]
                        or document.metadata != parser.metadata
                    ):
                        raise ValueError("解析出力のID・原文・metadataが入力と一致しません")
                    unchanged()
                    with db:
                        db.execute(
                            "INSERT INTO sources VALUES (?,?,?,?)",
                            (
                                manifest["document_count"] + 1,
                                document.source_id,
                                record["article_id"],
                                zlib.compress(_json(asdict(document))),
                            ),
                        )
                        before_types = db.total_changes
                        db.executemany(
                            "INSERT OR IGNORE INTO token_types VALUES (?,?)",
                            ((token.lemma, token.upos) for token in document.tokens),
                        )
                        manifest["vocabulary_size"] += db.total_changes - before_types
                        manifest["document_count"] += 1
                        manifest["token_count"] += len(document.tokens)
                        manifest["parse_seconds"] = elapsed + time.monotonic() - started
                        _save_manifest(db, manifest)
                    if progress is not None and (
                        manifest["document_count"] % batch_size == 0
                        or manifest["document_count"] == selected_count
                    ):
                        progress(
                            {
                                key: manifest[key]
                                for key in (
                                    "document_count",
                                    "expected_documents",
                                    "token_count",
                                    "vocabulary_size",
                                    "parse_seconds",
                                    "complete",
                                )
                            }
                        )
                if pending or manifest["document_count"] != selected_count:
                    raise ValueError("解析器が全文書を返しませんでした。解析正本は未完了です")
                unchanged()
                # 再開不能な途中digestは保存せず、成功時に保存済み全文書を入力順でhashする。
                content = hashlib.sha256()
                for article_id, compressed in db.execute(
                    "SELECT article_id, document FROM sources ORDER BY id"
                ):
                    content.update(
                        _json([article_id, json.loads(zlib.decompress(compressed))]) + b"\n"
                    )
                with db:
                    manifest["vocabulary_size"] = db.execute(
                        "SELECT count(*) FROM token_types"
                    ).fetchone()[0]
                    unchanged()
                    manifest["content_sha256"] = content.hexdigest()
                    manifest["complete"] = True
                    _save_manifest(db, manifest)
    finally:
        db.close()
    return ParsedCorpus(output_path)
