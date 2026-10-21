"""教師コーパスの原記号を読み、2026-10-21の固定group・選択記録を再生する。"""

import argparse
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

SELECTION = Path(__file__).with_name("teacher-selection-20261021.json")


def marked(text, left, right):
    if text.count(left) != 1 or text.count(right) != 1:
        return None
    start, end = text.index(left), text.index(right)
    if start >= end:
        return None
    prefix, phrase, suffix = text[:start], text[start + 1 : end], text[end + 1 :]
    return prefix + phrase + suffix, [start, start + len(phrase)], prefix, suffix


def inspect_rows(rows):
    records = []
    for line, row in enumerate(rows, 1):
        record = {
            "id": f"teacher:{line}",
            "line": line,
            "category": None,
            "source": "https://doi.org/10.5281/zenodo.4276130",
            "provenance": "日本語教師が学習者の典型誤りを模して作成",
            "validation": [],
        }
        if len(row) != 2:
            record["raw_fields"] = row
            record["validation"].append("column_count")
        else:
            record["raw_error"], record["raw_correction"] = row
            error, correction = marked(row[0], "<", ">"), marked(row[1], "(", ")")
            if error is None or correction is None:
                record["validation"].append("marker_format")
            else:
                record.update(
                    error=error[0],
                    correction=correction[0],
                    error_span=error[1],
                    correction_span=correction[1],
                )
                if (error[2], error[3]) != (correction[2], correction[3]):
                    record["validation"].append("outside_context_mismatch")
                if error[0] == correction[0]:
                    record["validation"].append("identical_error_and_correction")
        record["passes_exact_markup_context_checks"] = not record["validation"]
        records.append(record)
    valid = [r for r in records if "error" in r]
    counts = Counter(code for r in records for code in r["validation"])
    error_counts = Counter(r["error"] for r in valid)
    correct_counts = Counter(r["correction"] for r in valid)
    pairs = Counter((r["error"], r["correction"]) for r in valid)
    summary = {
        "rows": len(records),
        "format_valid_rows": len(valid),
        "exact_markup_context_pass_rows": sum(
            r["passes_exact_markup_context_checks"] for r in records
        ),
        "validation_counts": dict(counts),
        "unique_clean_error": len(error_counts),
        "unique_clean_correction": len(correct_counts),
        "unique_clean_pairs": len(pairs),
        "duplicate_clean_pair_excess": sum(n - 1 for n in pairs.values()),
        "repeated_clean_error_groups": sum(n > 1 for n in error_counts.values()),
        "rows_in_repeated_clean_error_groups": sum(n for n in error_counts.values() if n > 1),
        "max_error_group_size": max(error_counts.values()),
        "repeated_clean_correction_groups": sum(n > 1 for n in correct_counts.values()),
        "rows_in_repeated_clean_correction_groups": sum(
            n for n in correct_counts.values() if n > 1
        ),
        "empty_error_spans": sum(r["error_span"][0] == r["error_span"][1] for r in valid),
        "empty_correction_spans": sum(
            r["correction_span"][0] == r["correction_span"][1] for r in valid
        ),
        "raw_identical_pairs": sum(r.get("raw_error") == r.get("raw_correction") for r in records),
        "validation_examples": {
            code: [
                {
                    "line": r["line"],
                    "raw_error": r.get("raw_error"),
                    "raw_correction": r.get("raw_correction"),
                }
                for r in records
                if code in r["validation"]
            ][:8]
            for code in counts
        },
    }
    return records, summary


def restore_selection(records, metadata):
    """検査済み行の所属と選択を固定記録から戻す。groupの新規計算はしない。"""
    eligible = [row for row in records if row["passes_exact_markup_context_checks"]]
    by_line = {row["line"]: row for row in eligible}
    group_of = {
        row["line"]: group for row, group in zip(eligible, metadata["group_ids"], strict=True)
    }
    dev_pool = set(metadata["dev_pool_group_ids"])
    selection = {}
    selected = {}
    for name in ("dev", "test"):
        lines = metadata[f"{name}_lines"]
        if len(set(lines)) != len(lines) or not set(lines) <= by_line.keys():
            raise ValueError("選択記録に重複または検査非通過の原行がある")
        selected[name] = []
        for line in lines:
            if line in selection or (group_of[line] in dev_pool) != (name == "dev"):
                raise ValueError("dev/testの重複またはpool所属の不一致")
            selection[line] = name
            selected[name].append(dict(by_line[line], split=name, group_id=group_of[line]))
    held_groups = {group_of[line] for line in selection}
    audit_lines = metadata["audit_lines"]
    if not set(audit_lines) <= by_line.keys():
        raise ValueError("監査記録に検査非通過の原行がある")
    audit_groups = {group_of[line] for line in audit_lines}
    if len(audit_groups) != len(audit_lines) or audit_groups & held_groups:
        raise ValueError("監査groupが重複またはdev/test保留groupへ混入している")
    refs = defaultdict(list)
    for row in eligible:
        refs[row["error"]].append(row["id"])
    audit = []
    for rank, line in enumerate(audit_lines, 1):
        row = by_line[line]
        audit.append(
            {
                "selection_rank": rank,
                "group_id": group_of[line],
                "pair_id": row["id"],
                "source_line": line,
                "error": row["error"],
                "correction": row["correction"],
                "error_span": row["error_span"],
                "correction_span": row["correction_span"],
                "same_error_reference_ids": refs[row["error"]],
                "source": row["source"],
                "category": None,
            }
        )
    assignments = [
        {
            "id": row["id"],
            "source_line": row["line"],
            "group_id": group_of[row["line"]],
            "pool": "dev_pool" if group_of[row["line"]] in dev_pool else "test_pool",
            "selected_split": selection.get(row["line"]),
        }
        for row in eligible
    ]
    return assignments, selected, audit


def jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")


def run(source, output):
    if output.exists():
        raise FileExistsError("出力先が既に存在するため上書きしない")
    metadata_bytes = SELECTION.read_bytes()
    metadata = json.loads(metadata_bytes)
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["source"]["sha256"]:
        raise ValueError("指定されたteacher_data.tsvの原本SHA256と一致しない")
    records, summary = inspect_rows(
        csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), delimiter="\t")
    )
    inspected = jsonl(records)
    if (
        hashlib.sha256(inspected).hexdigest()
        != metadata["expected_sha256"]["teacher-inspected.jsonl"]
    ):
        raise ValueError("原記号の検査結果が固定実験のhashと一致しない")
    assignments, selected, audit = restore_selection(records, metadata)
    payloads = {
        "teacher-inspected.jsonl": inspected,
        "inspection-summary.json": (
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        "split-20261021/assignment.jsonl": jsonl(assignments),
        **{f"split-20261021/{name}.jsonl": jsonl(rows) for name, rows in selected.items()},
        "unused-group-audit-20261021/audit-part-1.jsonl": jsonl(audit[:500]),
        "unused-group-audit-20261021/audit-part-2.jsonl": jsonl(audit[500:]),
    }
    actual = {name: hashlib.sha256(body).hexdigest() for name, body in payloads.items()}
    if actual != metadata["expected_sha256"]:
        raise ValueError("固定選択の再生結果が既存出力のhashと一致しない")
    output.mkdir(parents=True)
    for name, body in payloads.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    manifest = {
        "purpose": "原本と固定selection metadataから2026-10-21実験を再生。新たな分割・監査分類は行わない。",
        "source": dict(metadata["source"], input_path=str(source)),
        "seed": metadata["seed"],
        "original_preparation": metadata["original_preparation"],
        "selection_metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "output_sha256": actual,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "rows": len(records),
                "eligible_pairs": len(assignments),
                "dev_pairs": len(selected["dev"]),
                "test_pairs": len(selected["test"]),
                "reserved_audit_groups": len(audit),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.output)
