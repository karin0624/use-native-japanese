"""複数注釈・gap・原文字保全・不明記法の扱いを確認する。"""

import unittest

from scripts import convert_teramura as c

CATEGORY = {"code": "3.2", "chapter": {"number": 3}}


def record(text, labels):
    fields = ["１", "台湾５，自由作文２", text, labels]
    return c.convert_record(1, "\t".join(fields), fields, 49, CATEGORY)


class ConversionTests(unittest.TestCase):
    def test_multiple_errors_preserve_original_characters_and_offsets(self):
        row = record("Ａ【Ｂ】（１）と【Ｃ】（２）、「Ｄ」。", "１Ｖ　２Ｎ")
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["input_text"], "ＡＢとＣ、「Ｄ」。")
        self.assertEqual([a["span"] for a in row["annotations"]], [[1, 2], [3, 4]])
        self.assertEqual([a["label_links"][0]["raw"] for a in row["annotations"]], ["Ｖ", "Ｎ"])

    def test_missing_placeholder_is_an_unexpanded_gap_and_wholetext_is_separate(self):
        row = record("文【＿】（１）【字】（２）。", "１－ノ連　２表記")
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["input_text"], "文字。")
        self.assertEqual(
            [(a["kind"], a["span"]) for a in row["annotations"]],
            [("gap", [1, 1]), ("span", [1, 2])],
        )
        whole = record("ある文。", "＆表現")
        self.assertEqual(whole["status"], "resolved")
        self.assertEqual(whole["annotations"], [])
        self.assertEqual(whole["whole_text_annotations"][0]["span"], [0, 4])

    def test_shared_annotation_numbers_and_ambiguous_numbers_are_not_guessed(self):
        row = record("【字】（１，２）。", "１Ｖ　２表記")
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["annotations"][0]["annotation_numbers"], [1, 2])
        self.assertEqual(len(row["annotations"][0]["label_links"]), 2)
        unclear = record("【字】（１）【文】（２）。", "１２Ｖ")
        self.assertEqual(unclear["status"], "unresolved")
        self.assertIn("本文とラベルの番号集合が一致しない", unclear["reasons"])
        parenthetical = record("【字】。", "Ｖ　（Ｎ）")
        self.assertEqual(parenthetical["status"], "unresolved")
        self.assertEqual(parenthetical["raw_labels"], "Ｖ　（Ｎ）")
        duplicate = record("【字】（１）【文】（１）。", "１Ｖ")
        self.assertEqual(duplicate["status"], "unresolved")
        self.assertIn("本文注釈番号が重複している", duplicate["reasons"])
        unknown_label = record("【字】。", "？")
        self.assertEqual(unknown_label["status"], "unresolved")
        self.assertEqual(unknown_label["raw_labels"], "？")

    def test_unsupported_markers_never_leave_unconverted_input(self):
        for text in ["【＿が】。", "【字。", "【【字】】。", "【字】（？）。"]:
            with self.subTest(text=text):
                row = record(text, "Ｖ")
                self.assertEqual(row["status"], "unresolved")
                self.assertIsNone(row["input_text"])
                self.assertEqual(row["annotated_text"], text)

    def test_source_reference_is_sentence_number_and_bad_columns_are_not_repaired(self):
        row = record("【字】。", "Ｖ")
        self.assertEqual(row["source_reference"]["learner_identifier_raw"], "台湾５")
        self.assertEqual(row["source_reference"]["composition_type_raw"], "自由作文")
        self.assertEqual(row["source_reference"]["sentence_number"], 2)
        self.assertIsNone(row["source_reference"]["essay_id"])
        malformed = c.convert_record(
            2, "原行", ["１１４　台湾５，自由作文２", "【字】。", "Ｖ"], 49, CATEGORY
        )
        self.assertEqual(malformed["status"], "excluded")
        self.assertIsNone(malformed["input_text"])
        self.assertEqual(malformed["raw_source_line"], "原行")

    def test_lexical_category_uses_official_source_label_not_missing_target(self):
        rows = [record("【字】（１）【文】（２）。", "１Ｖ　２格　＊ヲ／－Ｎ")]
        mapping = {
            "Ｖ": {"chapter": 3},
            "Ｎ": {"chapter": 3},
            "格": {"chapter": 5},
            "ヲ": {"chapter": 5},
        }
        c.attach_label_categories(rows, mapping)
        positions = c.summarize(rows)["usable_position_annotation_candidates"]
        self.assertEqual(positions["resolved_published_local_spans"], 2)
        self.assertEqual(positions["resolved_published_lexical_label_spans"], 1)


if __name__ == "__main__":
    unittest.main()
