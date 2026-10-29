"""検証済みの通常BloomFilterを、固定契約で保存・復元する。"""

import hashlib
import sys
from importlib.metadata import version
from struct import Struct

from probables import BloomFilter
from probables.hashes import default_sha256

FPR = 1 / 65536
VERSION = "0.7.0"
FOOTER = Struct(">QQf")
CONTRACT = {
    "format": "pyprobables-export-hex-bytes-v1",
    "package": "pyprobables",
    "package_version": VERSION,
    "filter": "BloomFilter",
    "hash": "probables.hashes.default_sha256",
    "byteorder": sys.byteorder,
    "key": "kwja-query-canonical-json-utf8-v1",
    "false_positive_rate": FPR,
}
INTEGER_FIELDS = (
    "capacity",
    "elements_added",
    "number_bits",
    "number_hashes",
    "payload_bytes",
    "raw_bytes",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def new_filter(capacity):
    require(
        version("pyprobables") == VERSION,
        "Bloomフィルターに必要なpyprobablesのバージョンが一致しません。",
    )
    require(type(capacity) is int and capacity >= 0, "Bloomフィルターの容量が不正です。")
    return (
        BloomFilter(est_elements=capacity, false_positive_rate=FPR, hash_function=default_sha256)
        if capacity
        else None
    )


class Builder:
    """重複排除した正本の件数を容量とし、一件ずつ追加する。"""

    def __init__(self, capacity):
        self.filter = new_filter(capacity)
        self.capacity = capacity
        self.added = 0

    def add(self, key: str):
        require(self.added < self.capacity, "Bloomフィルターの固定容量を超えて追加できません。")
        self.filter.add(key.encode("utf-8"))
        self.added += 1

    def finish(self):
        require(
            self.added == self.capacity, "元の用例数とBloomフィルターへの追加数が一致しません。"
        )
        filt = self.filter
        require(
            filt is None or filt.elements_added == self.added,
            "Bloomフィルターへの追加数が不正です。",
        )
        raw = bytes.fromhex(filt.export_hex()) if filt else b""
        manifest = {
            **CONTRACT,
            "capacity": self.capacity,
            "elements_added": self.added,
            "number_bits": filt.number_bits if filt else 0,
            "number_hashes": filt.number_hashes if filt else 0,
            "payload_bytes": filt.bloom_length if filt else 0,
            "raw_bytes": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
        return raw, manifest


def restore(raw: bytes, manifest: dict):
    """同一bufferを検査後に公開hex loaderへ渡す。試験件数の上限は設けない。"""
    require(
        type(raw) is bytes and type(manifest) is dict, "Bloomフィルターの保存データの型が不正です。"
    )
    require(
        version("pyprobables") == VERSION,
        "Bloomフィルターに必要なpyprobablesのバージョンが一致しません。",
    )
    require(
        set(manifest) == {*CONTRACT, *INTEGER_FIELDS, "raw_sha256"},
        "Bloomフィルターの保存情報の項目が一致しません。",
    )
    for key, expected in CONTRACT.items():
        require(
            type(manifest[key]) is type(expected) and manifest[key] == expected,
            f"Bloomフィルターの保存設定が一致しません: {key}",
        )
    for key in INTEGER_FIELDS:
        require(
            type(manifest[key]) is int and manifest[key] >= 0,
            f"Bloomフィルターの設定値は0以上の整数にしてください: {key}",
        )
    n = manifest["capacity"]
    require(manifest["elements_added"] == n, "Bloomフィルターへの追加数が容量と一致しません。")
    require(len(raw) == manifest["raw_bytes"], "Bloomフィルターの保存データ長が一致しません。")
    require(
        hashlib.sha256(raw).hexdigest() == manifest["raw_sha256"],
        "BloomフィルターのSHA-256が一致しません。",
    )
    if n == 0:
        require(
            not raw and all(manifest[k] == 0 for k in INTEGER_FIELDS),
            "用例が0件のBloomフィルターの保存情報が不正です。",
        )
        return None
    require(len(raw) >= FOOTER.size, "Bloomフィルターの末尾情報が不足しています。")
    require(
        FOOTER.unpack(raw[-FOOTER.size :]) == (n, n, FPR),
        "Bloomフィルターの末尾情報が一致しません。",
    )
    require(
        manifest["payload_bytes"] == len(raw) - FOOTER.size,
        "Bloomフィルター本体のデータ長が一致しません。",
    )
    # 固定FPRでは一要素あたり一byte以上が必要。巨大な偽容量を確保前に拒否する。
    require(
        n <= manifest["payload_bytes"], "Bloomフィルターの容量と本体のデータ長が矛盾しています。"
    )
    expected = new_filter(n)
    require(
        (manifest["number_bits"], manifest["number_hashes"], manifest["payload_bytes"])
        == (expected.number_bits, expected.number_hashes, expected.bloom_length),
        "Bloomフィルターの構造が固定容量と一致しません。",
    )
    del expected
    filt = BloomFilter(hex_string=raw.hex(), hash_function=default_sha256)
    require(
        (filt.estimated_elements, filt.elements_added, filt.number_bits, filt.number_hashes)
        == (n, n, manifest["number_bits"], manifest["number_hashes"]),
        "復元したBloomフィルターの値が保存情報と一致しません。",
    )
    return filt


def contains(filt, key: str) -> bool:
    return bool(filt is not None and filt.check(key.encode("utf-8")))
