"""固定モデルの取得・公開境界と、検証前のpickle拒否を確かめる。"""

import argparse
import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

from native_japanese import resources

SPEC = importlib.util.spec_from_file_location(
    "resources_setup", Path(__file__).parents[1] / "tools/setup-resources.py"
)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def asset(payload):
    return {
        "id": "fixture",
        "path": "fixture",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "url": "https://example.test/fixed-model",
    }


@pytest.mark.parametrize("payload", [b"wrong", b"longer than expected", b""])
def test_mismatch_never_publishes_completed_file(tmp_path, payload):
    target = tmp_path / "complete"
    with pytest.raises(ValueError, match="不一致|超過"):
        setup.write_verified(io.BytesIO(payload), target, asset(b"right"))
    assert not target.exists()
    assert not (tmp_path / "complete.part").exists()
    setup.write_verified(io.BytesIO(b"right"), target, asset(b"right"))
    assert target.read_bytes() == b"right"


def test_existing_file_and_partial_are_preserved(tmp_path):
    target = tmp_path / "complete"
    target.write_bytes(b"existing")
    with pytest.raises(ValueError, match="既に存在"):
        setup.write_verified(io.BytesIO(b"right"), target, asset(b"right"))
    assert target.read_bytes() == b"existing"
    partial = tmp_path / "complete.part"
    partial.write_bytes(b"previous attempt")
    with pytest.raises(ValueError, match="未完成"):
        setup.write_verified(io.BytesIO(b"right"), target, asset(b"right"))
    assert partial.read_bytes() == b"previous attempt"


def test_offline_reuse_does_not_replace_bad_cache(tmp_path, monkeypatch):
    original = tmp_path / "original"
    original.write_bytes(b"fixed")
    args = argparse.Namespace(
        assets_dir=tmp_path / "cache", reuse=[f"fixture={original}"], offline=True
    )
    monkeypatch.setattr(
        setup, "metadata", lambda: {"contract": "fixture", "assets": [asset(b"fixed")]}
    )
    setup.prepare(args)
    target = args.assets_dir / "fixture"
    assert target.is_symlink() and target.resolve() == original
    original.write_bytes(b"other")
    with pytest.raises(ValueError, match="SHA-256"):
        setup.prepare(args)
    assert original.read_bytes() == b"other"


def test_missing_offline_model_fails_without_network(tmp_path, monkeypatch):
    def unexpected_network(*_args, **_kwargs):
        pytest.fail("offline時に取得しています")

    monkeypatch.setattr(setup, "urlopen", unexpected_network)
    args = argparse.Namespace(assets_dir=tmp_path, reuse=[], offline=True)
    with pytest.raises(ValueError, match="未準備資源"):
        setup.prepare(args)
    assert not (tmp_path / "fasttext-ja-mini").exists()


def test_download_uses_fixed_url_and_rejects_mismatched_bytes(tmp_path, monkeypatch):
    item = asset(b"right")
    monkeypatch.setattr(setup, "metadata", lambda: {"contract": "fixture", "assets": [item]})
    calls = []

    def download(url, *, timeout):
        calls.append((url, timeout))
        return io.BytesIO(b"wrong")

    monkeypatch.setattr(setup, "urlopen", download)
    with pytest.raises(ValueError, match="不一致"):
        setup.prepare(argparse.Namespace(assets_dir=tmp_path, reuse=[], offline=False))
    assert calls == [(item["url"], 90)]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload", [b"wrong", b"wrong length"])
def test_untrusted_pickle_is_rejected_before_version_checks_or_load(tmp_path, monkeypatch, payload):
    item = {**asset(b"right"), "id": "fasttext-ja-mini", "path": "fasttext-ja-mini"}
    monkeypatch.setattr(resources, "metadata", lambda: {"assets": [item]})
    (tmp_path / "fasttext-ja-mini").write_bytes(payload)

    def unexpected_versions(_):
        pytest.fail("固定モデル検証より先に依存を読み込んでいます")

    monkeypatch.setattr(resources, "check_versions", unexpected_versions)
    with pytest.raises(ValueError, match="固定vector"):
        resources.load_fixed_vector(tmp_path)
