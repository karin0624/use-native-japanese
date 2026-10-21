"""人工例だけで文字評価と閾値選択の回帰を確認する。"""

import math
import unittest

from scripts.evaluate_detection import (
    NONE_THRESHOLD,
    budget_threshold,
    evaluate,
    normalize_predictions,
    select_dev,
    teacher_corrected,
    text_sha256,
    validate_gold,
    warning_spans,
)


def document(key, text, spans=(), gaps=()):
    return {
        "id": key,
        "text": text,
        "split": "dev",
        "group_id": key,
        "spans": [{"start": a, "end": b, "lexical": lexical} for a, b, lexical in spans],
        "gaps": [{"start": p, "end": p} for p in gaps],
    }


def prediction(doc, spans, **coverage):
    scores = [{"start": a, "end": b, "score": s} for a, b, s in spans]
    scored = {i for a, b, _ in spans for i in range(a, b)}
    return {
        "id": doc["id"],
        "text_sha256": text_sha256(doc["text"]),
        "scores": scores,
        "coverage": {"total_chars": len(doc["text"]), "scored_chars": len(scored), **coverage},
    }


def report(doc, row, threshold=0.5):
    docs = validate_gold([doc])
    return evaluate(docs, normalize_predictions(docs, [row]), threshold)


class CommonEvaluationTest(unittest.TestCase):
    def test_token_granularity_and_overlap_max_do_not_change_metrics(self):
        doc = document("粒度", "あいうえ", [(1, 3, True)])
        coarse = prediction(doc, [(0, 1, 0.1), (1, 3, 0.8), (3, 4, 0.1)])
        fine = prediction(doc, [(i, i + 1, s) for i, s in enumerate([0.1, 0.8, 0.8, 0.1])])
        overlapping = prediction(doc, [(0, 4, 0.1), (1, 3, 0.8), (1, 2, 0.2)])
        self.assertEqual(
            report(doc, coarse), report(doc, fine), "token粒度で評価が変わってはいけない"
        )
        self.assertEqual(report(doc, coarse), report(doc, overlapping), "重複scoreはmaxで投影する")

    def test_wide_warning_has_unannotated_chars_and_only_one_match(self):
        doc = document("広い警告", "あいうえお", [(0, 1, True), (4, 5, True)])
        value = report(doc, prediction(doc, [(0, 5, 0.9)]))
        self.assertEqual(
            value["character"],
            {
                "tp": 2,
                "unannotated_flagged": 3,
                "fn": 0,
                "precision": 0.4,
                "recall": 1.0,
                "f0_5": 2.5 / 5.5,
            },
        )
        self.assertEqual(value["span"]["matched"], 1, "1警告を複数goldのTPへ水増ししない")
        self.assertEqual(value["lexical_span"]["reach"], 0.5)
        self.assertEqual(value["span"]["exact_matched"], 0)
        self.assertEqual(value["flagged_chars_per_1000_chars"], 1000)

    def test_abstained_gold_stays_fn_and_gaps_stay_separate(self):
        doc = document("保留", "あいう", [(0, 1, True), (2, 3, False)], [1])
        row = prediction(
            doc,
            [(1, 2, 0.1)],
            unscored=[{"start": 0, "end": 1, "reason": "abstained"}],
            null_offset_items=1,
        )
        value = report(doc, row)
        self.assertEqual(value["character"]["fn"], 2, "未採点goldもFNに残す")
        self.assertEqual(value["lexical_character"]["recall"], 0)
        self.assertEqual(value["gaps"], {"annotations": 1, "documents": 1})
        self.assertEqual(value["coverage"]["scored_gold_chars"], 0)
        self.assertEqual(
            value["coverage"]["unscored_chars_by_reason"], {"abstained": 1, "unspecified": 1}
        )
        self.assertIn("partial", value["by_coverage"])
        empty = report(doc, prediction(doc, []))
        self.assertEqual(empty["character"]["fn"], 2)
        self.assertIn("none", empty["by_coverage"])

    def test_nonlexical_error_does_not_become_lexical_false_positive(self):
        doc = document("分類", "あいう", [(0, 1, True), (2, 3, False)])
        value = report(doc, prediction(doc, [(2, 3, 0.8)]))
        self.assertEqual(value["character"]["tp"], 1)
        self.assertEqual(value["character"]["unannotated_flagged"], 0)
        self.assertEqual(value["lexical_character"]["recall"], 0)
        self.assertNotIn("precision", value["lexical_character"], "語彙適合率は定義しない")

    def test_unicode_and_arc_endpoints_do_not_fill_middle(self):
        doc = document("Unicode", "𠮷😀か\u3099文", [(0, 1, True), (3, 4, False)])
        value = report(doc, prediction(doc, [(0, 1, 0.8), (3, 4, 0.8)]))
        self.assertEqual(len(doc["text"]), 5, "offset単位はUTF-16や書記素でなくPython文字")
        self.assertEqual(value["warnings"], 2, "arc両端の間は塗りつぶさない")
        self.assertEqual(value["flagged_chars"], 2)
        self.assertEqual(value["span"]["exact_matched"], 2)
        self.assertEqual(warning_spans([0.8, None, 0.8], 0.5), [(0, 1), (2, 3)])

    def test_invalid_or_missing_prediction_is_rejected(self):
        doc = document("検証", "😀あ", [(0, 1, True)])
        docs = validate_gold([doc])
        base = prediction(doc, [(0, 1, 0.8)])
        cases = [
            [],
            [base, base],
            [{**base, "id": "未知"}],
            [{**base, "text_sha256": "誤り"}],
            [{**base, "coverage": {"total_chars": 2, "scored_chars": 2}}],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                normalize_predictions(docs, rows)
        for start, end, score in [
            (0, 3, 0.5),
            (-1, 1, 0.5),
            (1, 1, 0.5),
            (None, None, 0.5),
            (True, 1, 0.5),
            (0, 1, math.nan),
            (0, 1, math.inf),
            (0, 1, 1.1),
            (0, 1, -0.1),
            (0, 1, True),
        ]:
            with self.subTest(span=(start, end, score)), self.assertRaises(ValueError):
                normalize_predictions(
                    docs, [{**base, "scores": [{"start": start, "end": end, "score": score}]}]
                )
        with self.assertRaises(ValueError, msg="完全重複prediction要素を拒否する"):
            normalize_predictions(docs, [{**base, "scores": base["scores"] * 2}])
        with self.assertRaises(ValueError, msg="保留範囲と採点済み範囲の矛盾を拒否する"):
            normalize_predictions(
                docs,
                [
                    {
                        **base,
                        "coverage": {
                            **base["coverage"],
                            "unscored": [{"start": 0, "end": 1, "reason": "保留"}],
                        },
                    }
                ],
            )

    def test_budget_ties_and_all_char_denominator(self):
        doc = document("訂正文", "あ" * 1000)
        row = prediction(doc, [(0, 1, 0.9), (1, 5, 0.8), (5, 11, 0.7)])
        docs = validate_gold([doc])
        predictions = normalize_predictions(docs, [row])
        for budget, expected_count, boundary in [(1, 1, 0.8), (5, 5, 0.7), (10, 5, 0.7)]:
            threshold = budget_threshold(docs, predictions, budget)
            self.assertEqual(threshold, math.nextafter(boundary, math.inf))
            value = evaluate(docs, predictions, threshold)
            self.assertEqual(value["flagged_chars"], expected_count)
            self.assertLessEqual(value["flagged_chars_per_1000_chars"], budget)
        all_one = normalize_predictions(docs, [prediction(doc, [(0, 1000, 1.0)])])
        self.assertEqual(budget_threshold(docs, all_one, 1), NONE_THRESHOLD)
        self.assertEqual(budget_threshold(docs, predictions, 1000), 0)

    def test_corrected_characters_affect_auxiliary_threshold_but_not_budget_selection(self):
        doc = document("寺村", "あい", [(0, 2, True)])
        corrected = document("教師訂正文", "う" * 1000)
        row = prediction(doc, [(0, 1, 0.9), (1, 2, 0.6)])
        negative = prediction(corrected, [(0, 1000, 0.7)])
        value = select_dev([doc], [row], [corrected], [negative])
        self.assertEqual(
            value["auxiliary_f0_5"]["threshold"],
            0.9,
            "訂正文1000文字が分母に入り低閾値のF0.5が落ちる",
        )
        changed = prediction(doc, [(0, 2, 0.95)])
        other = select_dev([doc], [changed], [corrected], [negative])
        self.assertEqual(
            [p["threshold"] for p in value["budget_points"]],
            [p["threshold"] for p in other["budget_points"]],
            "主閾値は寺村goldもscoreも参照しない",
        )
        self.assertEqual(
            value["budget_points"][0]["teramura_dev"]["lexical_character"]["recall"], 0.5
        )
        self.assertEqual(
            other["budget_points"][0]["teramura_dev"]["lexical_character"]["recall"], 1
        )
        self.assertEqual(len(value["references"]["teacher_corrected_dev_sha256"]), 64)
        test = {**doc, "split": "test"}
        with self.assertRaises(ValueError, msg="testからの閾値選択を拒否する"):
            select_dev([test], [row], [corrected], [negative])

    def test_teacher_corrected_adapter_deduplicates_full_text(self):
        rows = [
            {"id": "対1", "split": "dev", "correction": "同文"},
            {"id": "対2", "split": "dev", "correction": "同文"},
        ]
        result = teacher_corrected(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["spans"], [])
        self.assertEqual(result[0]["source_pair_ids"], ["対1", "対2"])


if __name__ == "__main__":
    unittest.main()
