"""原文文字位置に統一したGED比較。モデル推論や日本語構文解析は行わない。"""

import argparse
import hashlib
import json
import math
from collections import Counter
from fractions import Fraction
from pathlib import Path

from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching

VERSION = 1
NONE_THRESHOLD = math.nextafter(1.0, math.inf)


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_sha256(rows):
    return text_sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, allow_nan=False))


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def save_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _range(item, length, context, *, empty=False):
    start, end = item.get("start"), item.get("end")
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start <= end <= length
        or (not empty and start == end)
    ):
        raise ValueError(f"不正な原文文字範囲: {context}: {start}, {end}")
    return start, end


def validate_gold(rows):
    docs = {}
    for doc in rows:
        key = doc.get("id")
        if not isinstance(key, str) or not key or key in docs:
            raise ValueError(f"不正または重複したgold ID: {key}")
        if not isinstance(doc.get("text"), str):
            raise ValueError(f"原文文字列が必要: {key}")
        if not isinstance(doc.get("split"), str) or "group_id" not in doc:
            raise ValueError(f"splitとgroup_idが必要: {key}")
        for field in ("spans", "gaps"):
            if not isinstance(doc.get(field), list):
                raise ValueError(f"{field}配列が必要: {key}")
            seen = set()
            for span in doc[field]:
                pair = _range(span, len(doc["text"]), key, empty=field == "gaps")
                if field == "gaps" and pair[0] != pair[1]:
                    raise ValueError(f"gapは空範囲に限定: {key}")
                if pair in seen:
                    raise ValueError(f"重複したgold範囲: {key}: {pair}")
                seen.add(pair)
                if field == "spans" and type(span.get("lexical")) is not bool:
                    raise ValueError(f"lexical真偽値が必要: {key}")
        docs[key] = doc
    return docs


def normalize_predictions(docs, rows):
    """重なるscoreは文字ごとのmax。省略文字はNoneのまま残す。"""
    result = {}
    for row in rows:
        key = row.get("id")
        if key not in docs or key in result:
            raise ValueError(f"未知または重複したprediction ID: {key}")
        text = docs[key]["text"]
        if row.get("text_sha256") != text_sha256(text):
            raise ValueError(f"原文SHA-256不一致: {key}")
        if not isinstance(row.get("scores"), list):
            raise ValueError(f"scores配列が必要: {key}")
        scores = [None] * len(text)
        seen = set()
        for item in row["scores"]:
            start, end = _range(item, len(text), key)
            score = item.get("score")
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"0..1の有限scoreが必要: {key}")
            entry = (start, end, score)
            if entry in seen:
                raise ValueError(f"重複したprediction要素: {key}: {entry}")
            seen.add(entry)
            for index in range(start, end):
                scores[index] = score if scores[index] is None else max(scores[index], score)
        coverage = row.get("coverage")
        if not isinstance(coverage, dict):
            raise ValueError(f"coverageが必要: {key}")
        for field, expected in (
            ("total_chars", len(text)),
            ("scored_chars", sum(s is not None for s in scores)),
        ):
            if type(coverage.get(field)) is not int or coverage[field] != expected:
                raise ValueError(f"coverage.{field}が実範囲と不一致: {key}")
        if (
            type(coverage.get("null_offset_items", 0)) is not int
            or coverage.get("null_offset_items", 0) < 0
        ):
            raise ValueError(f"null_offset_itemsは非負整数: {key}")
        unscored = coverage.get("unscored", [])
        if not isinstance(unscored, list):
            raise ValueError(f"unscored配列が必要: {key}")
        reason_chars = Counter()
        declared = set()
        for item in unscored:
            start, end = _range(item, len(text), key)
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason:
                raise ValueError(f"未採点理由の文字列が必要: {key}")
            positions = set(range(start, end))
            if declared & positions or any(scores[i] is not None for i in positions):
                raise ValueError(f"未採点範囲の重複または採点済み範囲との矛盾: {key}")
            declared.update(positions)
            reason_chars[reason] += end - start
        reason_chars["unspecified"] += sum(s is None for s in scores) - len(declared)
        result[key] = {
            "scores": scores,
            "reason_chars": reason_chars,
            "null_offset_items": coverage.get("null_offset_items", 0),
        }
    missing = set(docs) - set(result)
    if missing:
        raise ValueError(f"prediction欠落: {len(missing)}件: {sorted(missing)[:5]}")
    return result


def _gold_chars(doc, *, lexical=False):
    return {
        i
        for span in doc["spans"]
        if not lexical or span["lexical"]
        for i in range(span["start"], span["end"])
    }


def warning_spans(scores, threshold):
    """閾値以上の連続文字だけを結合し、未採点や低scoreの穴は埋めない。"""
    spans = []
    start = None
    for index in range(len(scores) + 1):
        flagged = index < len(scores) and scores[index] is not None and scores[index] >= threshold
        if flagged and start is None:
            start = index
        elif not flagged and start is not None:
            spans.append((start, index))
            start = None
    return spans


def _matched(gold, warnings, *, exact=False):
    if not gold or not warnings:
        return 0
    edges = [
        (i, j)
        for i, span in enumerate(gold)
        for j, warning in enumerate(warnings)
        if ((span == warning) if exact else (span[0] < warning[1] and warning[0] < span[1]))
    ]
    if not edges:
        return 0
    rows, cols = zip(*edges)
    graph = csr_matrix(([1] * len(edges), (rows, cols)), shape=(len(gold), len(warnings)))
    return int(sum(maximum_bipartite_matching(graph, perm_type="column") >= 0))


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _char_metrics(tp, fp, fn):
    return {
        "tp": tp,
        "unannotated_flagged": fp,
        "fn": fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f0_5": _ratio(1.25 * tp, 1.25 * tp + fp + 0.25 * fn) or 0.0,
    }


def _evaluate(docs, predictions, threshold):
    counts = Counter()
    reasons = Counter()
    for key, doc in docs.items():
        prediction = predictions[key]
        scores = prediction["scores"]
        gold = _gold_chars(doc)
        lexical = _gold_chars(doc, lexical=True)
        flagged = {i for i, score in enumerate(scores) if score is not None and score >= threshold}
        scored = {i for i, score in enumerate(scores) if score is not None}
        warnings = warning_spans(scores, threshold)
        spans = [(s["start"], s["end"]) for s in doc["spans"]]
        lex_spans = [(s["start"], s["end"]) for s in doc["spans"] if s["lexical"]]
        counts.update(
            {
                "documents": 1,
                "chars": len(scores),
                "scored_chars": len(scored),
                "tp": len(flagged & gold),
                "fp": len(flagged - gold),
                "fn": len(gold - flagged),
                "lexical_tp": len(flagged & lexical),
                "lexical_gold_chars": len(lexical),
                "gold_chars": len(gold),
                "scored_gold_chars": len(scored & gold),
                "scored_lexical_gold_chars": len(scored & lexical),
                "warnings": len(warnings),
                "flagged_chars": len(flagged),
                "gold_spans": len(spans),
                "lexical_gold_spans": len(lex_spans),
                "span_matches": _matched(spans, warnings),
                "lexical_span_matches": _matched(lex_spans, warnings),
                "exact_span_matches": _matched(spans, warnings, exact=True),
                "exact_lexical_span_matches": _matched(lex_spans, warnings, exact=True),
                "gap_annotations": len(doc["gaps"]),
                "documents_with_gaps": bool(doc["gaps"]),
                "null_offset_items": prediction["null_offset_items"],
            }
        )
        reasons.update(prediction["reason_chars"])
    return {
        "documents": counts["documents"],
        "characters": counts["chars"],
        "character": _char_metrics(counts["tp"], counts["fp"], counts["fn"]),
        "lexical_character": {
            "hit": counts["lexical_tp"],
            "gold": counts["lexical_gold_chars"],
            "recall": _ratio(counts["lexical_tp"], counts["lexical_gold_chars"]),
        },
        "span": {
            "gold": counts["gold_spans"],
            "matched": counts["span_matches"],
            "reach": _ratio(counts["span_matches"], counts["gold_spans"]),
            "exact_matched": counts["exact_span_matches"],
            "exact_recall": _ratio(counts["exact_span_matches"], counts["gold_spans"]),
        },
        "lexical_span": {
            "gold": counts["lexical_gold_spans"],
            "matched": counts["lexical_span_matches"],
            "reach": _ratio(counts["lexical_span_matches"], counts["lexical_gold_spans"]),
            "exact_matched": counts["exact_lexical_span_matches"],
            "exact_recall": _ratio(
                counts["exact_lexical_span_matches"], counts["lexical_gold_spans"]
            ),
        },
        "warnings": counts["warnings"],
        "flagged_chars": counts["flagged_chars"],
        "warnings_per_1000_chars": _ratio(1000 * counts["warnings"], counts["chars"]),
        "flagged_chars_per_1000_chars": _ratio(1000 * counts["flagged_chars"], counts["chars"]),
        "coverage": {
            "scored_chars": counts["scored_chars"],
            "unscored_chars": counts["chars"] - counts["scored_chars"],
            "fraction": _ratio(counts["scored_chars"], counts["chars"]),
            "gold_chars": counts["gold_chars"],
            "scored_gold_chars": counts["scored_gold_chars"],
            "lexical_gold_chars": counts["lexical_gold_chars"],
            "scored_lexical_gold_chars": counts["scored_lexical_gold_chars"],
            "unscored_chars_by_reason": dict(reasons),
            "null_offset_items": counts["null_offset_items"],
        },
        "gaps": {
            "annotations": counts["gap_annotations"],
            "documents": counts["documents_with_gaps"],
        },
    }


def evaluate(docs, predictions, threshold):
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold < 0:
        raise ValueError("閾値は非負の有限数に限定")
    report = {"threshold": threshold, **_evaluate(docs, predictions, threshold)}
    groups = {"full": {}, "partial": {}, "none": {}}
    for key, doc in docs.items():
        scores = predictions[key]["scores"]
        count = sum(s is not None for s in scores)
        status = "full" if count == len(scores) else "partial" if count else "none"
        groups[status][key] = doc
    report["by_coverage"] = {
        status: _evaluate(group, predictions, threshold)
        for status, group in groups.items()
        if group
    }
    return report


def budget_threshold(docs, predictions, budget):
    """訂正文で予算に入る最小のbinary64閾値。score同点は分割しない。"""
    characters = sum(len(doc["text"]) for doc in docs.values())
    if not characters or any(doc["spans"] or doc["gaps"] for doc in docs.values()):
        raise ValueError("非空の訂正文・空goldだけを予算参照に使用")
    amount = Fraction(str(budget))
    if amount < 0:
        raise ValueError("予算は非負数に限定")
    histogram = Counter(
        score
        for prediction in predictions.values()
        for score in prediction["scores"]
        if score is not None
    )
    included = 0
    for score, count in sorted(histogram.items(), reverse=True):
        if Fraction(1000 * (included + count), characters) > amount:
            return math.nextafter(score, math.inf)
        included += count
    return 0.0


def f0_5_threshold(docs, predictions):
    """全gold分母を固定し、訂正文を含めて文字F0.5を一度の走査で選択。"""
    histogram = {}
    gold_count = 0
    for key, doc in docs.items():
        gold = _gold_chars(doc)
        gold_count += len(gold)
        for index, score in enumerate(predictions[key]["scores"]):
            if score is not None:
                values = histogram.setdefault(score, [0, 0])
                values[index not in gold] += 1
    best = (0.0, NONE_THRESHOLD)
    tp = fp = 0
    for score, (positive, negative) in sorted(histogram.items(), reverse=True):
        tp += positive
        fp += negative
        value = _char_metrics(tp, fp, gold_count - tp)["f0_5"]
        best = max(best, (value, score))
    return best[1]


def select_dev(
    gold_rows, prediction_rows, corrected_rows, corrected_prediction_rows, budgets=(1, 5, 10)
):
    docs, corrected = validate_gold(gold_rows), validate_gold(corrected_rows)
    if (
        not docs
        or not corrected
        or any(d["split"] != "dev" for d in [*docs.values(), *corrected.values()])
    ):
        raise ValueError("閾値選択は非空のdev入力だけに限定")
    if set(docs) & set(corrected):
        raise ValueError("寺村と訂正文の入力IDが重複")
    predictions = normalize_predictions(docs, prediction_rows)
    corrected_predictions = normalize_predictions(corrected, corrected_prediction_rows)
    points = []
    for budget in budgets:
        threshold = budget_threshold(corrected, corrected_predictions, budget)
        points.append(
            {
                "budget_flagged_chars_per_1000": budget,
                "threshold": threshold,
                "teramura_dev": evaluate(docs, predictions, threshold),
                "teacher_corrected_dev": evaluate(corrected, corrected_predictions, threshold),
            }
        )
    combined = {**docs, **corrected}
    combined_predictions = {**predictions, **corrected_predictions}
    auxiliary = f0_5_threshold(combined, combined_predictions)
    return {
        "schema_version": VERSION,
        "selection": "訂正文の警告対象文字予算内で最小binary64閾値、score以上を指摘",
        "references": {
            "teramura_dev_sha256": content_sha256(gold_rows),
            "teramura_predictions_sha256": content_sha256(prediction_rows),
            "teacher_corrected_dev_sha256": content_sha256(corrected_rows),
            "teacher_corrected_predictions_sha256": content_sha256(corrected_prediction_rows),
        },
        "budget_points": points,
        "auxiliary_f0_5": {
            "threshold": auxiliary,
            "selection": "寺村devと教師訂正文devの全文字F0.5最大、同点なら高閾値",
            "combined": evaluate(combined, combined_predictions, auxiliary),
        },
    }


def teacher_corrected(rows):
    """教師コーパスのdev訂正文を全文完全一致でまとめ、誤りspanを空にする。"""
    result = {}
    for row in rows:
        if row.get("split") != "dev" or not isinstance(row.get("correction"), str):
            raise ValueError("教師dev訂正文だけを指定")
        text = row["correction"]
        digest = text_sha256(text)
        key = f"teacher-corrected:dev:{digest}"
        result.setdefault(
            key,
            {
                "id": key,
                "text": text,
                "split": "dev",
                "group_id": digest,
                "spans": [],
                "gaps": [],
                "source_pair_ids": [],
            },
        )["source_pair_ids"].append(row["id"])
    return list(result.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-corrected", help="教師コーパスのdev訂正文だけを変換")
    prepare.add_argument("--teacher-dev", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    select = commands.add_parser("select-dev", help="devのみから比較閾値と評価を保存")
    for name in ("gold", "predictions", "corrected-gold", "corrected-predictions", "output"):
        select.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare-corrected":
        rows = teacher_corrected(read_jsonl(args.teacher_dev))
        args.output.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
    else:
        sources = {
            name: getattr(args, name)
            for name in ("gold", "predictions", "corrected_gold", "corrected_predictions")
        }
        report = select_dev(*(read_jsonl(path) for path in sources.values()))
        report["source_files"] = {
            name: {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in sources.items()
        }
        report["evaluator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        save_json(args.output, report)


if __name__ == "__main__":
    main()
