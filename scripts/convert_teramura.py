"""寺村誤用例集TXTの原注釈を保つ最小変換。日本語の解析・訂正はしない。"""

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

EXPECTED_SHA256 = "b63dc40c63a6559e389f6f1af5e59ed18607a1513e9112dfa60c74cba0c53181"
DIGITS = r"[０-９0-9]+"
NUMBER_LIST = DIGITS + "(?:，" + DIGITS + ")*"
SUFFIX = re.compile("（(" + NUMBER_LIST + ")）")
LABEL_PREFIX = re.compile(r"(?<!\S)(" + NUMBER_LIST + "|＆)")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def split_numbers(value):
    return [int(part) for part in value.split("，")]


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def parse_markers(text):
    """括弧・直後の番号・欠落記号だけを除去し、元文字を順序通りに保つ。"""
    clean, annotations, removed = [], [], []
    position = 0
    while position < len(text):
        if text[position] == "】":
            raise ValueError("閉じ括弧だけの注釈")
        if text[position] != "【":
            clean.append(text[position])
            position += 1
            continue
        close = text.find("】", position + 1)
        if close < 0 or "【" in text[position + 1 : close]:
            raise ValueError("未閉鎖または入れ子の注釈")
        content = text[position + 1 : close]
        if not content or ("＿" in content and content != "＿"):
            raise ValueError("空注釈または欠落記号と本文が混在")
        start = len(clean)
        gap = content == "＿"
        if not gap:
            clean.extend(content)
        removed.extend([[position, position + 1], [close, close + 1]])
        if gap:
            removed.append([position + 1, close])
        suffix = SUFFIX.match(text, close + 1)
        identifiers = split_numbers(suffix.group(1)) if suffix else []
        end = suffix.end() if suffix else close + 1
        if suffix:
            removed.append([close + 1, end])
        elif text[close + 1 : close + 2] in ("（", "("):
            raise ValueError("注釈直後の未対応括弧")
        annotations.append(
            {
                "kind": "gap" if gap else "span",
                "span": [start, len(clean)],
                "annotation_numbers": identifiers,
                "marked_text": content,
                "annotated_content_span": [position + 1, close],
                "raw_marker": text[position:end],
                "label_links": [],
            }
        )
        position = end
    value = "".join(clean)
    if "＿" in value or re.search("[（(]" + NUMBER_LIST + "[）)]", value):
        raise ValueError("未対応の欠落記号または注釈番号が本文に残る")
    # 除去した構造以外の文字が変化していないことを独立に照合する。
    removed_positions = {index for a, b in removed for index in range(a, b)}
    assert (
        "".join(char for index, char in enumerate(text) if index not in removed_positions) == value
    )
    return value, annotations, sorted(removed)


def link_labels(label_raw, annotations, text_length):
    """番号と＆の対応だけを読む。ラベル内の語を訂正文へ解釈しない。"""
    reasons, units, whole = [], [], []
    prefixes = list(LABEL_PREFIX.finditer(label_raw))
    initial = label_raw[: prefixes[0].start()].strip() if prefixes else label_raw.strip()
    if initial:
        units.append({"numbers": [], "raw": initial})
    for index, prefix in enumerate(prefixes):
        end = prefixes[index + 1].start() if index + 1 < len(prefixes) else len(label_raw)
        raw = label_raw[prefix.end() : end].strip()
        if not raw:
            reasons.append("番号または全文記号の後にラベルがない")
        if prefix.group(1) == "＆":
            whole.append(
                {
                    "kind": "whole_text",
                    "span": [0, text_length],
                    "annotation_numbers": [],
                    "label_raw": raw,
                }
            )
        else:
            units.append({"numbers": split_numbers(prefix.group(1)), "raw": raw})
    marker_numbers = {n for annotation in annotations for n in annotation["annotation_numbers"]}
    number_counts = Counter(
        n for annotation in annotations for n in annotation["annotation_numbers"]
    )
    if any(count > 1 for count in number_counts.values()):
        reasons.append("本文注釈番号が重複している")
    label_numbers = {n for unit in units for n in unit["numbers"]}
    numbered_annotations = [
        annotation for annotation in annotations if annotation["annotation_numbers"]
    ]
    unnumbered_annotations = [
        annotation for annotation in annotations if not annotation["annotation_numbers"]
    ]
    unnumbered_units = [unit for unit in units if not unit["numbers"]]
    if marker_numbers or label_numbers:
        if marker_numbers != label_numbers:
            reasons.append("本文とラベルの番号集合が一致しない")
        if unnumbered_annotations or unnumbered_units:
            reasons.append("番号付きと番号なしの対応が確定しない")
        for annotation in numbered_annotations:
            annotation["label_links"] = [
                unit
                for unit in units
                if set(unit["numbers"]) & set(annotation["annotation_numbers"])
            ]
    elif len(annotations) == 1 and len(unnumbered_units) == 1:
        annotations[0]["label_links"] = unnumbered_units
    elif annotations or unnumbered_units:
        reasons.append("番号なし注釈とラベルの対応が一意でない")
    if not annotations and not whole:
        reasons.append("局所範囲も全文誤り記号もない")
    # 括弧の意味は公式記号説明だけから決めず、補正や分類へ使わない。
    if any(char in label_raw for char in "（）()"):
        reasons.append("ラベル内括弧の意味を解釈していない")
    if "？" in label_raw or "?" in label_raw:
        reasons.append("公式説明にない疑問記号を含むラベル")
    return whole, units, reasons


def parse_source_reference(value):
    match = re.fullmatch("(.+?" + DIGITS + ")，(.+?)(" + DIGITS + ")", value)
    if not match:
        raise ValueError("国籍・学習者番号と作文形式・文番号を区別できない")
    learner, form, number = match.groups()
    return {
        "learner_identifier_raw": learner,
        "composition_type_raw": form,
        "sentence_number_raw": number,
        "sentence_number": int(number),
        "source_sentence_key": [learner, form, int(number)],
        "essay_id": None,
        "note": "末尾番号は作文内の文番号。作文IDは提供されず推測しない。",
    }


def toc_entries(lines):
    entries, chapter = [], None
    for source_line, line in enumerate(lines, 1):
        match = re.fullmatch("第(" + DIGITS + ")章[　 ](.+?)(?:・・・(" + DIGITS + "))?", line)
        if match:
            number, title, page = match.groups()
            chapter = {"number": int(number), "title": title, "toc_source_line": source_line}
            if page:
                entries.append(
                    {
                        "code": str(int(number)),
                        "title": title,
                        "page": int(page),
                        "chapter": chapter,
                    }
                )
        else:
            match = re.fullmatch("([０-９0-9．]+)．(.+?)・・・(" + DIGITS + ")", line)
            if match and chapter:
                code, title, page = match.groups()
                entries.append(
                    {
                        "code": ".".join(str(int(part)) for part in code.split("．")),
                        "title": title,
                        "page": int(page),
                        "chapter": chapter,
                    }
                )
    return entries


def official_label_categories(lines):
    """原本の略号表を読み、記載されたカテゴリ対応だけを保持する。"""
    result = {}
    for number, line in enumerate(lines, 1):
        fields = next(csv.reader([line], delimiter="\t", quoting=csv.QUOTE_NONE))
        if len(fields) == 3 and re.fullmatch(
            DIGITS + "(?:．" + DIGITS + ")*(?:～" + DIGITS + ")?", fields[2]
        ):
            label, title, code = fields
            result[label] = {
                "label_raw": label,
                "title": title,
                "code_raw": code,
                "chapter": int(code.split("．")[0]),
                "definition_source_line": number,
            }
    return result


def attach_label_categories(records, mapping):
    for row in records:
        for annotation in row["annotations"]:
            categories = []
            for unit in annotation["label_links"]:
                for token in re.split(r"[\s／]+", unit["raw"]):
                    operation = "misused"
                    label = token
                    if token.startswith("＊"):
                        operation, label = "overused", token[1:]
                    elif token.startswith("－"):
                        operation, label = "missing", token[1:]
                    if label in mapping:
                        categories.append(
                            {"raw_label_token": token, "operation": operation, **mapping[label]}
                        )
            annotation["official_label_categories"] = categories


def category_for_page(entries, page):
    eligible = [entry for entry in entries if entry["page"] <= page]
    if not eligible:
        raise ValueError("掲載ページに対応する目次項目がない")
    return max(eligible, key=lambda entry: (entry["page"], len(entry["code"].split("."))))


def convert_record(source_line, raw_line, fields, page, category):
    prefix = re.match(DIGITS, fields[0]) if fields else None
    row = {
        "source_line": source_line,
        "publication_id": int(prefix.group()) if prefix else None,
        "raw_publication_id": fields[0] if fields else None,
        "raw_source_line": raw_line,
        "raw_fields": fields,
        "publication_page": page,
        "publication_category": category,
        "status": "unresolved",
        "reasons": [],
        "annotated_text": None,
        "input_text": None,
        "raw_labels": None,
        "annotations": [],
        "whole_text_annotations": [],
    }
    if len(fields) != 4 or not fields[0].isdigit():
        row.update(status="excluded", reasons=["通番単独の先頭列を含む4列TSVでない"])
        return row
    _, reference, annotated, label_raw = fields
    row.update(source_reference_raw=reference, annotated_text=annotated, raw_labels=label_raw)
    try:
        row["source_reference"] = parse_source_reference(reference)
    except ValueError as error:
        row["reasons"].append(str(error))
    try:
        clean, annotations, removed = parse_markers(annotated)
        row.update(input_text=clean, annotations=annotations, removed_annotated_ranges=removed)
    except ValueError as error:
        row["reasons"].append(str(error))
        return row
    whole, units, reasons = link_labels(label_raw, annotations, len(clean))
    row.update(whole_text_annotations=whole, label_units_raw=units)
    row["reasons"].extend(reasons)
    if not row["reasons"]:
        row["status"] = "resolved"
    return row


def candidate_groups(rows):
    """完全一致入力または同じ出典参照を持つ行を連結成分APIでまとめ、group候補を付与する。"""
    import scipy
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    owners, left, right = {}, [], []
    for index, row in enumerate(rows):
        keys = []
        if row["input_text"] is not None:
            keys.append(("text", row["input_text"]))
        if "source_reference" in row:
            keys.append(("source", tuple(row["source_reference"]["source_sentence_key"])))
        for key in keys:
            if key in owners:
                left.append(owners[key])
                right.append(index)
            else:
                owners[key] = index
    graph = csr_matrix(([1] * len(left), (left, right)), shape=(len(rows), len(rows)))
    count, labels = connected_components(graph, directed=False)
    result = []
    for row, group in zip(rows, labels):
        row["candidate_group_id"] = int(group)
        result.append(
            {
                "source_line": row["source_line"],
                "publication_id": row["publication_id"],
                "candidate_group_id": int(group),
                "status": row["status"],
                "exact_input_sha256": hashlib.sha256(row["input_text"].encode()).hexdigest()
                if row["input_text"] is not None
                else None,
                "learner_group_key_raw": row.get("source_reference", {}).get(
                    "learner_identifier_raw"
                ),
                "source_sentence_key": row.get("source_reference", {}).get("source_sentence_key"),
            }
        )
    sources = defaultdict(set)
    for row in rows:
        if "source_reference" in row and row["input_text"] is not None:
            sources[tuple(row["source_reference"]["source_sentence_key"])].add(row["input_text"])
    return result, {
        "candidate_groups": count,
        "scipy": scipy.__version__,
        "largest_candidate_group_rows": max(Counter(labels).values()),
        "source_sentence_keys": len(
            {
                tuple(row["source_reference"]["source_sentence_key"])
                for row in rows
                if "source_reference" in row
            }
        ),
        "source_keys_with_multiple_distinct_inputs": sum(
            len(values) > 1 for values in sources.values()
        ),
        "learner_identifiers": len(
            {
                row["source_reference"]["learner_identifier_raw"]
                for row in rows
                if "source_reference" in row
            }
        ),
    }


def summarize(rows):
    statuses = Counter(row["status"] for row in rows)
    reasons = Counter(reason for row in rows for reason in set(row["reasons"]))
    chapters, categories = defaultdict(list), defaultdict(list)
    for row in rows:
        chapters[str(row["publication_category"]["chapter"]["number"])].append(row)
        categories[row["publication_category"]["code"]].append(row)

    def counts(members):
        inputs = {row["input_text"] for row in members if row["input_text"] is not None}
        return {
            "published_rows": len(members),
            "known_unique_input_texts": len(inputs),
            "resolved_rows": sum(row["status"] == "resolved" for row in members),
            "resolved_unique_input_texts": len(
                {row["input_text"] for row in members if row["status"] == "resolved"}
            ),
        }

    span_types = Counter(annotation["kind"] for row in rows for annotation in row["annotations"])
    coverage = {}
    for scope in ("all_known_inputs", "resolved_rows"):
        members = [
            row
            for row in rows
            if row["input_text"] is not None
            and (scope == "all_known_inputs" or row["status"] == "resolved")
        ]
        total_chars = sum(len(row["input_text"]) for row in members)
        marked_chars = sum(
            len(
                {
                    index
                    for annotation in row["annotations"]
                    if annotation["kind"] == "span"
                    for index in range(*annotation["span"])
                }
            )
            for row in members
        )
        coverage[scope] = {
            "row_weighted_input_characters": total_chars,
            "row_weighted_local_error_characters": marked_chars,
            "local_character_fraction": marked_chars / total_chars if total_chars else None,
            "note": "再掲載行を含む文字被覆。全体誤り・gapは局所文字数へ加算しない。",
        }
        by_text = defaultdict(set)
        for row in members:
            by_text[row["input_text"]].update(
                index
                for annotation in row["annotations"]
                if annotation["kind"] == "span"
                for index in range(*annotation["span"])
            )
        unique_total = sum(map(len, by_text))
        unique_marked = sum(len(value) for value in by_text.values())
        coverage[scope].update(
            unique_input_characters=unique_total,
            unique_input_local_error_characters=unique_marked,
            unique_input_local_character_fraction=unique_marked / unique_total
            if unique_total
            else None,
        )
    resolved_local = [
        (row, annotation)
        for row in rows
        if row["status"] == "resolved"
        for annotation in row["annotations"]
        if annotation["kind"] == "span"
    ]
    lexical = [
        (row, annotation)
        for row, annotation in resolved_local
        if any(
            c["chapter"] == 3 and c["operation"] != "missing"
            for c in annotation["official_label_categories"]
        )
    ]
    positions = {
        "resolved_published_local_spans": len(resolved_local),
        "resolved_unique_input_and_local_spans": len(
            {(row["input_text"], *annotation["span"]) for row, annotation in resolved_local}
        ),
        "resolved_published_lexical_label_spans": len(lexical),
        "resolved_unique_input_and_lexical_spans": len(
            {(row["input_text"], *annotation["span"]) for row, annotation in lexical}
        ),
        "resolved_unique_inputs_with_lexical_spans": len({row["input_text"] for row, _ in lexical}),
        "lexical_definition": "公式略号表の第3章に対応する原ラベルLまたは＊Lを持つ非空span。－Lだけの対象カテゴリをsource語の誤用へ読み替えない。連語・語彙選択専用の分類とは限らない。",
    }
    return {
        "published_rows": len(rows),
        "status": dict(statuses),
        "unresolved_or_excluded_reasons": dict(reasons),
        "total": counts(rows),
        "chapters": {key: counts(value) for key, value in chapters.items()},
        "categories": {key: counts(value) for key, value in categories.items()},
        "annotation_geometry": dict(span_types),
        "usable_position_annotation_candidates": positions,
        "whole_text_annotation_count": sum(len(row["whole_text_annotations"]) for row in rows),
        "character_coverage": coverage,
        "excluded_records": [
            {
                "source_line": row["source_line"],
                "publication_id": row["publication_id"],
                "reasons": row["reasons"],
            }
            for row in rows
            if row["status"] == "excluded"
        ],
    }


def input_inventory(records):
    by_text, unknown_by_source = defaultdict(list), defaultdict(list)
    for row in records:
        if row["input_text"] is not None:
            by_text[row["input_text"]].append(row)
        elif "source_reference" in row:
            unknown_by_source[tuple(row["source_reference"]["source_sentence_key"])].append(row)
    result = []
    for text, rows in by_text.items():
        source_keys = {
            tuple(row["source_reference"]["source_sentence_key"])
            for row in rows
            if "source_reference" in row
        }
        related_unknown = {
            r["publication_id"] for key in source_keys for r in unknown_by_source[key]
        }
        result.append(
            {
                "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "input_text": text,
                "candidate_group_id": rows[0]["candidate_group_id"],
                "publication_ids": [row["publication_id"] for row in rows],
                "source_lines": [row["source_line"] for row in rows],
                "learner_identifiers_raw": sorted(
                    {
                        row["source_reference"]["learner_identifier_raw"]
                        for row in rows
                        if "source_reference" in row
                    }
                ),
                "source_sentence_keys": sorted(source_keys),
                "resolved_publication_ids": [
                    row["publication_id"] for row in rows if row["status"] == "resolved"
                ],
                "unresolved_publication_ids": [
                    row["publication_id"] for row in rows if row["status"] == "unresolved"
                ],
                "has_unresolved_annotation_on_same_input": any(
                    row["status"] == "unresolved" for row in rows
                ),
                "has_whole_text_annotation": any(row["whole_text_annotations"] for row in rows),
                "has_gap_annotation": any(
                    annotation["kind"] == "gap" for row in rows for annotation in row["annotations"]
                ),
                "same_source_reference_with_unknown_input_ids": sorted(related_unknown),
                "unknown_source_note": "同じ出典参照で入力化できない行があれば別記。全文同一とは断定しない。",
            }
        )
    return result


def run(source, output):
    if output.exists():
        raise FileExistsError("出力先が既に存在するため上書きしない")
    if digest(source) != EXPECTED_SHA256:
        raise ValueError("指定された原本SHA256と一致しない")
    raw_lines = source.read_text(encoding="utf-8").splitlines()
    fields = [
        next(csv.reader(io.StringIO(line), delimiter="\t", quoting=csv.QUOTE_NONE)) if line else []
        for line in raw_lines
    ]
    start = next(index for index, row in enumerate(fields) if len(row) == 1 and row[0].isdigit())
    assert int(fields[start][0]) == 1
    toc = toc_entries(raw_lines[:start])
    label_mapping = official_label_categories(raw_lines[:start])
    records, page, page_headers, other = [], None, 0, []
    for index in range(start, len(fields)):
        row = fields[index]
        if len(row) == 1 and row[0].isdigit():
            page, page_headers = int(row[0]), page_headers + 1
        elif row and re.match(DIGITS, row[0]):
            records.append(
                convert_record(index + 1, raw_lines[index], row, page, category_for_page(toc, page))
            )
        else:
            other.append({"source_line": index + 1, "raw_line": raw_lines[index]})
    if other:
        raise ValueError("本文に未分類行がある。黙って落とさず資料構造を再確認する")
    assert len(records) == 7415
    attach_label_categories(records, label_mapping)
    groups, group_stats = candidate_groups(records)
    output.mkdir(parents=True)
    write_jsonl(output / "records.jsonl", records)
    write_jsonl(
        output / "unresolved-and-excluded.jsonl", [r for r in records if r["status"] != "resolved"]
    )
    write_jsonl(output / "candidate-groups.jsonl", groups)
    inventory = input_inventory(records)
    write_jsonl(output / "input-inventory.jsonl", inventory)
    (output / "official-label-categories.json").write_text(
        json.dumps(label_mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = summarize(records)
    summary.update(
        group_stats,
        source_total_lines=len(raw_lines),
        documentation_lines=start,
        page_headers=page_headers,
    )
    summary["unique_input_inventory"] = {
        "inputs_with_unresolved_annotation": sum(
            row["has_unresolved_annotation_on_same_input"] for row in inventory
        ),
        "inputs_with_whole_text_annotation": sum(
            row["has_whole_text_annotation"] for row in inventory
        ),
        "inputs_with_gap_annotation": sum(row["has_gap_annotation"] for row in inventory),
        "inputs_with_unknown_input_under_same_source_reference": sum(
            bool(row["same_source_reference_with_unknown_input_ids"]) for row in inventory
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "source": str(source),
        "source_sha256": digest(source),
        "source_bytes": source.stat().st_size,
        "official_doi": "https://doi.org/10.15084/00003533",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "changes": "CSV読取、注釈構造の除去と原文字offset化、未解決理由・掲載カテゴリ・group候補の付与。日本語正規化・訂正・新規gold生成はしない。",
        "script_sha256": digest(__file__),
        "output_sha256": {
            name: digest(output / name)
            for name in [
                "records.jsonl",
                "unresolved-and-excluded.jsonl",
                "candidate-groups.jsonl",
                "official-label-categories.json",
                "input-inventory.jsonl",
                "summary.json",
            ]
        },
        "grouping": "完全一致入力または国籍付き学習者ID＋作文形式＋文番号を共有する掲載行の連結成分。別途学習者keyも保持。作文IDの確定やdev/test分割を意味しない。",
        "limitations": [
            "元冊子の3131誤文と掲載7415行は異なる単位。カテゴリ別の再掲載がある。",
            "入力textが存在してもstatus=unresolvedの範囲/ラベルを確定goldとして利用しない。",
            "resolvedは原記号の対応が確定した意味で、人手注釈が完全・一貫している保証ではない。",
            "－の後はカテゴリ名もあり、訂正文の文字列へ復元しない。",
            "目次とページによるカテゴリは掲載先。文全体や全注釈がそのカテゴリだけを含む意味ではない。",
            "ラベル内部はraw保存。括弧の意味が不明な行は未解決とし、表記ゆれも修正しない。",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: summary[key]
                for key in [
                    "published_rows",
                    "status",
                    "total",
                    "chapters",
                    "annotation_geometry",
                    "whole_text_annotation_count",
                    "candidate_groups",
                    "excluded_records",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.output)
