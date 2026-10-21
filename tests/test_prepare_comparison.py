"""除外前のgroup連結・元offsetでの統合・補助group保留を確認する。"""

import hashlib
import unittest

from scripts import prepare_comparison as p


class ComparisonTests(unittest.TestCase):
    def test_excluded_bridge_and_original_variant_keep_the_same_group(self):
        rows = [
            {"candidate_group_id": 1, "learner_identifiers_raw": ["A"]},
            {"candidate_group_id": 2, "learner_identifiers_raw": ["A", "B"]},
            {"candidate_group_id": 3, "learner_identifiers_raw": ["B"]},
            {"candidate_group_id": 1, "learner_identifiers_raw": ["C"]},
            {"candidate_group_id": 4, "learner_identifiers_raw": ["D"]},
        ]
        groups = p.joint_groups(rows)
        self.assertEqual(len(set(groups[:4])), 1)
        self.assertNotEqual(groups[0], groups[4])
        excluded_bridge = {
            "has_unresolved_annotation_on_same_input": True,
            "same_source_reference_with_unknown_input_ids": [],
            "has_whole_text_annotation": False,
        }
        self.assertEqual(len(p.exclusion_reasons(excluded_bridge)), 1)
        # 真ん中の行が除外されても、既に確定した両側のgroupは分かれない。
        self.assertEqual(groups[0], groups[2])

    def test_equal_offsets_union_original_labels_without_expanding_gap(self):
        item = {
            "input_text": "AB",
            "input_sha256": hashlib.sha256(b"AB").hexdigest(),
            "learner_identifiers_raw": ["A"],
            "candidate_group_id": 2,
        }
        records = []
        for number, chapter in [(1, 3), (2, 5)]:
            records.append(
                {
                    "status": "resolved",
                    "input_text": "AB",
                    "publication_id": number,
                    "source_line": number,
                    "source_reference_raw": "A,1",
                    "source_reference": {"source_sentence_key": ["A", "form", 1]},
                    "raw_labels": str(chapter),
                    "annotations": [
                        {
                            "kind": "span",
                            "span": [1, 2],
                            "annotation_numbers": [1],
                            "raw_marker": "raw",
                            "official_label_categories": [
                                {"chapter": chapter, "operation": "misused"}
                            ],
                            "label_links": [{"raw": str(chapter)}],
                        },
                        {
                            "kind": "gap",
                            "span": [1, 1],
                            "annotation_numbers": [2],
                            "raw_marker": "gap",
                            "official_label_categories": [{"chapter": 3, "operation": "missing"}],
                            "label_links": [{"raw": "missing"}],
                        },
                    ],
                }
            )
        output = p.merged_input(item, records, 8, "dev")
        self.assertEqual(len(output["spans"]), 1)
        self.assertEqual((output["spans"][0]["start"], output["spans"][0]["end"]), (1, 2))
        self.assertEqual(len(output["spans"][0]["labels"]), 2)
        self.assertTrue(output["spans"][0]["lexical"])
        self.assertEqual(len(output["spans"][0]["source_refs"]), 2)
        self.assertEqual((output["gaps"][0]["start"], output["gaps"][0]["end"]), (1, 1))

    def test_reserved_groups_and_exact_evaluation_sentences_never_enter_auxiliary(self):
        assignments = [
            {"id": f"p{i}", "group_id": group} for i, group in enumerate([1, 2, 3, 4, 4], 1)
        ]
        rows = [
            {
                "id": f"p{i}",
                "line": i,
                "passes_exact_markup_context_checks": True,
                "correction": text,
                "source": "source",
                "raw_correction": text,
            }
            for i, text in enumerate(["reserved1", "reserved2", "eval", "keep", "keep"], 1)
        ]
        corrected, ledger = p.supplementary_corrected(assignments, rows, {1, 2}, {"eval"}, set())
        self.assertEqual([r["text"] for r in corrected], ["keep"])
        self.assertEqual(len(corrected[0]["source_refs"]), 2)
        self.assertEqual(corrected[0]["group_ids"], [4])
        self.assertFalse(corrected[0]["is_guaranteed_correct"])
        self.assertEqual(len(ledger), 3)


if __name__ == "__main__":
    unittest.main()


def test_fixed_seed_partitions_rows_without_sharing_groups():
    groups = [group for group in range(20) for _ in range(2)]
    selected = p.split_indices(groups)
    assert sorted(index for indices in selected.values() for index in indices) == list(range(40))
    assert {name: list(indices) for name, indices in selected.items()} == {
        name: list(indices) for name, indices in p.split_indices(groups).items()
    }
    group_sets = [{groups[index] for index in selected[name]} for name in ("train", "dev", "test")]
    assert [len(values) for values in group_sets] == [12, 4, 4]
    assert all(not a & b for index, a in enumerate(group_sets) for b in group_sets[index + 1 :])
