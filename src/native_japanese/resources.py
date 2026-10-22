"""固定資源・native設定の検証と、許可済みvectorのloadを共有する。"""

from __future__ import annotations

import hashlib
import json
import tempfile
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from compress_fasttext.models import CompressedFastTextKeyedVectors


def require(condition, message):
    if not condition:
        raise ValueError(message)


def metadata():
    return json.loads(files("native_japanese").joinpath("data/native-assets.json").read_bytes())


def metadata_sha256():
    payload = files("native_japanese").joinpath("data/native-assets.json").read_bytes()
    return hashlib.sha256(payload).hexdigest()


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_asset(path, item):
    require(
        path.is_file() and path.stat().st_size == item["bytes"], f"資源size不一致: {item['id']}"
    )
    require(sha256(path) == item["sha256"], f"資源SHA-256不一致: {item['id']}")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def identity(path):
    stat = path.stat()
    return {
        "resolved": str(path.resolve()),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "device": stat.st_dev,
        "inode": stat.st_ino,
    }


def required_files(data):
    return [
        "bin/knp",
        "bin/jumanpp",
        "etc/knprc",
        "build.json",
        "libexec/jumanpp/jumandic.config",
        "libexec/jumanpp/jumandic.jppmdl",
        *["share/juman/dic/" + name for name in data["grammar_files"]],
        *["share/knp/rule/" + name + ".data" for name in data["rules"]],
        *["share/knp/dict/" + name for name in data["generated_dict"]],
        *[
            "share/knp/dict/" + x["path"].removeprefix("knp/")
            for x in data["assets"]
            if "member" in x and x["id"] != "wikipedia.dat.skip"
        ],
    ]


def read_runtime(config_path: Path, full: bool = False) -> dict:
    data, record = metadata(), json.loads(config_path.read_text())
    require(
        record["schema"] == 1
        and record["contract"] == data["contract"]
        and record["metadata_sha256"] == metadata_sha256(),
        "runtime契約の不一致",
    )
    prefix = Path(record["prefix"])
    require(
        record["options"] == ["-r", str(prefix / "etc/knprc"), *data["options"]], "固定option不一致"
    )
    require(
        set(required_files(data)) <= {x["path"] for x in record["files"]}, "必須runtime記録不足"
    )
    for item in record["files"]:
        require(
            not Path(item["path"]).is_absolute() and ".." not in Path(item["path"]).parts,
            "runtime記録の相対pathが不正です",
        )
        path = prefix / item["path"]
        require(path.is_file(), f"runtime file不足: {item['path']}")
        if full:
            require(sha256(path) == item["sha256"], f"runtime hash不一致: {item['path']}")
            item.update(identity(path))
        else:
            require(
                identity(path) == {k: item[k] for k in identity(path)},
                f"runtime identity変更。完全verifyが必要: {item['path']}",
            )
    if full:
        save(config_path, record)
    return record


def check_versions(expected):
    for name, pin in expected.items():
        require(version(name) == pin, f"依存版不一致: {name}")


def load_fixed_vector(assets_dir: Path) -> CompressedFastTextKeyedVectors:
    """現行adapter同様、固定bytesだけを公開pickle loaderへ渡す。"""
    data = metadata()
    item = next(x for x in data["assets"] if x["id"] == "fasttext-ja-mini")
    with (assets_dir / item["path"]).open("rb") as stream:
        payload = stream.read(item["bytes"] + 1)
    require(
        len(payload) == item["bytes"] and hashlib.sha256(payload).hexdigest() == item["sha256"],
        "許可した固定vectorモデルではありません",
    )
    check_versions(data["vector_python"])
    from compress_fasttext.models import CompressedFastTextKeyedVectors

    with tempfile.TemporaryDirectory(prefix="native-fixed-vector-") as directory:
        snapshot = Path(directory) / "fasttext-ja-mini"
        snapshot.write_bytes(payload)
        model = CompressedFastTextKeyedVectors.load(str(snapshot))
    require(model.vector_size == 300, "固定vectorの次元不一致")
    return model
