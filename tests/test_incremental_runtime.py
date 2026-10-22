"""追加用例と公開API・JSONLの、利用者から見える振る舞いを確かめる。"""

import io
import json
from dataclasses import replace

import pytest

from native_japanese import Analysis, Endpoint, Query, Relation, Unavailable
from native_japanese import __main__ as cli
from native_japanese.examples import Examples, extract_records
from native_japanese.service import Inspector
from native_japanese.support import Support

QUERY = Query("畳む/たたむ", "動", "ヲ", "重複/じゅうふく")
ALTERNATE = replace(QUERY, noun="別表記/べつひょうき")
TEXT = "😀重複を間に挟んで畳む。"
RELATION = Relation(Endpoint("畳む", ((9, 11),)), Endpoint("重複", ((1, 3),)), (QUERY,))
ANALYSIS = Analysis(TEXT, (RELATION,), ())


def record(source_id="出典A", observation_id="出現1", query=QUERY):
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(query,)),))
    return {
        **next(extract_records(analysis, source_id, "https://example.test/source")),
        "observation_id": observation_id,
    }


def lines(*records):
    return [json.dumps(row, ensure_ascii=False) + "\n" for row in records]


def test_import_preserves_sources_candidates_and_rolls_back(tmp_path):
    path = tmp_path / "追加.sqlite"
    with Examples(path, writable=True) as examples:
        rows = lines(record(), record("出典B"), record(query=ALTERNATE))
        assert examples.import_jsonl(iter(rows)) == {
            "records": 3,
            "usages_added": 2,
            "observations_added": 3,
        }
        size = path.stat().st_size
        assert examples.import_jsonl(rows)["observations_added"] == 0
        assert path.stat().st_size == size
        assert len(examples.lookup(QUERY)) == 1
        assert examples.connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 3
        assert len(examples.lookup(ALTERNATE)) == 1
        extra = record("不正前にある出典", "出現2")
        with pytest.raises(ValueError, match="3行目"):
            examples.import_jsonl(["\n", *lines(extra), "{}\n"])
        assert examples.connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 3
        assert examples.lookup(replace(QUERY, predicate_type="名")) == ()
    with Examples(path) as reopened:
        assert len(reopened.lookup(QUERY)) == 1


def test_extract_keeps_whole_words_and_occurrence_identity():
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(QUERY, ALTERNATE)),))
    first = list(extract_records(analysis, "出典", "https://example.test"))
    assert len(first) == 2
    assert first[0]["noun"] == QUERY.noun
    assert first[0]["observation_id"] == first[1]["observation_id"]
    assert first == list(extract_records(analysis, "出典", "https://example.test"))
    moved = replace(RELATION, predicate=Endpoint("畳む", ((10, 12),)))
    other = replace(analysis, relations=(moved,))
    assert (
        first[0]["observation_id"] != next(extract_records(other, "出典", "url"))["observation_id"]
    )


def inspector(monkeypatch, *, assessments=None, examples_path=None, analysis=ANALYSIS):
    import native_japanese.service as service

    class Parser:
        def __init__(self, runtime, timeout):
            self.closed = False

        def analyze(self, text):
            if text == "解析失敗":
                raise RuntimeError("解析に失敗しました。")
            return replace(analysis, text=text) if text.strip() else Analysis(text, (), ())

        def close(self):
            self.closed = True

    class Reference:
        def __init__(self, runtime, cache_path):
            self.calls = []
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def assess(self, query):
            self.calls.append(query)
            return (assessments or {}).get(query, Support(False, 0.2, None))

        def observed(self, query):
            support = (assessments or {}).get(query)
            return support if support is not None and support.observed else None

    monkeypatch.setattr(service, "read_runtime", lambda _: {})
    monkeypatch.setattr(service, "Parser", Parser)
    monkeypatch.setattr(service, "Reference", Reference)
    return Inspector("config", "cache", examples_path)


def test_added_observation_suppresses_only_exact_relation_without_scoring(monkeypatch, tmp_path):
    path = tmp_path / "追加.sqlite"
    with Examples(path, writable=True) as examples:
        examples.import_jsonl(lines(record(query=ALTERNATE)))
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(QUERY, ALTERNATE)),))
    with inspector(monkeypatch, examples_path=path, analysis=analysis) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].status == "observed"
        assert result.relations[0].queries[0].support.evidence[0]["source_id"] == "出典A"
        assert instance.reference.calls == []
    assert instance.parser.closed and instance.reference.closed


def test_support_threshold_unavailable_and_discontinuous_utf16_ranges(monkeypatch):
    analysis = replace(ANALYSIS, unavailable=(Unavailable(((0, 1),), "公開fallback"),))
    with inspector(monkeypatch, analysis=analysis) as instance:
        result = instance.lint(TEXT)
        assert {item.range for item in result.diagnostics} == {(1, 3), (9, 11)}
        encoded = cli.result_json(result)
        assert encoded["offsetEncoding"] == "utf-16"
        assert {tuple(item["range"]) for item in encoded["diagnostics"]} == {(2, 4), (10, 12)}
        assert encoded["unavailable"] == [
            {"kind": "analysis", "ranges": [[0, 2]], "reason": "公開fallback"}
        ]
        assert "analysis" not in encoded
        assert "組合せを確認" in encoded["diagnostics"][0]["message"]
    with inspector(monkeypatch, assessments={QUERY: Support(False, None, "比較不能")}) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].status == "unavailable"
        assert result.relations[0].reasons == ("比較不能",)
        assert cli.result_json(result)["unavailable"] == [
            {"kind": "reference", "ranges": [[10, 12], [2, 4]], "reason": "比較不能"}
        ]
    with inspector(monkeypatch, assessments={QUERY: Support(False, 0.35, None)}) as instance:
        assert instance.lint(TEXT).diagnostics == ()


@pytest.mark.parametrize(
    ("relation", "reason"),
    [
        (replace(RELATION, queries=()), "比較可能な照会がありません。"),
        (
            replace(RELATION, unavailable_reasons=("公開項の原文位置がありません。",)),
            "公開項の原文位置がありません。；比較不能",
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
        unavailable = cli.result_json(result)["unavailable"]
        assert unavailable == [
            {
                "kind": "analysis",
                "ranges": [[10, 12], [2, 4]],
                "reason": reason,
            }
        ]


def test_all_candidates_are_checked_for_direct_observation_before_scoring(monkeypatch):
    analysis = replace(ANALYSIS, relations=(replace(RELATION, queries=(QUERY, ALTERNATE)),))
    supports = {ALTERNATE: Support(True, None, None, ({"source": "base"},))}
    with inspector(monkeypatch, assessments=supports, analysis=analysis) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].status == "observed"
        assert instance.reference.calls == []
    supports = {QUERY: Support(False, 0.2, None), ALTERNATE: Support(False, 0.4, None)}
    with inspector(monkeypatch, assessments=supports, analysis=analysis) as instance:
        result = instance.lint(TEXT)
        assert result.diagnostics == ()
        assert result.relations[0].score == 0.4
        assert len(result.relations[0].queries) == 2


def test_serve_retains_request_ids_errors_empty_unicode_and_reuses_parser(monkeypatch, capsys):
    instance = inspector(monkeypatch)
    requests = [
        {"id": "一", "texts": [TEXT, "", "  \n"]},
        {"id": "不正", "texts": [1]},
        {"id": "失敗", "texts": ["解析失敗"]},
        {"id": "二", "texts": [TEXT]},
    ]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("\n" + "\n".join(lines(*requests))))
    with instance:
        cli.serve(instance)
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [response["id"] for response in output] == ["一", "不正", "失敗", "二"]
    assert output[0]["results"][1]["diagnostics"] == []
    assert output[0]["results"][2]["diagnostics"] == []
    assert "error" in output[1] and "error" in output[2]
    assert output[3]["results"][0]["diagnostics"] == output[0]["results"][0]["diagnostics"]
    assert instance.parser.closed


def test_cli_reports_initialization_failure_without_json_noise(monkeypatch, capsys):
    import native_japanese.service as service

    def fail(*args):
        raise RuntimeError("設定が一致しません。")

    monkeypatch.setattr(service, "Inspector", fail)
    assert cli.main(["serve", "--config", "不正", "--cache", "cache"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "設定が一致しません" in captured.err


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
