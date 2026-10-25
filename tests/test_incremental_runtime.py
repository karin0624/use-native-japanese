"""圧縮参照の検査APIとCLIの、利用者から見える振る舞いを確かめる。"""

import io
import json
from dataclasses import replace

import pytest

from native_japanese import Analysis, Endpoint, Query, Relation, Unavailable
from native_japanese import __main__ as cli
from native_japanese.service import Inspector
from native_japanese.support import Support

QUERY = Query(("畳む",), "VERB", "obj", ("重複",), "NOUN", "dependent")
ALTERNATE = replace(QUERY, noun=("別表記",))
TEXT = "😀重複を間に挟んで畳む。"
RELATION = Relation(Endpoint("畳む", ((9, 11),)), Endpoint("重複", ((1, 3),)), (QUERY,))
ANALYSIS = Analysis(TEXT, (RELATION,), ())


def inspector(monkeypatch, *, assessments=None, analysis=ANALYSIS):
    import native_japanese.service as service

    class Parser:
        def __init__(self):
            self.closed = False
            self.batches = []

        def analyze(self, text):
            if text == "解析失敗":
                raise RuntimeError("解析に失敗しました。")
            return replace(analysis, text=text) if text.strip() else Analysis(text, (), ())

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
            return (assessments or {}).get(query, Support(False, 0.2, None))

    monkeypatch.setattr(service, "Parser", Parser)
    monkeypatch.setattr(service, "Reference", Reference)
    return Inspector("reference", "assets")


def test_indexed_candidates_suppress_warning_without_claiming_direct_observation(monkeypatch):
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(QUERY, ALTERNATE)),))
    with inspector(
        monkeypatch, assessments={ALTERNATE: Support(True, None, None)}, analysis=analysis
    ) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].status == "indexed"
        assert instance.reference.calls == [QUERY, ALTERNATE]
        encoded = cli.result_json(result)
        assert encoded["relations"][0]["queries"][1]["support"]["indexed"]
        assert "observed" not in json.dumps(encoded)
    assert instance.parser.closed and instance.reference.closed


def test_support_threshold_unavailable_and_discontinuous_utf16_ranges(monkeypatch):
    analysis = replace(ANALYSIS, unavailable=(Unavailable(((0, 1),), "公開情報不足"),))
    with inspector(monkeypatch, analysis=analysis) as instance:
        result = instance.lint(TEXT)
        assert {item.range for item in result.diagnostics} == {(1, 3), (9, 11)}
        encoded = cli.result_json(result)
        assert encoded["offsetEncoding"] == "utf-16"
        assert {tuple(item["range"]) for item in encoded["diagnostics"]} == {(2, 4), (10, 12)}
        assert encoded["unavailable"] == [
            {"kind": "analysis", "ranges": [[0, 2]], "reason": "公開情報不足"}
        ]
        assert "analysis" not in encoded
        assert encoded["diagnostics"][0]["message"] == (
            "「重複」と「畳む」の組み合わせが不自然かもしれません。表現を確認してください。"
        )
    with inspector(monkeypatch, assessments={QUERY: Support(False, None, "比較不能")}) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].status == "unavailable"
        assert result.relations[0].reasons == ("比較不能",)
        assert cli.result_json(result)["unavailable"] == [
            {"kind": "reference", "ranges": [[10, 12], [2, 4]], "reason": "比較不能"}
        ]
    with inspector(monkeypatch, assessments={QUERY: Support(False, 0.35, None)}) as instance:
        assert instance.lint(TEXT).relations[0].status == "supported"
        assert instance.lint(TEXT).diagnostics == ()


@pytest.mark.parametrize(
    ("relation", "reason"),
    [
        (replace(RELATION, queries=()), "比較に必要な語の関係を解析結果から取得できません。"),
        (
            replace(RELATION, unavailable_reasons=("公開項の原文位置がありません。",)),
            "公開項の原文位置がありません。比較不能",
        ),
    ],
)
def test_relation_without_public_analysis_is_not_reference_shortage(monkeypatch, relation, reason):
    analysis = replace(ANALYSIS, relations=(relation,))
    with inspector(
        monkeypatch, analysis=analysis, assessments={QUERY: Support(False, None, "比較不能")}
    ) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert cli.result_json(result)["unavailable"] == [
            {"kind": "analysis", "ranges": [[10, 12], [2, 4]], "reason": reason}
        ]


def test_all_comparable_candidates_use_greatest_support(monkeypatch):
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(QUERY, ALTERNATE)),))
    supports = {QUERY: Support(False, 0.2, None), ALTERNATE: Support(False, 0.4, None)}
    with inspector(monkeypatch, assessments=supports, analysis=analysis) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].score == 0.4
        assert instance.reference.calls == [QUERY, ALTERNATE]


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
            assert text == TEXT
            return ANALYSIS

        def close(self):
            self.closed = True

    monkeypatch.setattr(parser_module, "Parser", Parser)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(TEXT))
    assert (
        cli.main(["extract", "--source-id", "出典", "--source-url", "https://example.test/source"])
        == 0
    )
    extracted = capsys.readouterr().out
    record = json.loads(extracted)
    assert record["predicate"] == ["畳む"]
    assert record["noun"] == ["重複"]
    assert record["dependency"] == "obj"
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
