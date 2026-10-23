"""固定した小型fastTextモデルだけを検証して準備する。"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen

# checkoutから標準ライブラリだけでも準備を開始できるようにする。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from native_japanese.resources import check_asset, metadata, metadata_sha256, require, save

RESERVE = 32 << 20


def write_verified(stream, target, item):
    """既知size/hashとEOFを確認するまで完成pathへrenameしない。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    require(not partial.exists() and not partial.is_symlink(), "未完成fileを先に確認してください")
    count, digest = 0, hashlib.sha256()
    try:
        with partial.open("xb") as output:
            while chunk := stream.read(1 << 20):
                count += len(chunk)
                require(count <= item["bytes"], f"資源の展開量超過: {item['id']}")
                require(shutil.disk_usage(target.parent).free >= len(chunk) + RESERVE, "空容量不足")
                output.write(chunk)
                digest.update(chunk)
            require(
                count == item["bytes"] and digest.hexdigest() == item["sha256"],
                f"資源の固定size/SHA-256不一致: {item['id']}",
            )
            output.flush()
            os.fsync(output.fileno())
        require(not target.exists() and not target.is_symlink(), "完成先が既に存在します")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def prepare(args):
    """固定hashを確認し、既存資源は上書きせず再利用する。"""
    data, root = metadata(), args.assets_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    reuse = dict(item.split("=", 1) for item in args.reuse)
    require(set(reuse) <= {item["id"] for item in data["assets"]}, "未知の資源IDです")
    for item in data["assets"]:
        target = root / item["path"]
        if target.exists() or target.is_symlink():
            check_asset(target, item)
        elif item["id"] in reuse:
            original = Path(reuse[item["id"]]).resolve(strict=True)
            check_asset(original, item)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(original)
        else:
            require(not args.offline, "未準備資源: " + item["id"])
            require(shutil.disk_usage(root).free >= item["bytes"] + RESERVE, "空容量不足")
            with urlopen(item["url"], timeout=90) as stream:
                write_verified(stream, target, item)
    save(
        root / "assets-manifest.json",
        {
            "contract": data["contract"],
            "metadata_sha256": metadata_sha256(),
            "assets": [{"id": item["id"], "sha256": item["sha256"]} for item in data["assets"]],
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="固定fastTextモデルを検証して準備します。")
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--reuse", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        prepare(args)
    except Exception as error:
        print(f"setup-resources: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
