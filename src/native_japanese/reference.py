"""出典正本を、集合索引と固定群平均だけの配布参照へコンパイルする。"""

import itertools
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import numpy as np

from . import membership, resources
from .examples import (
    Examples,
    canonical,
    group_key,
    parse_predicate_key,
    parse_query_key,
    predicate_key,
    query_key,
)
from .models import NORMALIZATION_IDENTITY, PARSER_VERSIONS, Query
from .support import Support, Vectors, direction, group_mean

APPLICATION_ID = 0x4E4A5246
SCHEMA_VERSION = 3
DIMENSION = 300


def vector_identity():
    data = resources.metadata()
    item = next(x for x in data["assets"] if x["id"] == "fasttext-ja-mini")
    return {
        **{key: item[key] for key in ("id", "bytes", "sha256")},
        "python": data["vector_python"],
    }


def contract():
    return {
        "schema_version": SCHEMA_VERSION,
        "normalization_identity": NORMALIZATION_IDENTITY,
        "parser_versions": PARSER_VERSIONS,
        "vector": vector_identity(),
        "dimension": DIMENSION,
        "dtype": "<f8",
        "group": "exact-predicate-attachment-relation-noun-feature-mean-v2",
    }


def build_reference(examples_path, output_path, assets_dir) -> dict:
    """正本のsnapshotから逐次構築し、完成した一ファイルだけを置き換える。"""
    started = time.perf_counter()
    source, output = Path(examples_path), Path(output_path)
    if source.resolve() == output.resolve() or (
        output.exists() and os.path.samefile(source, output)
    ):
        raise ValueError("配布用の参照DBで元の用例DBを上書きできません。")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".part", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        with Examples(source) as examples, closing(sqlite3.connect(temporary)) as db, db:
            examples.connection.execute("BEGIN")
            usages = examples.connection.execute("SELECT count(*) FROM usages").fetchone()[0]
            builder = membership.Builder(usages)
            vectors = Vectors(resources.load_fixed_vector(Path(assets_dir)))
            if vectors.model.vector_size != DIMENSION:
                raise ValueError("固定の単語ベクトルの次元数が一致しません。")
            db.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
                CREATE TABLE membership (id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL);
                CREATE TABLE predicates (key TEXT PRIMARY KEY) WITHOUT ROWID;
                CREATE TABLE groups (
                    key TEXT PRIMARY KEY, mean BLOB, comparable INTEGER NOT NULL,
                    total INTEGER NOT NULL
                ) WITHOUT ROWID;
            """)
            predicates = 0
            for predicate in examples.iter_predicates():
                db.execute("INSERT INTO predicates VALUES (?)", (predicate_key(predicate),))
                predicates += 1
            groups = comparable = 0
            for key, queries in itertools.groupby(examples.iter_usages(), key=group_key):

                def nouns():
                    for query in queries:
                        builder.add(query_key(query))
                        yield query.noun

                mean, count, total = group_mean(nouns(), vectors)
                blob = mean.astype("<f8").tobytes() if mean is not None else None
                db.execute("INSERT INTO groups VALUES (?,?,?,?)", (key, blob, count, total))
                groups += 1
                comparable += count
            raw, manifest = builder.finish()
            restored = membership.restore(raw, manifest)
            # 保存経路を通った全登録が陽性であることを、公開前に正本へ照合する。
            if any(not membership.contains(restored, query_key(q)) for q in examples.iter_usages()):
                raise ValueError("配布用の索引に登録されていない用例があります。")
            identity = {
                **contract(),
                "predicates": predicates,
                "usages": usages,
                "groups": groups,
                "comparable": comparable,
            }
            db.execute("INSERT INTO metadata VALUES ('identity',?)", (canonical(identity),))
            db.execute("INSERT INTO metadata VALUES ('membership',?)", (canonical(manifest),))
            db.execute("INSERT INTO membership VALUES (1,?)", (raw,))
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        temporary.replace(output)
        return {
            "usages": usages,
            "predicates": predicates,
            "groups": groups,
            "comparable": comparable,
            "bytes": output.stat().st_size,
            "membership_bytes": len(raw),
            "seconds": time.perf_counter() - started,
        }
    finally:
        temporary.unlink(missing_ok=True)


class Reference:
    """検査時は配布参照だけを読み、原文・全出典・全用例を要求しない。"""

    def __init__(self, reference_path, assets_dir):
        self.assets_dir = Path(assets_dir)
        self._vectors = None
        path = Path(reference_path)
        self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            self.connection.execute("BEGIN")
            self._load(path.stat().st_size)
        except BaseException:
            self.connection.close()
            raise

    def _load(self, file_size):
        if (
            self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or self.connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        ):
            raise ValueError("配布用の参照DBの種類またはバージョンが一致しません。")
        lengths = self.connection.execute(
            "SELECT key,length(CAST(value AS BLOB)) FROM metadata"
        ).fetchall()
        if (
            len(lengths) != 2
            or {row[0] for row in lengths} != {"identity", "membership"}
            or any(
                type(length) is not int or not 0 < length <= min(file_size, 65536)
                for _, length in lengths
            )
        ):
            raise ValueError("参照DBのメタデータの項目またはデータ長が不正です。")
        meta = {
            key: json.loads(value)
            for key, value in self.connection.execute("SELECT * FROM metadata")
        }
        identity = meta["identity"]
        expected = contract()
        if type(identity) is not dict or set(identity) != {
            *expected,
            "usages",
            "groups",
            "comparable",
            "predicates",
        }:
            raise ValueError("参照DBの識別情報（identity）の項目が一致しません。")
        if any(type(identity[k]) is not type(v) or identity[k] != v for k, v in expected.items()):
            raise ValueError(
                "参照DBの作成に使った設定（形式のバージョン、解析器、固定資源など）が、この検査器に対応していません。"
            )
        if any(
            type(identity[k]) is not int or identity[k] < 0
            for k in ("usages", "groups", "comparable", "predicates")
        ):
            raise ValueError("参照DBに記録された件数が不正です。")
        lengths = self.connection.execute("SELECT id,length(payload) FROM membership").fetchall()
        if len(lengths) != 1 or lengths[0][0] != 1 or not 0 <= lengths[0][1] <= file_size:
            raise ValueError("Bloomフィルター本体の保存データ長が不正です。")
        manifest = meta["membership"]
        if type(manifest) is not dict or manifest.get("raw_bytes") != lengths[0][1]:
            raise ValueError("Bloomフィルターのデータ長が、記録された長さと一致しません。")
        if type(manifest.get("capacity")) is not int or manifest["capacity"] != identity["usages"]:
            raise ValueError("Bloomフィルターの容量と参照DBの用例数が一致しません。")
        raw = self.connection.execute("SELECT payload FROM membership WHERE id=1").fetchone()[0]
        self.membership = membership.restore(raw, manifest)
        predicates = set()
        for (key,) in self.connection.execute("SELECT key FROM predicates"):
            parse_predicate_key(key)
            predicates.add(key)
        if len(predicates) != identity["predicates"]:
            raise ValueError("述語の厳密索引とメタデータの件数が一致しません。")
        groups = total = comparable = 0
        for key, mean, count, size in self.connection.execute("SELECT * FROM groups"):
            self._decode_group(mean, count, size)
            query = parse_query_key(key, group=True)
            if predicate_key(query.predicate) not in predicates:
                raise ValueError("関係群に対応する厳密な述語用例がありません。")
            groups += 1
            total += size
            comparable += count
        if (groups, total, comparable) != (
            identity["groups"],
            identity["usages"],
            identity["comparable"],
        ):
            raise ValueError("用例のグループに含まれる件数が、参照DBのメタデータと一致しません。")
        self.identity = identity

    @staticmethod
    def _decode_group(blob, comparable, total):
        if (
            type(comparable) is not int
            or type(total) is not int
            or not 0 <= comparable <= total
            or total == 0
        ):
            raise ValueError("保存された用例のグループの件数が不正です。")
        if blob is None:
            if comparable != 0:
                raise ValueError("用例の平均データの有無と、比較できる用例数が矛盾しています。")
            return None
        if type(blob) is not bytes or len(blob) != DIMENSION * 8 or comparable == 0:
            raise ValueError("保存された用例の平均ベクトルの形式が不正です。")
        mean = np.frombuffer(blob, dtype="<f8")
        if not np.isfinite(mean).all():
            raise ValueError(
                "保存された用例の平均ベクトルに、無限大や数値でない値が含まれています。"
            )
        return mean

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.connection.close()
        self._vectors = None

    def assess(self, query: Query) -> Support:
        evidence = (
            {
                "source": "compiled-reference",
                "normalization_identity": NORMALIZATION_IDENTITY,
                "predicate": json.loads(predicate_key(query.predicate)),
                "group": json.loads(group_key(query)) if query.dependency is not None else None,
            },
        )
        # Bloomの誤陽性で未登録の述語・関係を支持しない。
        if (
            self.connection.execute(
                "SELECT 1 FROM predicates WHERE key=?", (predicate_key(query.predicate),)
            ).fetchone()
            is None
        ):
            return Support(False, None, evidence)
        if query.dependency is None:
            return Support(True, None, evidence)
        row = self.connection.execute(
            "SELECT mean,comparable,total FROM groups WHERE key=?", (group_key(query),)
        ).fetchone()
        if row is None:
            return Support(False, None, evidence)
        if membership.contains(self.membership, query_key(query)):
            return Support(True, None, evidence)
        blob, comparable, total = row
        mean = self._decode_group(*row)
        if mean is None or not np.any(mean):
            return Support(False, None, evidence, comparable, total)
        if self._vectors is None:
            self._vectors = Vectors(resources.load_fixed_vector(self.assets_dir))
            if self._vectors.model.vector_size != DIMENSION:
                raise ValueError("固定の単語ベクトルの次元数が一致しません。")
        x = self._vectors.compose(query.noun)
        score = direction(x, mean) if x is not None else None
        return Support(False, score, evidence, comparable, total)
