"""固定原本のGiNZA解析を再開可能なSQLite正本へ保存する。"""

import argparse
import json
import os
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from native_japanese.corpus import parse_corpus
from native_japanese.parser import GinzaParser


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--expected-documents", type=int, required=True)
    parser.add_argument(
        "--limit", type=int, help="先頭だけの限定snapshot。全量とは別の出力を指定する"
    )
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    started = time.monotonic()

    def log(event: str, **values) -> None:
        print(
            json.dumps({"event": event, **values}, ensure_ascii=False), file=sys.stderr, flush=True
        )

    log("started", pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat())
    try:
        corpus = parse_corpus(
            args.source,
            args.output,
            parser=GinzaParser(),
            expected_source_sha256=args.source_sha256,
            expected_documents=args.expected_documents,
            limit=args.limit,
            batch_size=args.batch_size,
            progress=lambda status: log("progress", **status),
        )
        print(json.dumps(corpus.manifest, ensure_ascii=False, indent=2), flush=True)
    finally:
        log(
            "runtime",
            elapsed_seconds=time.monotonic() - started,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
        )


if __name__ == "__main__":
    main()
