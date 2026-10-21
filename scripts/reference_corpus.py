"""固定manifestの参照passageを取得・検証する。日本語解析・修復はしない。"""

import argparse
import hashlib
import json
import resource
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pyarrow
import pyarrow.parquet as pq

FROZEN_MANIFEST = Path(__file__).with_name("reference-2019-manifest.json")


def acquire(manifest_path, root):
    manifest_bytes = manifest_path.read_bytes()
    m = json.loads(manifest_bytes)
    limits = m["limits"]
    assert pyarrow.__version__ == m["reader"]["version"]
    seed = m["sampling"]["seed"]
    temporary = root / "temporary"
    # 新規ディレクトリの排他的作成で再実行と同時書込みを防ぐ。
    root.mkdir(parents=True)
    (root / "manifest.json").write_bytes(manifest_bytes)
    temporary.mkdir()
    start = time.monotonic()
    report = {
        "status": "incomplete",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "reader_version": pyarrow.__version__,
        "python": sys.version,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "downloaded_bytes": 0,
        "processed_rows": 0,
        "articles": 0,
        "selected_rows": 0,
        "initial_rows": 0,
        "output_bytes": 0,
        "article_bucket_counts": [0] * 100,
        "shards": [],
        "outputs": {},
    }
    article_ids = set()
    outputs = {}
    hashes = {name: hashlib.sha256() for name in m["output_files"]}
    output_sizes = dict.fromkeys(m["output_files"], 0)

    def rss():
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if sys.platform == "darwin" else 1024
        )

    def save_report():
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
        report["peak_rss_bytes"] = rss()
        report["articles"] = len(article_ids)
        for name, filename in m["output_files"].items():
            report["outputs"][name] = {
                "file": filename,
                "bytes": output_sizes[name],
                "sha256": hashes[name].hexdigest(),
                "complete": report["status"] == "complete",
            }
        target = root / "result.json"
        temp_report = root / "result.json.tmp"
        temp_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temp_report.replace(target)

    try:
        # 排他的作成により追記と上書きを防ぐ。
        for name, filename in m["output_files"].items():
            outputs[name] = (root / filename).open("xb")
        for shard in m["shards"]:
            local = temporary / Path(shard["path"]).name
            entry = {"source": shard, "downloaded_bytes": 0, "status": "downloading"}
            report["shards"].append(entry)
            shard_start = time.monotonic()
            if report["downloaded_bytes"] + shard["bytes"] > limits["corpus_download_bytes"]:
                raise RuntimeError("コーパス転送量の上限を超える")
            digest = hashlib.sha256()
            with urllib.request.urlopen(
                shard["url"], timeout=limits["timeout_seconds"]
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"想定外のHTTP status: {response.status}")
                entry["http_status"] = response.status
                entry["content_length"] = response.headers.get("Content-Length")
                with local.open("xb") as stream:
                    while True:
                        remaining = min(
                            shard["bytes"] - entry["downloaded_bytes"],
                            limits["corpus_download_bytes"] - report["downloaded_bytes"],
                        )
                        if remaining == 0:
                            break
                        chunk = response.read(min(limits["download_chunk_bytes"], remaining))
                        if not chunk:
                            break
                        stream.write(chunk)
                        digest.update(chunk)
                        entry["downloaded_bytes"] += len(chunk)
                        report["downloaded_bytes"] += len(chunk)
            entry["actual_sha256"] = digest.hexdigest()
            if entry["downloaded_bytes"] != shard["bytes"] or digest.hexdigest() != shard["sha256"]:
                raise RuntimeError(f"原データのサイズまたはSHA256が不一致: {shard['path']}")
            entry["status"] = "checksum_verified"
            entry["rows"] = 0
            parquet = pq.ParquetFile(local)
            if parquet.schema_arrow.names != ["docid", "title", "text"]:
                raise RuntimeError(f"想定外の原データschema: {parquet.schema_arrow}")
            entry["metadata_rows"] = parquet.metadata.num_rows
            for batch in parquet.iter_batches(batch_size=limits["batch_rows"], use_threads=False):
                for row in batch.to_pylist():
                    docid = row["docid"]
                    parts = docid.split("#")
                    if len(parts) != 2 or not all(p.isascii() and p.isdecimal() for p in parts):
                        raise RuntimeError(f"想定外のdocid形式: {docid!r}")
                    article_id = parts[0]
                    bucket = (
                        int.from_bytes(
                            hashlib.sha256(f"{seed}:{article_id}".encode("utf-8")).digest(), "big"
                        )
                        % 100
                    )
                    if article_id not in article_ids:
                        article_ids.add(article_id)
                        report["article_bucket_counts"][bucket] += 1
                    report["processed_rows"] += 1
                    entry["rows"] += 1
                    if bucket >= 5:
                        continue
                    record = {
                        "docid": docid,
                        "article_id": article_id,
                        "title": row["title"],
                        "source": f"https://ja.wikipedia.org/?curid={article_id}",
                        "dataset": m["dataset"],
                        "snapshot": m["snapshot"],
                        "revision": m["revision"],
                        "license": m["license"]["text"],
                        "bucket": bucket,
                        "text": row["text"],
                    }
                    encoded = (
                        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    destinations = ["selected", "initial"] if bucket == 0 else ["selected"]
                    increment = len(encoded) * len(destinations)
                    if report["output_bytes"] + increment > limits["combined_output_bytes"]:
                        raise RuntimeError("出力合計サイズの上限を超える")
                    for name in destinations:
                        outputs[name].write(encoded)
                        hashes[name].update(encoded)
                        output_sizes[name] += len(encoded)
                    report["selected_rows"] += 1
                    if bucket == 0:
                        report["initial_rows"] += 1
                    report["output_bytes"] += increment
                if rss() > limits["peak_rss_target_bytes"]:
                    raise RuntimeError("最大RSSの監視上限を超えた")
            parquet.close()
            if entry["rows"] != entry["metadata_rows"]:
                raise RuntimeError("shardの行数が不一致")
            entry["status"] = "processed_and_temporary_deleted"
            entry["elapsed_seconds"] = round(time.monotonic() - shard_start, 3)
            # 検証と処理を終えた、この実行の一時shardだけを削除する。
            local.unlink()
            for stream in outputs.values():
                stream.flush()
            save_report()
            print(
                json.dumps(
                    {
                        "shard": shard["path"],
                        "rows": entry["rows"],
                        "selected_rows": report["selected_rows"],
                        "elapsed_seconds": report["elapsed_seconds"],
                        "peak_rss_bytes": rss(),
                    }
                ),
                flush=True,
            )
        if (
            report["processed_rows"] != m["expected"]["passages"]
            or len(article_ids) != m["expected"]["articles"]
        ):
            raise RuntimeError("全コーパスのpassage数または記事数が不一致")
        report["selected_articles"] = sum(report["article_bucket_counts"][:5])
        report["initial_articles"] = report["article_bucket_counts"][0]
        report["status"] = "complete"
    except BaseException as error:
        report["status"] = "failed_partial"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        for stream in outputs.values():
            stream.close()
        save_report()
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in [
                        "status",
                        "downloaded_bytes",
                        "processed_rows",
                        "articles",
                        "selected_rows",
                        "initial_rows",
                        "output_bytes",
                        "elapsed_seconds",
                        "peak_rss_bytes",
                    ]
                }
            ),
            flush=True,
        )


def verify(root):
    manifest_bytes = (root / "manifest.json").read_bytes()
    m = json.loads(manifest_bytes)
    r = json.loads((root / "result.json").read_bytes())
    assert r["status"] == "complete", r["status"]
    assert r["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert r["downloaded_bytes"] == sum(s["bytes"] for s in m["shards"])
    assert r["processed_rows"] == m["expected"]["passages"]
    assert r["articles"] == sum(r["article_bucket_counts"]) == m["expected"]["articles"]
    assert len(r["shards"]) == len(m["shards"])
    for actual, source in zip(r["shards"], m["shards"], strict=True):
        assert actual["source"] == source
        assert actual["actual_sha256"] == source["sha256"]
        assert actual["downloaded_bytes"] == source["bytes"]
        assert actual["rows"] == actual["metadata_rows"]
        assert actual["status"] == "processed_and_temporary_deleted"
        assert not (root / "temporary" / Path(source["path"]).name).exists()
    filtered_initial_digest = hashlib.sha256()
    counts = {}
    for name, filename in m["output_files"].items():
        digest = hashlib.sha256()
        size = 0
        rows = 0
        ids = set()
        with (root / filename).open("rb") as stream:
            for line in stream:
                digest.update(line)
                size += len(line)
                rows += 1
                item = json.loads(line)
                article_id, passage_id = item["docid"].split("#")
                assert item["article_id"] == article_id
                bucket = (
                    int.from_bytes(
                        hashlib.sha256(
                            (m["sampling"]["seed"] + ":" + article_id).encode()
                        ).digest(),
                        "big",
                    )
                    % 100
                )
                assert item["bucket"] == bucket
                assert 0 <= bucket < 5 if name == "selected" else bucket == 0
                for field in ["snapshot", "revision", "dataset"]:
                    assert item[field] == m[field]
                assert item["license"] == m["license"]["text"]
                assert item["source"] == f"https://ja.wikipedia.org/?curid={article_id}"
                assert isinstance(item["title"], str) and isinstance(item["text"], str)
                ids.add(article_id)
                if name == "selected" and bucket == 0:
                    filtered_initial_digest.update(line)
        assert size == r["outputs"][name]["bytes"]
        assert digest.hexdigest() == r["outputs"][name]["sha256"]
        assert rows == r[f"{name}_rows"]
        assert len(ids) == r[f"{name}_articles"]
        counts[name] = {
            "rows": rows,
            "articles": len(ids),
            "bytes": size,
            "sha256": digest.hexdigest(),
        }
    assert filtered_initial_digest.hexdigest() == r["outputs"]["initial"]["sha256"]
    assert (
        sum(x["bytes"] for x in counts.values())
        == r["output_bytes"]
        <= m["limits"]["combined_output_bytes"]
    )
    assert r["downloaded_bytes"] <= m["limits"]["corpus_download_bytes"]
    assert r["peak_rss_bytes"] <= m["limits"]["peak_rss_target_bytes"]
    result = {
        "status": "passed",
        "checks": [
            "原データ全件数とSHA256",
            "全出力行とmetadata",
            "記事IDだけによるhash抽出",
            "初期集合とbucket 0のbyte一致",
            "全出力のSHA256と件数",
            "予算と処理済み一時shardの削除",
        ],
        "counts": counts,
    }
    (root / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("acquire", help="固定shardを取得して記事単位で抽出する")
    fetch.add_argument("--manifest", type=Path, default=FROZEN_MANIFEST)
    fetch.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("verify", help="既存出力のhash・抽出条件・包含関係を検証する")
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "acquire":
        acquire(args.manifest, args.output)
    else:
        verify(args.output)
