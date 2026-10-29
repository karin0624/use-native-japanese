"""固定資源を検証して、本文を含まないnpm配布用の来歴を作る。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from native_japanese.models import NORMALIZATION_IDENTITY

REFERENCE_BYTES = 107048960
REFERENCE_SHA256 = "3f8d1bb31f1f4f4bb1c629eb83e2a4451ca812319b5d7cc481e16823b4aeea01"
MODEL_SHA256 = "f1435852226301053f2cae0196b61ab291826d5c4a2235215ff9410f728815d7"
REVISION = "4b58cd23ae34af0f77c424b98807901cf472c377"
PARSER_MODEL = {
    "repository": "ku-nlp/kwja-checkpoints",
    "release": "v2.4",
    "revision": "4c5db234089ca9e524fc5d32d383bc45e6fe27a2",
    "size": "base",
    "tasks": ["char", "word"],
    "wordWindow": {
        "default": 256,
        "extended": 512,
        "selection": "maximum-sentence-encoding-with-special-tokens",
    },
    "checkpoints": [
        {
            "file": "char_deberta-v2-base-wwm.ckpt",
            "bytes": 423739270,
            "sha256": "0643c468321725e076ed598c6869a33d359a2f14b1c1334b3c71d9d74eeed3d1",
        },
        {
            "file": "word_deberta-v2-base.ckpt",
            "bytes": 532226786,
            "sha256": "26a0a2fbcc5ba977d6a837936b6d427784271edb080be4539995a7b118397813",
        },
    ],
}


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    if path.is_symlink():
        raise ValueError(f"symlinkには書き込めません: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--passages", required=True, action="append", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if len(args.passages) != 1 or digest(args.passages[0]) != (
        "be556530f1ece9213fda4c3203038a4acad06081c1a33d030cb7b13c5829c3c5"
    ):
        raise ValueError("固定した参照本文ファイルのhashと一致しません")
    articles: dict[str, dict] = {}
    seen: set[str] = set()
    for path in args.passages:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            article_id, passage_id = row["docid"].split("#")
            if (
                row["dataset"] != "miracl/miracl-corpus"
                or row["snapshot"] != "20190201"
                or row["revision"] != REVISION
                or row["license"] != "CC-BY-SA-3.0"
                or article_id != row["article_id"]
                or not article_id.isdecimal()
                or not passage_id.isdecimal()
                or row["source"] != f"https://ja.wikipedia.org/?curid={article_id}"
                or not isinstance(row["title"], str)
                or not row["title"]
                or row["docid"] in seen
            ):
                raise ValueError("固定した出典契約と一致しません")
            seen.add(row["docid"])
            article = articles.setdefault(
                article_id,
                {
                    "articleId": article_id,
                    "title": row["title"],
                    "url": row["source"],
                    "passages": [],
                },
            )
            if article["title"] != row["title"]:
                raise ValueError("同じ記事の題名が一致しません")
            article["passages"].append(row["docid"])
    if len(articles) != 1025 or len(seen) != 6463:
        raise ValueError("固定した1,025記事・6,463区間ではありません")
    for source, size, expected in (
        (args.reference, REFERENCE_BYTES, REFERENCE_SHA256),
        (args.model, 21944687, MODEL_SHA256),
    ):
        if source.stat().st_size != size or digest(source) != expected:
            raise ValueError(f"固定資源のsize/hashと一致しません: {source}")
    if args.output_dir.is_symlink():
        raise ValueError("出力先にsymlinkは使えません")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for source, name in (
        (args.reference, "reference.sqlite3"),
        (args.model, "fasttext-ja-mini"),
    ):
        destination = args.output_dir / name
        if destination.is_symlink():
            raise ValueError(f"symlinkには書き込めません: {destination}")
        shutil.copyfile(source, destination)
        if digest(destination) != digest(source):
            raise ValueError("コピー後のhashが一致しません")
    sources_path = args.output_dir / "sources.json"
    write_json(
        sources_path,
        {
            "schemaVersion": 1,
            "dataset": "miracl/miracl-corpus",
            "snapshot": "20190201",
            "revision": REVISION,
            "sourceLicense": "CC-BY-SA-3.0",
            "attribution": "Wikimedia projects editors; MIRACL contributors",
            "datasetUrl": "https://huggingface.co/datasets/miracl/miracl-corpus",
            "dumpUrl": "https://archive.org/details/jawiki-20190201",
            "articleCount": len(articles),
            "passageCount": len(seen),
            "articles": sorted(articles.values(), key=lambda article: int(article["articleId"])),
        },
    )
    write_json(
        args.output_dir / "manifest.json",
        {
            "schemaVersion": 1,
            "reference": {
                "file": "reference.sqlite3",
                "bytes": REFERENCE_BYTES,
                "sha256": REFERENCE_SHA256,
                "license": "CC-BY-SA-4.0",
            },
            "model": {
                "file": "fasttext-ja-mini",
                "bytes": 21944687,
                "sha256": MODEL_SHA256,
                "license": "CC-BY-SA-3.0",
                "source": "https://doi.org/10.5281/zenodo.4905385",
                "version": "1.0",
                "sourceChecksum": "md5:6f50bff91970a95743040320f76872ef",
                "recordLicense": "CC-BY-4.0",
                "modified": False,
            },
            "provenance": {
                "sourcesFile": "sources.json",
                "sourcesSha256": digest(sources_path),
                "selectionManifestSha256": (
                    "7ec795750975091fd671951577a892c89e7245113568e151b70bec0121143d4d"
                ),
                "passagesSha256": digest(args.passages[0]),
                "parserModel": PARSER_MODEL,
                "referenceTransformation": (
                    "固定した6,463区間の全文をKWJAへ渡す。原文全文・全KNP解析・"
                    "公式文字正規化と原文の位置対応はschema 6の構築用正本に保持する。"
                    "公式tokenizerで得る各文の長さと特殊token数によりwordの窓を256または512に設定する。"
                    "原文・文境界・モデルの重みは変更しない。"
                    "配布対象のschema 4のDBは正本から生成する派生キャッシュである。"
                    "基本句・形態素・述語項構造をその解析単位のまま参照し、"
                    "述語構成の形態素列・代表表記（なければ原形と読み）・品詞・活用情報・"
                    "主辞を静的な完全一致キーにする。"
                    "同じ文節内の接頭辞基本句の連鎖は、元の基本句依存辺とともに述語構成へ含める。"
                    "形態素間に存在しない依存辺は補わない。基本句の係り受けとPASの格を"
                    "区別し、元の対象と述語の接続位置・役割・向きを保持する。"
                    "実在述語の完全一致表をBloom filterより先に確認する。"
                    "名詞特徴だけは語識別の表記部の列をfastTextで合成し、異なる特徴列の300次元"
                    "ベクトルを同じ厳密な述語構成・接続位置の名詞組合せの群内で等重み平均する。"
                    "名詞との照会は格・役割・向きを共通化し、その元情報はKNPと正本のviewへ保持する。"
                    "述語同士の関係は両構成・両接続点・元関係・役割・向きの実共起を完全一致で照会する。"
                ),
                "normalizationIdentity": NORMALIZATION_IDENTITY,
            },
        },
    )


if __name__ == "__main__":
    main()
