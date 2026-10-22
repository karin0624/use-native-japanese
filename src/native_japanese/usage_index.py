"""同じ局所構造のc/r/m/Nを、完成snapshotの静的索引から取得する。

構築中だけSQLiteで集約し、完成後の等価照会はBinaryTrieと固定幅配列を使う。
対象はstructureが列挙する全2〜4 node出現。morphや選択外の文脈はkeyに含めない。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import zlib
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Iterator

import igraph as ig
import marisa_trie
import numpy as np
from numpy.lib.format import open_memmap

from .structure import (
    SCHEMA_VERSION,
    Document,
    FragmentShape,
    ParseMetadata,
    enumerate_fragments,
    shape_to_tuple,
)

INDEX_SCHEMA = "native-japanese.usage-index.v1"
SHARDS = 64
UINT32_MAX = (1 << 32) - 1
SQL_COUNT_MAX = (1 << 63) - 1
TRIE_ID_MAX = (1 << 31) - 1
F_DTYPE = np.dtype(
    [
        ("count", "<u8"),
        ("articles", "<u8"),
        ("document", "<u4"),
        ("sentence", "<u4"),
        ("nodes", "<u4", (4,)),
    ]
)
Q_DTYPE = np.dtype([("count", "<u8"), ("articles", "<u8"), ("offset", "<u8"), ("length", "<u4")])
COUNT_DTYPE = np.dtype([("count", "<u8")])
DTYPES = {"f": F_DTYPE, "q": Q_DTYPE, "m": COUNT_DTYPE, "n": COUNT_DTYPE}


class IndexUnavailableError(ValueError):
    """未完了・破損・版違いを、未観測の0件と混同しない。"""


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _uint32(value: int, name: str) -> int:
    if type(value) is not int or not 0 <= value <= UINT32_MAX:
        raise OverflowError(f"{name}がuint32の範囲外です")
    return value


def _key(*values: int) -> bytes:
    return struct.pack(">" + "I" * len(values), *(_uint32(v, "key ID") for v in values))


def _shape_key(shape: FragmentShape) -> bytes:
    size = len(shape.upos)
    if size not in (2, 3, 4):
        raise ValueError("照会対象は2〜4 nodeのshapeです")
    edges = tuple((e.head, e.child, e.dep) for e in shape.edges)
    if edges != tuple(sorted(edges)) or len(edges) != size - 1:
        raise ValueError("shapeには原文順に整列した依存木の全辺が必要です")
    if any(not 0 <= head < size or not 0 <= child < size for head, child, _ in edges):
        raise ValueError("shapeの辺の端点が範囲外です")
    graph = ig.Graph(n=size, edges=[(head, child) for head, child, _ in edges], directed=True)
    if not graph.is_tree(mode="out"):
        raise ValueError("shapeはheadからchildへ向く依存木である必要があります")
    return _json(shape_to_tuple(shape))


@dataclass(frozen=True, slots=True)
class UsageCounts:
    """出現数と記事数は異なる測度。2×2表に使うのは出現数だけ。"""

    c: int
    r: int
    m: int
    N: int
    c_articles: int
    r_articles: int
    status: str

    @property
    def contingency(self) -> tuple[int, int, int, int]:
        """同じshape母集団の2×2表を返す。"""
        return self.c, self.r - self.c, self.m - self.c, self.N - self.r - self.m + self.c


@dataclass(frozen=True, slots=True)
class Citation:
    """保存した同じ実出現の全選択tokenと、原文の半開区間。"""

    source_id: str
    article_id: str
    sentence_id: int
    node_ids: tuple[int, ...]
    spans: tuple[tuple[int, int], ...]
    surfaces: tuple[str, ...]
    text: str
    document: dict


@dataclass(frozen=True, slots=True)
class UsageCandidate:
    """qに属する実在値。原語自身の直接支持は通常この一覧から除く。"""

    lemma: str
    lemmas: tuple[str, ...]
    count: int
    articles: int


class UsageIndexBuilder:
    """順不同の記事入力をdiskへ集約する。全keyをPython辞書へ保持しない。

    expected_documentsは固定入力snapshotのpassage数。source_sha256はその入力のSHA。
    add_documentが予算等で失敗した場合は、その文書のtransactionを取り消す。
    再開機能は持たず、新規の空ディレクトリで構築する。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        metadata: ParseMetadata,
        source_identity: str,
        source_sha256: str,
        expected_documents: int,
        max_fragments_per_sentence: int,
    ) -> None:
        if version("marisa-trie") != "1.4.1":
            raise IndexUnavailableError("marisa-trieは1.4.1が必要です")
        if metadata.schema_version != SCHEMA_VERSION:
            raise IndexUnavailableError("解析schema版が一致しません")
        if type(expected_documents) is not int or expected_documents < 0:
            raise ValueError("expected_documentsは0以上の整数です")
        if type(max_fragments_per_sentence) is not int or max_fragments_per_sentence < 0:
            raise ValueError("文ごとのfragment予算は0以上の整数です")
        if not source_identity or len(source_sha256) != 64:
            raise ValueError("入力の識別子と64桁のSHA256が必要です")
        try:
            if len(bytes.fromhex(source_sha256)) != 32:
                raise ValueError("SHA256の長さが不正です")
        except ValueError as error:
            raise ValueError("source_sha256は16進数で指定してください") from error
        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        if any(self.path.iterdir()):
            raise ValueError("索引構築先は空ディレクトリにしてください")
        self.metadata = metadata
        self.expected_documents = expected_documents
        self.max_fragments_per_sentence = max_fragments_per_sentence
        self.documents = 0
        self.fragments = 0
        self._closed = False
        self._content_hash = hashlib.sha256()
        self.manifest = {
            "schema": INDEX_SCHEMA,
            "structure_schema": SCHEMA_VERSION,
            "complete": False,
            "metadata": asdict(metadata),
            "source_identity": source_identity,
            "source_sha256": source_sha256.lower(),
            "expected_documents": expected_documents,
            "marisa_version": "1.4.1",
            "shards": SHARDS,
            "abstraction": "原文順・UPOS・全有向dep・lemma。surface/morph/選択外文脈はkey外",
            "id_dtype": "uint32",
            "count_dtype": "uint64",
            "builder_count_max": SQL_COUNT_MAX,
        }
        self._write_manifest()
        self.db = sqlite3.connect(self.path / "build.sqlite")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.executescript("""
            CREATE TABLE lemma(id INTEGER PRIMARY KEY, value BLOB UNIQUE NOT NULL);
            CREATE TABLE shape(id INTEGER PRIMARY KEY, value BLOB UNIQUE NOT NULL);
            CREATE TABLE article(id INTEGER PRIMARY KEY, value TEXT UNIQUE NOT NULL);
            CREATE TABLE sources(id INTEGER PRIMARY KEY, source_id TEXT UNIQUE NOT NULL,
                                 article_id TEXT NOT NULL, document BLOB NOT NULL);
            CREATE TABLE f(key BLOB PRIMARY KEY, shard INTEGER, count INTEGER NOT NULL,
                           articles INTEGER NOT NULL DEFAULT 0, document INTEGER,
                           sentence INTEGER, nodes BLOB);
            CREATE TABLE q(key BLOB PRIMARY KEY, shard INTEGER, count INTEGER NOT NULL,
                           articles INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE m(key BLOB PRIMARY KEY, shard INTEGER, count INTEGER NOT NULL);
            CREATE TABLE n(key BLOB PRIMARY KEY, shard INTEGER, count INTEGER NOT NULL);
            CREATE TABLE f_article(key BLOB, article INTEGER, PRIMARY KEY(key, article)) WITHOUT ROWID;
            CREATE TABLE q_article(key BLOB, article INTEGER, PRIMARY KEY(key, article)) WITHOUT ROWID;
            CREATE TABLE candidate(qkey BLOB, fkey BLOB, shard INTEGER,
                                   PRIMARY KEY(qkey, fkey)) WITHOUT ROWID;
        """)

    def _write_manifest(self) -> None:
        temporary = self.path / "manifest.tmp"
        temporary.write_bytes(_json(self.manifest))
        temporary.replace(self.path / "manifest.json")

    def _intern(self, table: str, value: bytes | str) -> int:
        self.db.execute(f"INSERT OR IGNORE INTO {table}(value) VALUES (?)", (value,))
        result = self.db.execute(f"SELECT id FROM {table} WHERE value=?", (value,)).fetchone()[0]
        return _uint32(result, f"{table} ID")

    def _increment(self, table: str, key: bytes, shard: int, *citation: object) -> None:
        if table == "f":
            columns, extra = ",document,sentence,nodes", ",?,?,?"
        else:
            columns, extra = "", ""
        cursor = self.db.execute(
            f"INSERT INTO {table}(key,shard,count{columns}) VALUES (?,?,1{extra}) "
            f"ON CONFLICT(key) DO UPDATE SET count=count+1 WHERE count < ?",
            (key, shard, *citation, SQL_COUNT_MAX),
        )
        if cursor.rowcount != 1:
            raise OverflowError("出現数がSQLite構築用のint64上限を超えました")

    def add_document(self, document: Document, *, article_id: str) -> None:
        """全文書を列挙し、同じnode集合を一度だけ計数する。記事順の制約はない。"""
        if self._closed:
            raise IndexUnavailableError("構築は既に終了しています")
        if document.metadata != self.metadata:
            raise IndexUnavailableError("解析器・モデル・辞書・schema版が一致しません")
        if not isinstance(article_id, str) or not article_id:
            raise ValueError("article_idは空でない文字列です")
        if self.documents >= self.expected_documents:
            raise ValueError("固定snapshotの予定文書数を超えています")
        raw = _json(asdict(document))
        count = 0
        # 文書内だけのintern cache。コーパス全keyをRAMへ蓄積しない。
        shape_ids: dict[FragmentShape, int] = {}
        lemma_ids: dict[str, int] = {}
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO article(value) VALUES (?)", (article_id,))
            article = self.db.execute(
                "SELECT id FROM article WHERE value=?", (article_id,)
            ).fetchone()[0]
            cursor = self.db.execute(
                "INSERT INTO sources(source_id,article_id,document) VALUES (?,?,?)",
                (document.source_id, article_id, zlib.compress(raw)),
            )
            document_id = _uint32(cursor.lastrowid, "文書ID")
            for sentence in document.sentences:
                _uint32(sentence.id, "文ID")
                for fragment in enumerate_fragments(
                    document,
                    sentence_id=sentence.id,
                    max_fragments=self.max_fragments_per_sentence,
                ):
                    if fragment.shape not in shape_ids:
                        shape_ids[fragment.shape] = self._intern(
                            "shape", _shape_key(fragment.shape)
                        )
                    shape = shape_ids[fragment.shape]
                    shard = shape % SHARDS
                    for lemma in fragment.lemmas:
                        if lemma not in lemma_ids:
                            lemma_ids[lemma] = self._intern("lemma", lemma.encode())
                    lemmas = tuple(lemma_ids[s] for s in fragment.lemmas)
                    fkey = _key(shape, *lemmas)
                    nodes = _key(*fragment.node_ids)
                    self._increment("f", fkey, shard, document_id, sentence.id, nodes)
                    self.db.execute("INSERT OR IGNORE INTO f_article VALUES (?,?)", (fkey, article))
                    self._increment("n", _key(shape), shard)
                    for slot, lemma in enumerate(lemmas):
                        qkey = _key(shape, slot, *(lemmas[:slot] + lemmas[slot + 1 :]))
                        self._increment("q", qkey, shard)
                        self._increment("m", _key(shape, slot, lemma), shard)
                        self.db.execute(
                            "INSERT OR IGNORE INTO q_article VALUES (?,?)", (qkey, article)
                        )
                        self.db.execute(
                            "INSERT OR IGNORE INTO candidate VALUES (?,?,?)",
                            (qkey, fkey, shard),
                        )
                    count += 1
        self.documents += 1
        self.fragments += count
        self._content_hash.update(struct.pack(">Q", len(raw)))
        self._content_hash.update(raw)
        article_bytes = article_id.encode()
        self._content_hash.update(struct.pack(">Q", len(article_bytes)))
        self._content_hash.update(article_bytes)

    def _export_dictionary(self, name: str) -> None:
        count = self.db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        if count > TRIE_ID_MAX:
            raise OverflowError("辞書のkey数がmarisaのC int上限を超えました")
        trie = marisa_trie.BinaryTrie(
            row[0] for row in self.db.execute(f"SELECT value FROM {name}")
        )
        trie.save(str(self.path / f"{name}.trie"))
        ids = open_memmap(self.path / f"{name}.ids.npy", mode="w+", dtype="<u4", shape=(count,))
        reverse = open_memmap(
            self.path / f"{name}.reverse.npy", mode="w+", dtype="<u4", shape=(count + 1,)
        )
        reverse[:] = 0
        for item_id, value in self.db.execute(f"SELECT id,value FROM {name}"):
            key_id = trie[value]
            ids[key_id] = _uint32(item_id, f"{name} ID")
            reverse[item_id] = key_id
        ids.flush()
        reverse.flush()

    def _export_shard(self, shard: int) -> dict[str, int]:
        tries = {}
        sizes = {}
        for name in DTYPES:
            count = self.db.execute(
                f"SELECT count(*) FROM {name} WHERE shard=?", (shard,)
            ).fetchone()[0]
            if count > TRIE_ID_MAX:
                raise OverflowError("shardのkey数がmarisaのC int上限を超えました")
            sizes[name] = count
            if not count:
                continue
            trie = marisa_trie.BinaryTrie(
                row[0] for row in self.db.execute(f"SELECT key FROM {name} WHERE shard=?", (shard,))
            )
            trie.save(str(self.path / f"{shard:02d}.{name}.trie"))
            tries[name] = trie
        if not sizes["f"]:
            return sizes
        f_values = open_memmap(
            self.path / f"{shard:02d}.f.npy", mode="w+", dtype=F_DTYPE, shape=(sizes["f"],)
        )
        for key, count, articles, doc, sentence, nodes in self.db.execute(
            "SELECT key,count,articles,document,sentence,nodes FROM f WHERE shard=?",
            (shard,),
        ):
            ids = struct.unpack(">" + "I" * (len(nodes) // 4), nodes)
            padded = ids + (UINT32_MAX,) * (4 - len(ids))
            f_values[tries["f"][key]] = count, articles, doc, sentence, padded
        f_values.flush()
        candidates_count = self.db.execute(
            "SELECT count(*) FROM candidate WHERE shard=?", (shard,)
        ).fetchone()[0]
        candidates = open_memmap(
            self.path / f"{shard:02d}.candidates.npy",
            mode="w+",
            dtype="<u4",
            shape=(candidates_count,),
        )
        q_values = open_memmap(
            self.path / f"{shard:02d}.q.npy", mode="w+", dtype=Q_DTYPE, shape=(sizes["q"],)
        )
        offset = 0
        for key, count, articles in self.db.execute(
            "SELECT key,count,articles FROM q WHERE shard=? ORDER BY key",
            (shard,),
        ):
            start = offset
            for (fkey,) in self.db.execute(
                "SELECT fkey FROM candidate WHERE qkey=? ORDER BY fkey", (key,)
            ):
                candidates[offset] = tries["f"][fkey]
                offset += 1
            length = _uint32(offset - start, "qの実在値数")
            q_values[tries["q"][key]] = count, articles, start, length
        q_values.flush()
        candidates.flush()
        for name in ("m", "n"):
            values = open_memmap(
                self.path / f"{shard:02d}.{name}.npy",
                mode="w+",
                dtype=COUNT_DTYPE,
                shape=(sizes[name],),
            )
            for key, count in self.db.execute(
                f"SELECT key,count FROM {name} WHERE shard=?", (shard,)
            ):
                values[tries[name][key]] = (count,)
            values.flush()
        sizes["candidates"] = candidates_count
        return sizes

    def finish(self, *, input_complete: bool) -> Path:
        """全入力完了と予定件数を検証してからmanifestを原子的に完成状態へ変える。"""
        if self._closed:
            raise IndexUnavailableError("構築は既に終了しています")
        if input_complete is not True or self.documents != self.expected_documents:
            raise IndexUnavailableError("入力が未完了か、固定snapshotの予定文書数と一致しません")
        # 一度書き出しを開始したbuilderの再使用はしない。失敗artifactは未完了のまま残る。
        self._closed = True
        try:
            with self.db:
                for name in ("f", "q"):
                    self.db.execute(f"CREATE INDEX {name}_shard ON {name}(shard)")
                    self.db.execute(
                        f"UPDATE {name} SET articles=(SELECT count(*) FROM {name}_article a WHERE a.key={name}.key)"
                    )
                for name in ("m", "n", "candidate"):
                    self.db.execute(f"CREATE INDEX {name}_shard ON {name}(shard)")
            for name in ("lemma", "shape"):
                self._export_dictionary(name)
            sizes = [self._export_shard(shard) for shard in range(SHARDS)]
            with sqlite3.connect(self.path / "sources.sqlite") as target:
                target.execute(
                    "CREATE TABLE sources(id INTEGER PRIMARY KEY, source_id TEXT, article_id TEXT, document BLOB)"
                )
                target.executemany(
                    "INSERT INTO sources VALUES (?,?,?,?)", self.db.execute("SELECT * FROM sources")
                )
            self.manifest.update(
                {
                    "documents": self.documents,
                    "fragments": self.fragments,
                    "documents_sha256": self._content_hash.hexdigest(),
                    "input_complete": True,
                    "sizes": sizes,
                }
            )
        finally:
            self.db.close()
        (self.path / "build.sqlite").unlink()
        files = sorted(p for p in self.path.iterdir() if p.is_file() and p.name != "manifest.json")
        self.manifest["files"] = {
            p.name: {"sha256": _sha(p), "bytes": p.stat().st_size} for p in files
        }
        self.manifest["complete"] = True
        self._write_manifest()
        return self.path

    def close(self) -> None:
        """未完了で終了する。完成manifestは作らない。"""
        if not self._closed:
            self.db.close()
            self._closed = True

    def __enter__(self) -> UsageIndexBuilder:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class UsageIndex:
    """完成artifactをmmapで開く。版とSHAの不一致は例外にする。

    expected_metadata/source_*は呼出側が使うsnapshotとの一致確認に必須。
    起動時のSHA検証は全ファイルを読むが、各照会では全graphを走査しない。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        expected_metadata: ParseMetadata,
        source_identity: str,
        source_sha256: str,
    ) -> None:
        self.path = Path(path).resolve()
        try:
            self.manifest = json.loads((self.path / "manifest.json").read_bytes())
            manifest = self.manifest
            if not isinstance(manifest, dict):
                raise IndexUnavailableError("索引manifestはJSON objectである必要があります")
            if manifest.get("complete") is not True or manifest.get("input_complete") is not True:
                raise IndexUnavailableError("索引または入力が未完了です")
            if (
                manifest["schema"] != INDEX_SCHEMA
                or manifest["structure_schema"] != SCHEMA_VERSION
                or manifest["metadata"] != asdict(expected_metadata)
                or manifest["source_identity"] != source_identity
                or manifest["source_sha256"] != source_sha256.lower()
                or manifest["marisa_version"] != "1.4.1"
                or version("marisa-trie") != "1.4.1"
                or manifest["shards"] != SHARDS
                or manifest["documents"] != manifest["expected_documents"]
            ):
                raise IndexUnavailableError("索引のschema・解析来歴・入力snapshot版が一致しません")
            self._verify_files()
            self.dictionaries = {}
            self.ids = {}
            self.reverse = {}
            for name in ("lemma", "shape"):
                self.dictionaries[name] = self._trie(f"{name}.trie")
                count = len(self.dictionaries[name])
                self.ids[name] = self._array(f"{name}.ids.npy", np.dtype("<u4"), count)
                self.reverse[name] = self._array(f"{name}.reverse.npy", np.dtype("<u4"), count + 1)
            self.tries: list[dict] = []
            self.values: list[dict] = []
            for shard, sizes in enumerate(manifest["sizes"]):
                if any(
                    type(sizes[name]) is not int or not 0 <= sizes[name] <= TRIE_ID_MAX
                    for name in DTYPES
                ):
                    raise IndexUnavailableError("shardのkey件数がID範囲外です")
                if any(bool(sizes[name]) != bool(sizes["f"]) for name in DTYPES):
                    raise IndexUnavailableError("shardの集約面が欠落しています")
                tries, values = {}, {}
                for name, dtype in DTYPES.items():
                    count = sizes[name]
                    if count:
                        tries[name] = self._trie(f"{shard:02d}.{name}.trie")
                        if len(tries[name]) != count:
                            raise IndexUnavailableError("trieと配列の件数が一致しません")
                        values[name] = self._array(f"{shard:02d}.{name}.npy", dtype, count)
                if sizes["f"]:
                    values["candidates"] = self._array(
                        f"{shard:02d}.candidates.npy", np.dtype("<u4"), sizes["candidates"]
                    )
                self.tries.append(tries)
                self.values.append(values)
            if len(self.tries) != SHARDS:
                raise IndexUnavailableError("shard数が一致しません")
            if "sources.sqlite" not in manifest["files"]:
                raise IndexUnavailableError("原文正本のSHA記録がありません")
            self._sources = sqlite3.connect(
                (self.path / "sources.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True
            )
        except IndexUnavailableError:
            raise
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            raise IndexUnavailableError("索引artifactの読込に失敗しました") from error

    def _verify_files(self) -> None:
        files = self.manifest["files"]
        if not files:
            raise IndexUnavailableError("完成artifactのSHA一覧がありません")
        for name, record in files.items():
            if Path(name).name != name:
                raise IndexUnavailableError("artifactのファイル名が不正です")
            path = self.path / name
            if path.stat().st_size != record["bytes"] or _sha(path) != record["sha256"]:
                raise IndexUnavailableError(f"artifactのSHAが一致しません: {name}")

    def _trie(self, name: str) -> marisa_trie.BinaryTrie:
        if name not in self.manifest["files"]:
            raise IndexUnavailableError(f"artifactのSHA記録がありません: {name}")
        return marisa_trie.BinaryTrie().mmap(str(self.path / name))

    def _array(self, name: str, dtype: np.dtype, count: int) -> np.memmap:
        if name not in self.manifest["files"]:
            raise IndexUnavailableError(f"artifactのSHA記録がありません: {name}")
        array = np.load(self.path / name, mmap_mode="r", allow_pickle=False)
        if array.dtype != dtype or array.shape != (count,):
            raise IndexUnavailableError("固定幅配列のdtypeまたは長さが一致しません")
        return array

    def _id(self, name: str, key: bytes) -> int | None:
        try:
            return int(self.ids[name][self.dictionaries[name][key]])
        except KeyError:
            return None

    def _word(self, lemma_id: int) -> str:
        key_id = int(self.reverse["lemma"][lemma_id])
        return self.dictionaries["lemma"].restore_key(key_id).decode()

    def _query(
        self,
        shape: FragmentShape,
        lemmas: tuple[str, ...],
        target_slot: int,
    ) -> tuple[int | None, tuple[int | None, ...]]:
        shape_key = _shape_key(shape)
        if len(lemmas) != len(shape.upos) or any(not isinstance(s, str) for s in lemmas):
            raise ValueError("lemma列はshapeと同じ長さの文字列tupleが必要です")
        if type(target_slot) is not int or not 0 <= target_slot < len(lemmas):
            raise ValueError("target_slotがshapeの範囲外です")
        return self._id("shape", shape_key), tuple(self._id("lemma", s.encode()) for s in lemmas)

    def _record(self, shard: int, name: str, key: bytes) -> np.void | None:
        try:
            return self.values[shard][name][self.tries[shard][name][key]]
        except KeyError:
            return None

    def lookup(
        self, shape: FragmentShape, lemmas: tuple[str, ...], *, target_slot: int
    ) -> UsageCounts:
        """c=全原語、r=対象以外固定、m=対象だけ固定、N=shape全体。未見はexact0。"""
        shape_id, ids = self._query(shape, lemmas, target_slot)
        if shape_id is None:
            return UsageCounts(0, 0, 0, 0, 0, 0, "shape_unseen")
        shard = shape_id % SHARDS
        n = self._record(shard, "n", _key(shape_id))
        others = ids[:target_slot] + ids[target_slot + 1 :]
        q = (
            self._record(shard, "q", _key(shape_id, target_slot, *others))
            if None not in others
            else None
        )
        f = self._record(shard, "f", _key(shape_id, *ids)) if None not in ids else None
        m = (
            self._record(shard, "m", _key(shape_id, target_slot, ids[target_slot]))
            if ids[target_slot] is not None
            else None
        )
        if n is None:
            raise IndexUnavailableError("既存shapeの母集団集約がありません")

        def count(row: np.void | None) -> int:
            return 0 if row is None else int(row["count"])

        result = UsageCounts(
            count(f),
            count(q),
            count(m),
            count(n),
            0 if f is None else int(f["articles"]),
            0 if q is None else int(q["articles"]),
            "q_unseen" if q is None else "observed",
        )
        if min(result.contingency) < 0:
            raise IndexUnavailableError("集約件数が同じshape母集団の恒等式を満たしません")
        return result

    def _citation(self, row: np.void, size: int) -> Citation:
        source = self._sources.execute(
            "SELECT source_id,article_id,document FROM sources WHERE id=?", (int(row["document"]),)
        ).fetchone()
        if source is None:
            raise IndexUnavailableError("引用の原文正本がありません")
        source_id, article_id, compressed = source
        doc = json.loads(zlib.decompress(compressed))
        nodes = tuple(int(i) for i in row["nodes"][:size])
        tokens = tuple(doc["tokens"][i] for i in nodes)
        spans = tuple((t["start"], t["end"]) for t in tokens)
        surfaces = tuple(doc["text"][start:end] for start, end in spans)
        if surfaces != tuple(t["surface"] for t in tokens):
            raise IndexUnavailableError("引用のoffsetと原文表層が一致しません")
        return Citation(
            source_id, article_id, int(row["sentence"]), nodes, spans, surfaces, doc["text"], doc
        )

    def citation(self, shape: FragmentShape, lemmas: tuple[str, ...]) -> Citation | None:
        """原語Fの最小一例を返す。全出現postingの列挙ではない。"""
        shape_id, ids = self._query(shape, lemmas, 0)
        if shape_id is None or None in ids:
            return None
        row = self._record(shape_id % SHARDS, "f", _key(shape_id, *ids))
        return None if row is None else self._citation(row, len(ids))

    def candidates(
        self,
        shape: FragmentShape,
        lemmas: tuple[str, ...],
        *,
        target_slot: int,
        include_original: bool = False,
    ) -> Iterator[UsageCandidate]:
        """同じqの全実在値と集約値を返す。引用は選択後にcitation()で取得する。"""
        shape_id, ids = self._query(shape, lemmas, target_slot)
        others = ids[:target_slot] + ids[target_slot + 1 :]
        if shape_id is None or None in others:
            return
        shard = shape_id % SHARDS
        q = self._record(shard, "q", _key(shape_id, target_slot, *others))
        if q is None:
            return
        start, length = int(q["offset"]), int(q["length"])
        for f_id in self.values[shard]["candidates"][start : start + length]:
            key = self.tries[shard]["f"].restore_key(int(f_id))
            f_ids = struct.unpack(">" + "I" * (len(key) // 4), key)[1:]
            if not include_original and f_ids[target_slot] == ids[target_slot]:
                continue
            words = tuple(self._word(i) for i in f_ids)
            row = self.values[shard]["f"][int(f_id)]
            yield UsageCandidate(
                words[target_slot],
                words,
                int(row["count"]),
                int(row["articles"]),
            )

    def close(self) -> None:
        """原文正本の接続を閉じる。mmap配列は参照の解放時に閉じる。"""
        self._sources.close()

    def __enter__(self) -> UsageIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
