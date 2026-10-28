"""圧縮参照の検査APIとCLIの、利用者から見える振る舞いを確かめる。"""

import io
import json
from dataclasses import dataclass, replace

import pytest
from test_examples import make_analysis
from test_reference import QUERY as REFERENCE_QUERY
from test_reference import setup_reference as setup_reference

from native_japanese import Endpoint, Query, Relation, reference
from native_japanese import __main__ as cli
from native_japanese.models import PredicateGraph, PredicateNode
from native_japanese.service import Inspector
from native_japanese.support import Support


@dataclass(frozen=True)
class StubAnalysis:
    text: str
    relations: tuple[Relation, ...]

    def validate_comparison(self):
        pass


QUERY = Query(
    PredicateGraph(0, (PredicateNode("畳む", "動詞-一般", (), None, None),)),
    0,
    "obj",
    ("重複",),
    "dependent",
)
ALTERNATE = replace(QUERY, noun=("別表記",))
TEXT = "😀重複を間に挟んで畳む。"
RELATION = Relation(Endpoint("畳む", ((9, 11),)), Endpoint("重複", ((1, 3),)), (QUERY,))
ANALYSIS = StubAnalysis(TEXT, (RELATION,))


def inspector(monkeypatch, *, assessments=None, analysis=ANALYSIS):
    import native_japanese.service as service

    class Parser:
        def __init__(self):
            self.closed = False
            self.batches = []

        def analyze(self, text):
            if text == "解析失敗":
                raise RuntimeError("解析に失敗しました。")
            return replace(analysis, text=text) if text.strip() else StubAnalysis(text, ())

        def analyze_many(self, texts):
            texts = tuple(texts)
            self.batches.append(texts)
            return (self.analyze(text) for text in texts)

        def close(self):
            self.closed = True

    class Reference:
        def __init__(self, reference_path, assets_dir):
            self.calls = []
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def assess(self, query):
            self.calls.append(query)
            return (assessments or {}).get(query, Support(False, 0.2))

    monkeypatch.setattr(service, "Parser", Parser)
    monkeypatch.setattr(service, "Reference", Reference)
    return Inspector("reference", "assets")


def test_indexed_candidates_suppress_warning_without_claiming_direct_observation(monkeypatch):
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(QUERY, ALTERNATE)),))
    with inspector(
        monkeypatch, assessments={ALTERNATE: Support(True, None)}, analysis=analysis
    ) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].status == "indexed"
        assert instance.judges[0].reference.calls == [QUERY, ALTERNATE]
        encoded = cli.result_json(result)
        assert encoded["relations"][0]["queries"][1]["support"]["indexed"]
        assert "observed" not in json.dumps(encoded)
    assert instance.parser.closed and instance.judges[0].reference.closed


def test_no_support_reports_usage_error_at_discontinuous_utf16_ranges(monkeypatch):
    with inspector(monkeypatch) as instance:
        result = instance.lint(TEXT)
        assert {item.range for item in result.diagnostics} == {(1, 3), (9, 11)}
        encoded = cli.result_json(result)
        assert encoded["offsetEncoding"] == "utf-16"
        assert {tuple(item["range"]) for item in encoded["diagnostics"]} == {(2, 4), (10, 12)}
        assert "unavailable" not in encoded
        assert "analysis" not in encoded
        assert encoded["diagnostics"][0]["message"] == (
            "「重複」と「畳む」の組み合わせに合う用例が見つかりません。表現を見直してください。"
        )
    with inspector(monkeypatch, assessments={QUERY: Support(False, None)}) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics
        assert result.relations[0].status == "review"
        assert result.relations[0].score is None
        assert "unavailable" not in cli.result_json(result)
    with inspector(monkeypatch, assessments={QUERY: Support(False, 0.35)}) as instance:
        assert instance.lint(TEXT).relations[0].status == "supported"
        assert instance.lint(TEXT).diagnostics == ()


def test_relation_without_query_is_analysis_failure(monkeypatch):
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=()),))
    with inspector(monkeypatch, analysis=analysis) as instance:
        with pytest.raises(ValueError, match="照会条件"):
            instance.lint(TEXT)


def test_all_comparable_candidates_use_greatest_support(monkeypatch):
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(QUERY, ALTERNATE)),))
    supports = {QUERY: Support(False, 0.2), ALTERNATE: Support(False, 0.4)}
    with inspector(monkeypatch, assessments=supports, analysis=analysis) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].score == 0.4
        assert instance.judges[0].reference.calls == [QUERY, ALTERNATE]


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_support_stops_inspection(monkeypatch, score):
    with inspector(monkeypatch, assessments={QUERY: Support(False, score)}) as instance:
        with pytest.raises(ValueError, match="比較結果"):
            instance.lint(TEXT)


def test_serve_retains_ids_errors_empty_unicode_and_batch_parser(monkeypatch, capsys):
    instance = inspector(monkeypatch)
    requests = [
        {"id": "一", "texts": [TEXT, "", "  \n"]},
        {"id": "不正", "texts": [1]},
        {"id": "失敗", "texts": ["解析失敗"]},
        {"id": "二", "texts": [TEXT]},
    ]
    monkeypatch.setattr(
        cli.sys, "stdin", io.StringIO("\n" + "\n".join(json.dumps(row) for row in requests))
    )
    with instance:
        cli.serve(instance)
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [response["id"] for response in output] == ["一", "不正", "失敗", "二"]
    assert output[0]["results"][1]["diagnostics"] == []
    assert output[0]["results"][2]["diagnostics"] == []
    assert "error" in output[1] and "error" in output[2]
    assert output[3]["results"][0]["diagnostics"] == output[0]["results"][0]["diagnostics"]
    assert instance.parser.batches == [(TEXT, "", "  \n"), ("解析失敗",), (TEXT,)]
    assert instance.parser.closed


def test_serve_reports_no_usage_and_preserves_real_vector_failure(
    setup_reference, monkeypatch, capsys
):
    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    instance = inspector(monkeypatch)

    def analyze(text):
        noun = "追加の未収録" if text == TEXT else "非有限"
        relation = replace(RELATION, queries=(replace(REFERENCE_QUERY, noun=(noun,)),))
        return replace(ANALYSIS, text=text, relations=(relation,))

    monkeypatch.setattr(instance.parser, "analyze", analyze)
    requests = [
        {"id": "不足", "texts": [TEXT]},
        {"id": "異常", "texts": [TEXT + "計算異常"]},
    ]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests))))
    with instance, reference.Reference(output, assets) as runtime:
        instance.judges[0].reference = runtime
        cli.serve(instance)
    shortage, failure = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "error" not in shortage
    assert shortage["results"][0]["diagnostics"]
    assert "unavailable" not in shortage["results"][0]
    assert failure["id"] == "異常" and "error" in failure and "results" not in failure
    assert "非有限" in failure["error"]["message"]


def test_generalization_support_and_vectorless_usage_addition(setup_reference, monkeypatch):
    from test_examples import lines, record

    from native_japanese.examples import Examples

    source, output, assets = setup_reference
    reference.build_reference(source, output, assets)
    probe = replace(REFERENCE_QUERY, noun=("東", "東"))
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(probe,)),))
    with inspector(monkeypatch, analysis=analysis) as instance:
        with reference.Reference(output, assets) as runtime:
            instance.judges[0].reference = runtime
            result = instance.lint(TEXT)
            assert result.diagnostics == ()
            assert result.relations[0].status == "supported"
            assert not result.relations[0].queries[0].support.indexed

    probe = replace(REFERENCE_QUERY, noun=("追加の未収録",))
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(probe,)),))
    with inspector(monkeypatch, analysis=analysis) as instance:
        with reference.Reference(output, assets) as runtime:
            instance.judges[0].reference = runtime
            before = instance.lint(TEXT)
            assert before.diagnostics
            assert before.relations[0].score is None
        with Examples(source, writable=True) as examples:
            examples.import_jsonl(lines(record(probe)))
        reference.build_reference(source, output, assets)
        with reference.Reference(output, assets) as runtime:
            instance.judges[0].reference = runtime
            after = instance.lint(TEXT)
            assert after.diagnostics == ()
            assert after.relations[0].status == "indexed"
            assert after.relations[0].score is None


def test_cli_reports_initialization_failure_without_json_noise(monkeypatch, capsys):
    import native_japanese.service as service

    def fail(*args):
        raise RuntimeError("参照が一致しません。")

    monkeypatch.setattr(service, "Inspector", fail)
    assert cli.main(["serve", "--reference", "不正", "--assets-dir", "assets"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "参照が一致しません" in captured.err


def test_serve_can_emit_surrogate_ids_and_recover_from_nonfinite_ids(monkeypatch):
    instance = inspector(monkeypatch)
    requests = '{"id":"\\ud800","texts":[]}\n{"id":NaN,"texts":[]}\n{"id":"後続","texts":[]}\n'
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(requests))
    raw = io.BytesIO()
    output = io.TextIOWrapper(raw, encoding="utf-8", errors="strict")
    with monkeypatch.context() as patch:
        patch.setattr(cli.sys, "stdout", output)
        with instance:
            cli.serve(instance)
    responses = [json.loads(line) for line in raw.getvalue().decode("utf-8").splitlines()]
    assert responses[0] == {"id": "\ud800", "results": []}
    assert responses[1]["id"] is None and "error" in responses[1]
    assert responses[2] == {"id": "後続", "results": []}


def test_cli_prepare_builds_compiled_reference(monkeypatch, capsys):
    import native_japanese.reference as reference

    calls = []

    def build(*args):
        calls.append(args)
        return {"usages": 2}

    monkeypatch.setattr(reference, "build_reference", build)
    assert (
        cli.main(
            [
                "prepare",
                "--examples",
                "source.sqlite",
                "--reference",
                "compiled.sqlite",
                "--assets-dir",
                "assets",
            ]
        )
        == 0
    )
    assert calls == [("source.sqlite", "compiled.sqlite", "assets")]
    assert json.loads(capsys.readouterr().out) == {"usages": 2}


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -1.1, 1.1])
def test_invalid_threshold_is_rejected_before_initializing(threshold):
    with pytest.raises(ValueError, match="threshold"):
        Inspector("reference", "assets", threshold)


def test_cli_extract_import_preserves_source_and_new_analysis_contract(
    monkeypatch, capsys, tmp_path
):
    import native_japanese.parser as parser_module
    from native_japanese.examples import Examples

    instances = []

    class Parser:
        def __init__(self):
            self.closed = False
            instances.append(self)

        def analyze(self, text):
            assert text == make_analysis(QUERY).text
            return make_analysis(QUERY)

        def close(self):
            self.closed = True

    monkeypatch.setattr(parser_module, "Parser", Parser)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(make_analysis(QUERY).text))
    assert (
        cli.main(["extract", "--source-id", "出典", "--source-url", "https://example.test/source"])
        == 0
    )
    extracted = capsys.readouterr().out
    record = json.loads(extracted)
    assert record["text"] == make_analysis(QUERY).text
    assert record["schema_version"] == 4
    assert record["doc_base64"]
    assert record["source_id"] == "出典"
    assert record["source_url"] == "https://example.test/source"
    assert instances[0].closed
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(extracted))
    path = tmp_path / "examples.sqlite"
    assert cli.main(["import", "--examples", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["usages_added"] == 1
    with Examples(path) as examples:
        assert examples.lookup(QUERY)[0]["source_id"] == "出典"


def test_reference_is_closed_when_parser_initialization_fails(monkeypatch):
    import native_japanese.service as service

    closed = []

    class Reference:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed.append(True)

    def fail():
        raise RuntimeError("モデルがありません。")

    monkeypatch.setattr(service, "Reference", Reference)
    monkeypatch.setattr(service, "Parser", fail)
    with pytest.raises(RuntimeError, match="モデルがありません"):
        Inspector("reference", "assets")
    assert closed == [True]


@pytest.mark.parametrize("standard_input", [False, True])
def test_cli_extract_preserves_crlf_and_full_document(
    standard_input, tmp_path, monkeypatch, capsys
):
    from native_japanese.examples import analysis_from_record
    from native_japanese.parser import Parser

    text = "電気を消す。\r\n本を読む。\r\n"
    parser = Parser()
    monkeypatch.setattr("native_japanese.parser.Parser", lambda: parser)
    args = ["extract", "--source-id", "出典", "--source-url", "url"]
    if standard_input:
        # TextIOWrapperの既定newline=Noneでも、原bytesを読む。
        monkeypatch.setattr(
            cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8")
        )
    else:
        path = tmp_path / "原文.txt"
        path.write_bytes(text.encode("utf-8"))
        args.append(str(path))
    assert cli.main(args) == 0
    row = json.loads(capsys.readouterr().out)
    analysis = analysis_from_record(row)
    assert analysis.text == analysis.restore_doc().text == text
    assert len(analysis.relations) == 2
