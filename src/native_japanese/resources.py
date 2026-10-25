"""固定資源の検証と、許可済みvectorのloadを共有する。"""

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
        path.is_file() and path.stat().st_size == item["bytes"],
        f"資源ファイルの有無またはサイズを確認してください: {item['id']}",
    )
    require(sha256(path) == item["sha256"], f"資源ファイルのSHA-256が一致しません: {item['id']}")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def check_versions(expected):
    for name, pin in expected.items():
        require(version(name) == pin, f"依存ライブラリのバージョンが一致しません: {name}")


def load_fixed_vector(assets_dir: Path) -> CompressedFastTextKeyedVectors:
    """現行adapter同様、固定bytesだけを公開pickle loaderへ渡す。"""
    data = metadata()
    item = next(x for x in data["assets"] if x["id"] == "fasttext-ja-mini")
    with (assets_dir / item["path"]).open("rb") as stream:
        payload = stream.read(item["bytes"] + 1)
    require(
        len(payload) == item["bytes"] and hashlib.sha256(payload).hexdigest() == item["sha256"],
        "所定の固定単語ベクトルモデルではありません。",
    )
    check_versions(data["vector_python"])
    from compress_fasttext.models import CompressedFastTextKeyedVectors

    with tempfile.TemporaryDirectory(prefix="native-fixed-vector-") as directory:
        snapshot = Path(directory) / "fasttext-ja-mini"
        snapshot.write_bytes(payload)
        model = CompressedFastTextKeyedVectors.load(str(snapshot))
    require(model.vector_size == 300, "固定の単語ベクトルの次元数が一致しません。")
    return model
