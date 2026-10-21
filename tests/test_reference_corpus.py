"""小さいParquetで記事単位の抽出、原文保全、失敗時の保全を確認する。"""

import hashlib
import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts import reference_corpus as r


@pytest.fixture
def source(tmp_path, monkeypatch):
    # この固定seedでは218→0、62→1、35→7。記事の全passageを同じbucketへ入れる。
    rows = [
        {"docid": "218#0", "title": "人工の題名", "text": "Ａ　文。\n次の行。"},
        {"docid": "218#1", "title": "人工の題名", "text": "【原文】を変えない"},
        {"docid": "62#0", "title": "二番目", "text": "別の文"},
        {"docid": "35#0", "title": "対象外", "text": "この文は抽出しない"},
    ]
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buffer)
    body = buffer.getvalue()
    manifest = json.loads(r.FROZEN_MANIFEST.read_bytes())
    manifest["shards"] = [
        {
            "path": "fixture.parquet",
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "url": "https://fixture.invalid/shard",
        }
    ]
    manifest["expected"] = {"passages": 4, "articles": 3, "parquet_bytes": len(body)}
    path = tmp_path / "source-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    def response(url, timeout):
        assert url == "https://fixture.invalid/shard"
        stream = io.BytesIO(body)
        stream.status = 200
        stream.headers = {"Content-Length": str(len(body))}
        return stream

    monkeypatch.setattr(r.urllib.request, "urlopen", response)
    return path, rows


def test_preserves_text_and_nested_article_sample(source, tmp_path):
    manifest, rows = source
    output = tmp_path / "output"
    r.acquire(manifest, output)
    r.verify(output)
    selected = [
        json.loads(line)
        for line in (output / "selected-buckets-00-04.jsonl").read_bytes().splitlines()
    ]
    initial = [
        json.loads(line) for line in (output / "initial-bucket-00.jsonl").read_bytes().splitlines()
    ]
    assert [{key: row[key] for key in ("docid", "title", "text")} for row in selected] == rows[:3]
    assert [row["bucket"] for row in selected] == [0, 0, 1]
    assert initial == selected[:2]
    assert (output / "manifest.json").read_bytes() == manifest.read_bytes()
    assert json.loads((output / "result.json").read_bytes())["status"] == "complete"
    before = (output / "result.json").read_bytes()
    with pytest.raises(FileExistsError):
        r.acquire(manifest, output)
    assert (output / "result.json").read_bytes() == before
    with (output / "selected-buckets-00-04.jsonl").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises((AssertionError, ValueError)):
        r.verify(output)


def test_bad_source_hash_retains_failed_report_and_partial_artifacts(source, tmp_path):
    manifest_path, _ = source
    manifest = json.loads(manifest_path.read_bytes())
    manifest["shards"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="SHA256"):
        r.acquire(manifest_path, output)
    report = json.loads((output / "result.json").read_bytes())
    assert report["status"] == "failed_partial"
    assert not report["outputs"]["selected"]["complete"]
    assert (output / "temporary" / "fixture.parquet").exists()
    with pytest.raises(AssertionError):
        r.verify(output)
