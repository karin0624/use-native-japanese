"""固定資源を検証して、本文を含まないnpm配布用の来歴を作る。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

REFERENCE_SHA256 = "b0ff104d3aac398665adb893f56626e76520a1c6c0785d5a61f0705ceab8b1b5"
MODEL_SHA256 = "f1435852226301053f2cae0196b61ab291826d5c4a2235215ff9410f728815d7"
REVISION = "4b58cd23ae34af0f77c424b98807901cf472c377"


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
    if len(args.passages) != 2 or {digest(path) for path in args.passages} != {
        "cb48121ac61267d6272e30329b3cdaf7b0a99f5d3e9e5421104a2d3aaef987dc",
        "0bb0ef39ce173fd9f40a3b99f92cda1cba52533448cbd5fc137a235f9f990272",
    }:
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
        (args.reference, 70631424, REFERENCE_SHA256),
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
                "bytes": 70631424,
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
                "referenceTransformation": (
                    "既存Docと元Tokenの品詞・細分類・依存辺を保ち、GiNZA公開lemmaと"
                    "原文へ対応させたASCII識別子Spanから抽出した用例集合のBloom filterと、"
                    "異なる名詞列を等重みとした300次元ベクトル群平均。原文は収録しない。"
                ),
                "normalizationIdentity": "ginza-bunsetu-lemma-no-ner-identifier-span-v5",
            },
        },
    )


if __name__ == "__main__":
    main()
