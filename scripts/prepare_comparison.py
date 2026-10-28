"""原注釈を統合し、共通比較入力をgroupで固定分割する。学習・推論なし。"""

import argparse
import hashlib
import json
import platform
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import scipy
import sklearn
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neighbors import NearestNeighbors

SEED = 20261021


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def joint_groups(inventory):
    owners, left, right = {}, [], []
    for index, row in enumerate(inventory):
        keys = [("candidate", row["candidate_group_id"])]
        keys.extend(("learner", key) for key in row["learner_identifiers_raw"])
        for key in keys:
            if key in owners:
                left.append(owners[key])
                right.append(index)
            else:
                owners[key] = index
    graph = csr_matrix(([1] * len(left), (left, right)), shape=(len(inventory), len(inventory)))
    _, labels = connected_components(graph, directed=False)
    return [int(value) for value in labels]


def exclusion_reasons(row):
    reasons = []
    if row["has_unresolved_annotation_on_same_input"]:
        reasons.append("同一全文の別掲載に未解決注釈あり")
    if row["same_source_reference_with_unknown_input_ids"]:
        reasons.append("同一出典参照に入力化不可の掲載あり")
    if row["has_whole_text_annotation"]:
        reasons.append("全文誤り注釈あり")
    return reasons


def merged_input(item, source_records, group_id, split):
    positions = {}
    refs = []
    for row in source_records:
        assert row["status"] == "resolved" and row["input_text"] == item["input_text"]
        refs.append(
            {
                "publication_id": row["publication_id"],
                "source_line": row["source_line"],
                "source_reference_raw": row["source_reference_raw"],
                "source_sentence_key": row["source_reference"]["source_sentence_key"],
                "raw_labels": row["raw_labels"],
            }
        )
        for annotation in row["annotations"]:
            start, end = annotation["span"]
            key = annotation["kind"], start, end
            merged = positions.setdefault(
                key,
                {
                    "start": start,
                    "end": end,
                    "lexical": False,
                    "labels": {},
                    "raw_label_units": set(),
                    "source_refs": [],
                },
            )
            for category in annotation["official_label_categories"]:
                merged["labels"][json.dumps(category, ensure_ascii=False, sort_keys=True)] = (
                    category
                )
                if category["chapter"] == 3 and (
                    category["operation"] != "missing" or annotation["kind"] == "gap"
                ):
                    merged["lexical"] = True
            merged["raw_label_units"].update(unit["raw"] for unit in annotation["label_links"])
            merged["source_refs"].append(
                {
                    "publication_id": row["publication_id"],
                    "source_line": row["source_line"],
                    "annotation_numbers": annotation["annotation_numbers"],
                    "raw_marker": annotation["raw_marker"],
                }
            )
    result = {
        "id": "teramura:" + item["input_sha256"],
        "text": item["input_text"],
        "split": split,
        "group_id": group_id,
        "learner_ids": item["learner_identifiers_raw"],
        "candidate_group_id": item["candidate_group_id"],
        "spans": [],
        "gaps": [],
        "source_refs": refs,
    }
    for (kind, start, end), value in sorted(positions.items()):
        value["labels"] = [value["labels"][key] for key in sorted(value["labels"])]
        value["raw_label_units"] = sorted(value["raw_label_units"])
        assert 0 <= start <= end <= len(result["text"])
        assert (start == end) == (kind == "gap")
        result["gaps" if kind == "gap" else "spans"].append(value)
    return result


def supplementary_corrected(
    assignments, teacher_rows, reserved_groups, evaluation_texts, main_train_texts
):
    group_of = {row["id"]: row["group_id"] for row in assignments}
    corrected, ledger = {}, []
    for row in teacher_rows:
        if not row.get("passes_exact_markup_context_checks"):
            ledger.append(
                {
                    "pair_id": row["id"],
                    "source_line": row["line"],
                    "reason": "既存eligible判定を満たさない",
                }
            )
            continue
        group = group_of[row["id"]]
        if group in reserved_groups:
            ledger.append(
                {
                    "pair_id": row["id"],
                    "source_line": row["line"],
                    "group_id": group,
                    "reason": "既存dev/testまたは固定1000監査groupを完全保留",
                }
            )
            continue
        if row["correction"] in evaluation_texts:
            ledger.append(
                {
                    "pair_id": row["id"],
                    "source_line": row["line"],
                    "group_id": group,
                    "reason": "寺村dev/testと訂正文全文が一致",
                }
            )
            continue
        text = row["correction"]
        entry = corrected.setdefault(
            text,
            {
                "id": "teacher-corrected:" + hashlib.sha256(text.encode()).hexdigest(),
                "text": text,
                "role": "auxiliary_teacher_correction",
                "group_ids": set(),
                "source_refs": [],
                "is_guaranteed_correct": False,
                "matches_teramura_train_input": text in main_train_texts,
            },
        )
        entry["group_ids"].add(group)
        entry["source_refs"].append(
            {
                "pair_id": row["id"],
                "source_line": row["line"],
                "source": row["source"],
                "raw_correction": row["raw_correction"],
            }
        )
    for row in corrected.values():
        row["group_ids"] = sorted(row["group_ids"])
        assert len(row["group_ids"]) == 1
    return list(corrected.values()), ledger


def audit_near(auxiliary, evaluation):
    """文字TF-IDFで近似を監査し、最近傍の類似度と監査表示用の件数を返す。"""
    if not auxiliary or not evaluation:
        return {"available": False}
    texts = [r["text"] for r in auxiliary] + [r["text"] for r in evaluation]
    matrix = TfidfVectorizer(
        analyzer="char", ngram_range=(2, 4), lowercase=False, dtype=np.float32, norm="l2"
    ).fit_transform(texts)
    count = len(auxiliary)
    neighbors = NearestNeighbors(n_neighbors=1, metric="cosine", algorithm="brute", n_jobs=1).fit(
        matrix[count:]
    )
    distances, indices = neighbors.kneighbors(matrix[:count])
    hits = [
        {
            "cosine": float(1 - distance[0]),
            "auxiliary_id": row["id"],
            "auxiliary_text": row["text"],
            "evaluation_id": evaluation[int(index[0])]["id"],
            "evaluation_text": evaluation[int(index[0])]["text"],
            "evaluation_split": evaluation[int(index[0])]["split"],
        }
        for row, distance, index in zip(auxiliary, distances, indices)
    ]
    hits.sort(key=lambda row: row["cosine"], reverse=True)
    return {
        "available": True,
        "method": "文字TF-IDF 2–4gram、cosine nearest。独自正規化なし。",
        "maximum_cosine": hits[0]["cosine"],
        "auxiliary_inputs_with_nearest_cosine_at_least_0_85": sum(
            row["cosine"] >= 0.85 for row in hits
        ),
        "nearest_30": hits[:30],
        "limitation": "全文完全一致0は保証するが、近似文・同じ意味のテンプレートの漏洩なしは保証しない。0.85は監査表示用で除外基準ではない。",
    }


def split_counts(rows):
    return {
        "inputs": len(rows),
        "groups": len({r["group_id"] for r in rows}),
        "learners": len({learner for r in rows for learner in r["learner_ids"]}),
        "local_spans": sum(len(r["spans"]) for r in rows),
        "lexical_local_spans": sum(span["lexical"] for r in rows for span in r["spans"]),
        "inputs_with_lexical_spans": sum(any(span["lexical"] for span in r["spans"]) for r in rows),
        "gap_annotations": sum(len(r["gaps"]) for r in rows),
        "lexical_gap_annotations": sum(gap["lexical"] for r in rows for gap in r["gaps"]),
    }


def split_indices(group_ids):
    """固定seedでgroup単位のtrain/dev/testを分け、元行の位置を返す。"""
    groups = np.array(group_ids)
    indices = np.arange(len(groups))
    remaining, test = next(
        GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=SEED).split(
            indices, groups=groups
        )
    )
    train, dev = next(
        GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=SEED).split(
            remaining, groups=groups[remaining]
        )
    )
    return {"train": remaining[train], "dev": remaining[dev], "test": test}


def run(source_dir, teacher_dir, output_dir):
    if output_dir.exists():
        raise SystemExit("分割先が既に存在するため再抽出しません")
    inventory = read(source_dir / "input-inventory.jsonl")
    all_records = read(source_dir / "records.jsonl")
    records_by_text = defaultdict(list)
    for row in all_records:
        if row["input_text"] is not None:
            records_by_text[row["input_text"]].append(row)
    labels = joint_groups(inventory)
    eligible, excluded, assignment = [], [], []
    for item, group in zip(inventory, labels):
        reasons = exclusion_reasons(item)
        record = {
            "input_sha256": item["input_sha256"],
            "group_id": group,
            "split": None,
            "candidate_group_id": item["candidate_group_id"],
            "learner_ids": item["learner_identifiers_raw"],
        }
        if reasons:
            excluded.append(
                {
                    "text": item["input_text"],
                    "exclusion_reasons": reasons,
                    **record,
                    "source_lines": item["source_lines"],
                    "publication_ids": item["publication_ids"],
                    "unknown_source_publication_ids": item[
                        "same_source_reference_with_unknown_input_ids"
                    ],
                }
            )
        else:
            eligible.append((item, group, record))
        assignment.append(record)
    selected = split_indices([group for _, group, _ in eligible])
    splits = {}
    for name, selected_indices in selected.items():
        splits[name] = []
        for index in sorted(selected_indices):
            item, group, record = eligible[index]
            record["split"] = name
            splits[name].append(
                merged_input(item, records_by_text[item["input_text"]], group, name)
            )
    intersections = {}
    for a, b in [("train", "dev"), ("train", "test"), ("dev", "test")]:
        intersections[f"{a}_{b}"] = {
            "shared_texts": len({r["text"] for r in splits[a]} & {r["text"] for r in splits[b]}),
            "shared_learners": len(
                {v for r in splits[a] for v in r["learner_ids"]}
                & {v for r in splits[b] for v in r["learner_ids"]}
            ),
            "shared_groups": len(
                {r["group_id"] for r in splits[a]} & {r["group_id"] for r in splits[b]}
            ),
        }
        assert not any(intersections[f"{a}_{b}"].values())
    assignment_path = teacher_dir / "split-20261021/assignment.jsonl"
    teacher_assignment = read(assignment_path)
    held = {r["group_id"] for r in teacher_assignment if r["selected_split"] is not None}
    audit_paths = [
        teacher_dir / f"unused-group-audit-20261021/audit-part-{n}.jsonl" for n in (1, 2)
    ]
    audit_groups = {r["group_id"] for p in audit_paths for r in read(p)}
    assert len(audit_groups) == 1000 and not (held & audit_groups)
    reserved = held | audit_groups
    auxiliary, auxiliary_ledger = supplementary_corrected(
        teacher_assignment,
        read(teacher_dir / "teacher-inspected.jsonl"),
        reserved,
        {r["text"] for name in ("dev", "test") for r in splits[name]},
        {r["text"] for r in splits["train"]},
    )
    assert not (
        {r["text"] for r in auxiliary}
        & {r["text"] for name in ("dev", "test") for r in splits[name]}
    )
    assert not ({g for r in auxiliary for g in r["group_ids"]} & reserved)
    near = audit_near(auxiliary, splits["dev"] + splits["test"])
    output_dir.mkdir(parents=True)
    for name, rows in splits.items():
        save_jsonl(output_dir / f"{name}.jsonl", rows)
    save_jsonl(output_dir / "all-known-assignment.jsonl", assignment)
    save_jsonl(output_dir / "excluded-main-inputs.jsonl", excluded)
    save_jsonl(output_dir / "train-corrected.jsonl", auxiliary)
    save_jsonl(output_dir / "excluded-teacher-pairs.jsonl", auxiliary_ledger)
    save_json(output_dir / "auxiliary-near-evaluation-audit.json", near)
    source_paths = [
        source_dir / "manifest.json",
        source_dir / "records.jsonl",
        source_dir / "input-inventory.jsonl",
        assignment_path,
        teacher_dir / "teacher-inspected.jsonl",
        *audit_paths,
        teacher_dir / "split-20261021/dev.jsonl",
        teacher_dir / "split-20261021/test.jsonl",
        Path(__file__),
    ]
    manifest = {
        "seed": SEED,
        "model_predictions_consulted": False,
        "method": "全既知inventoryのlearner IDまたはcandidate group共有をscipy無向連結成分化後、指定3条件を主比較から除外。GroupShuffleSplit(seed)でtest20%、残り25%dev。",
        "all_known_inputs": len(inventory),
        "all_known_joint_groups": len(set(labels)),
        "excluded_main_inputs": len(excluded),
        "excluded_main_reasons": dict(
            Counter(reason for r in excluded for reason in r["exclusion_reasons"])
        ),
        "splits": {name: split_counts(rows) for name, rows in splits.items()},
        "cross_split_checks": intersections,
        "auxiliary_teacher_corrections": {
            "inputs": len(auxiliary),
            "source_groups": len({g for r in auxiliary for g in r["group_ids"]}),
            "reserved_existing_dev_test_groups": len(held),
            "reserved_audit_groups": len(audit_groups),
            "excluded_pair_reasons": dict(Counter(r["reason"] for r in auxiliary_ledger)),
            "exact_teramura_dev_test_overlap": 0,
            "exact_teramura_train_input_matches": sum(
                r["matches_teramura_train_input"] for r in auxiliary
            ),
            "guaranteed_correct_gold": False,
        },
        "source_sha256": {str(path): sha(path) for path in source_paths},
        "output_sha256": {path.name: sha(path) for path in sorted(output_dir.iterdir())},
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
        },
        "annotation_policy": "同一offsetの原ラベルをunion。非空spanのlexicalは公式第3章L/＊L、gapのlexicalは欠落対象も含む第3章カテゴリ。gapを近隣語のIへ拡張しない。",
        "limitations": [
            "原ラベルは語彙章全般で、連語専用・AI生成文専用のgoldではない。",
            "原注釈の範囲は広く、厳密な誤語境界精度とは限らない。",
            "未解決/全文誤りを除いたため、原資料全体の代表性を保証しない。",
            "補助訂正文は完全な正用goldではなく、C/Iタグも新規alignmentも付けない。",
            "補助訂正文と寺村dev/testの全文一致なしだけを保証。近似文は別auditで報告し除外していない。",
            "既存teacher dev/test・1000監査groupは補助trainに含めず、元データは変更しない。",
        ],
    }
    save_json(output_dir / "manifest.json", manifest)
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in [
                    "all_known_inputs",
                    "all_known_joint_groups",
                    "excluded_main_inputs",
                    "splits",
                    "cross_split_checks",
                    "auxiliary_teacher_corrections",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--teacher-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source_dir, args.teacher_dir, args.output)
