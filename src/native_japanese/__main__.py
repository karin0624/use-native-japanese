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

    relations = []
    for assessment in result.relations:
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
    }


def emit(value):
    # JSON escapeで孤立surrogateを含む要求IDもUTF-8へ安全に返す。
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def read_text(source):
    """標準入力の改行変換も避け、UTF-8の原文をそのまま読む。"""
    if source is sys.stdin and hasattr(source, "buffer"):
        return source.buffer.read().decode("utf-8")
    return source.read()


def serve(inspector):
    """要求ごとの失敗を返し、後続のJSONL要求を引き続き処理する。"""
    for line in sys.stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("要求には、idとtextsを含むJSONオブジェクトを指定してください。")
            try:
                json.dumps(request.get("id"), allow_nan=False)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "要求のidにはJSONで表せる値を指定してください。NaNや無限大は使えません。"
                ) from error
            request_id = request.get("id")
            if "id" not in request or not isinstance(request.get("texts"), list):
                raise ValueError("要求には、idと、文字列の配列textsを指定してください。")
            if not all(isinstance(text, str) for text in request["texts"]):
                raise ValueError("textsの各要素には文字列が必要です。")
            with redirect_stdout(sys.stderr):
                results = [result_json(result) for result in inspector.lint_many(request["texts"])]
            response = {"id": request_id, "results": results}
        except Exception as error:
            response = {"id": request_id, "error": {"message": str(error)}}
        emit(response)


def arguments(argv):
    parser = argparse.ArgumentParser(description="語の組み合わせを用例に照らして検査します。")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-parser", help="解析モデルを取得し、解析器の起動を確認します。")
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
                    else stack.enter_context(open(options.input, encoding="utf-8", newline=""))
                )
            if options.command == "setup-parser":
                from .models import PARSER_VERSIONS
                from .parser import Parser

                with redirect_stdout(sys.stderr):
                    parser = Parser()
                    stack.callback(parser.close)
                    parser.analyze("日本語を解析する。")
                emit({"parser_versions": PARSER_VERSIONS})
            elif options.command == "import":
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
                    raise ValueError("出典IDと出典URLには空でない文字列を指定してください。")
                with redirect_stdout(sys.stderr):
                    parser = Parser()
                    stack.callback(parser.close)
                    analysis = parser.analyze(read_text(source))
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
                        result = result_json(inspector.lint(read_text(source)))
                    emit(result)
        return 0
    except Exception as error:
        print(f"native-japanese: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
