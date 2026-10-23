"""公開保存経路の登録保持と、library読込前の不正拒否を確認する。"""

import hashlib
from copy import deepcopy

import pytest

from native_japanese import membership


def saved(capacity=1000):
    builder = membership.Builder(capacity)
    for number in range(capacity):
        builder.add(f"キー[{number}]")
    return builder.finish()


@pytest.mark.parametrize("capacity", [0, 1, 2, 1000])
def test_all_added_keys_survive_public_serialization(capacity):
    raw, manifest = saved(capacity)
    restored = membership.restore(raw, manifest)
    assert all(membership.contains(restored, f"キー[{number}]") for number in range(capacity))
    if capacity == 0:
        assert not membership.contains(restored, "任意")
    else:
        assert bytes.fromhex(restored.export_hex()) == raw
        assert restored.elements_added == capacity


def test_capacity_requires_a_complete_distinct_stream():
    builder = membership.Builder(1)
    with pytest.raises(ValueError, match="追加数"):
        builder.finish()
    builder.add("一件")
    with pytest.raises(ValueError, match="容量"):
        builder.add("二件")


@pytest.mark.parametrize(
    "damage",
    [
        "truncated",
        "hash",
        "footer",
        "huge",
        "byteorder",
        "version",
        "shape",
        "added",
        "length",
    ],
)
def test_invalid_payload_never_reaches_hex_loader(monkeypatch, damage):
    raw, manifest = saved(2)
    manifest = deepcopy(manifest)
    if damage == "truncated":
        raw = raw[:-1]
    elif damage == "hash":
        raw = bytes([raw[0] ^ 1]) + raw[1:]
    elif damage == "footer":
        raw = raw[:-20] + membership.FOOTER.pack(2, 1, membership.FPR)
        manifest["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    elif damage == "huge":
        manifest.update(capacity=2**63, elements_added=2**63)
        raw = raw[:-20] + membership.FOOTER.pack(2**63, 2**63, membership.FPR)
        manifest["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    elif damage == "byteorder":
        manifest["byteorder"] = "big" if membership.sys.byteorder == "little" else "little"
    elif damage == "version":
        manifest["package_version"] = "0.0.0"
    elif damage == "shape":
        manifest["number_bits"] += 1
    elif damage == "added":
        manifest["elements_added"] -= 1
    else:
        manifest["raw_bytes"] = True
    original = membership.BloomFilter

    def checked_constructor(*args, **kwargs):
        assert "hex_string" not in kwargs, "不正入力が公開loaderへ到達しました"
        return original(*args, **kwargs)

    monkeypatch.setattr(membership, "BloomFilter", checked_constructor)
    with pytest.raises(ValueError):
        membership.restore(raw, manifest)
