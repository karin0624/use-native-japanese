"""検査・常駐JSONL・正規化用例の交換を提供する。"""

import argparse
import json
import sys
from contextlib import ExitStack, redirect_stdout
from dataclasses import asdict

from .examples import Examples, extract_records


def result_json(result) -> dict:
    """外部へ返す全範囲をUTF-16へ変換し、原文や関係を重複展開しない。"""
    text = result.analysis.text
    offsets = [0]
    for character in text:
        offsets.append(offsets[-1] + (2 if ord(character) > 0xFFFF else 1))

    def ranges(spans):
        return [[offsets[start], offsets[end]] for start, end in spans]

    unavailable = [
        {"kind": "analysis", "ranges": ranges(item.ranges), "reason": item.reason}
        for item in result.analysis.unavailable
    ]
    relations = []
    for assessment in result.relations:
        if assessment.status == "unavailable":
            relation = assessment.relation
            spans = (
                *relation.predicate.ranges,
                *(relation.argument.ranges if relation.argument else ()),
            )
            unavailable.append(
                {
                    "kind": "analysis"
                    if not relation.queries or relation.unavailable_reasons
                    else "reference",
                    "ranges": ranges(dict.fromkeys(spans)),
                    "reason": "；".join(assessment.reasons),
                }
            )
        row = asdict(assessment)
        relation = row.pop("relation")
        for field in ("predicate", "argument"):
            endpoint = relation[field]
            if endpoint is not None:
                endpoint["ranges"] = ranges(endpoint["ranges"])
        # 照会は支持情報と共に既に含まれる。
        relation.pop("queries")
        relations.append({**relation, **row})
    return {
        "offsetEncoding": "utf-16",
        "diagnostics": [
            {"range": ranges((item.range,))[0], "message": item.message}
            for item in result.diagnostics
        ],
        "relations": relations,
        "unavailable": unavailable,
    }


def emit(value):
    # JSON escapeで孤立surrogateを含む要求IDもUTF-8へ安全に返す。
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def serve(inspector):
    """要求ごとの失敗を返し、後続のJSONL要求を引き続き処理する。"""
    for line in sys.stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("要求にはidとtextsを持つJSON objectが必要です。")
            try:
                json.dumps(request.get("id"), allow_nan=False)
            except (TypeError, ValueError) as error:
                raise ValueError("要求idには有限のJSON値が必要です。") from error
            request_id = request.get("id")
            if "id" not in request or not isinstance(request.get("texts"), list):
                raise ValueError("要求にはidとtexts配列が必要です。")
            if not all(isinstance(text, str) for text in request["texts"]):
                raise ValueError("textsの各要素には文字列が必要です。")
            with redirect_stdout(sys.stderr):
                results = [result_json(result) for result in inspector.lint_many(request["texts"])]
            response = {"id": request_id, "results": results}
        except Exception as error:
            response = {"id": request_id, "error": {"message": str(error)}}
        emit(response)


def arguments(argv):
    parser = argparse.ArgumentParser(description="語の組合せを用例に照らして検査します。")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument("--examples", required=True)
    command.add_argument("--reference", required=True)
    command.add_argument("--assets-dir", required=True)
    for name in ("lint", "serve"):
        command = commands.add_parser(name)
        command.add_argument("--reference", required=True)
        command.add_argument("--assets-dir", required=True)
        command.add_argument("--threshold", type=float, default=0.35)
        if name == "lint":
            command.add_argument("input", nargs="?", default="-")
    command = commands.add_parser("extract")
    command.add_argument("--source-id", required=True)
    command.add_argument("--source-url", required=True)
    command.add_argument("input", nargs="?", default="-")
    command = commands.add_parser("import")
    command.add_argument("--examples", required=True)
    command.add_argument("input", nargs="?", default="-")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    options = arguments(argv)
    try:
        with ExitStack() as stack:
            if options.command in ("lint", "extract", "import"):
                source = (
                    sys.stdin
                    if options.input == "-"
                    else stack.enter_context(open(options.input, encoding="utf-8"))
                )
            if options.command == "import":
                examples = stack.enter_context(Examples(options.examples, writable=True))
                emit(examples.import_jsonl(source))
            elif options.command == "prepare":
                from .reference import build_reference

                with redirect_stdout(sys.stderr):
                    prepared = build_reference(
                        options.examples, options.reference, options.assets_dir
                    )
                emit(prepared)
            elif options.command == "extract":
                from .parser import Parser

                if not options.source_id.strip() or not options.source_url.strip():
                    raise ValueError("出典IDと出典の所在には空でない文字列が必要です。")
                with redirect_stdout(sys.stderr):
                    parser = Parser()
                    stack.callback(parser.close)
                    analysis = parser.analyze(source.read())
                for record in extract_records(analysis, options.source_id, options.source_url):
                    emit(record)
            else:
                from .service import Inspector

                with redirect_stdout(sys.stderr):
                    inspector = stack.enter_context(
                        Inspector(
                            options.reference,
                            options.assets_dir,
                            options.threshold,
                        )
                    )
                if options.command == "serve":
                    serve(inspector)
                else:
                    with redirect_stdout(sys.stderr):
                        result = result_json(inspector.lint(source.read()))
                    emit(result)
        return 0
    except Exception as error:
        print(f"native-japanese: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
