"""原記号の厳密な読取と固定記録によるgroup保留を確認する。"""

from copy import deepcopy

import pytest

from scripts import prepare_teacher as p


def test_marker_inspection_preserves_characters_offsets_and_unresolved_rows():
    records, summary = p.inspect_rows(
        [
            ["Ａ<Ｂ>Ｃ", "Ａ(Ｄ)Ｃ"],
            ["文<>末", "文(が)末"],
            ["前<字>後", "別(字)後"],
            ["前<字><文>後", "前(字)後"],
            ["<同じ>", "(同じ)"],
            ["列が一つ"],
        ]
    )
    assert records[0]["error"] == "ＡＢＣ"
    assert records[0]["error_span"] == [1, 2]
    assert records[1]["error"] == "文末"
    assert records[1]["error_span"] == [1, 1]
    assert records[1]["correction_span"] == [1, 2]
    assert [row["line"] for row in records if row["passes_exact_markup_context_checks"]] == [1, 2]
    assert [row["validation"] for row in records[2:]] == [
        ["outside_context_mismatch"],
        ["marker_format"],
        ["identical_error_and_correction"],
        ["column_count"],
    ]
    assert summary["rows"] == 6


@pytest.fixture
def selection():
    records, _ = p.inspect_rows(
        [
            ["前<A>後", "前(a)後"],
            ["前<A>後", "前(b)後"],
            ["前<B>後", "前(c)後"],
            ["前<C>後", "前(d)後"],
            ["前<C>後", "前(e)後"],
        ]
    )
    metadata = {
        "group_ids": [0, 0, 1, 2, 2],
        "dev_pool_group_ids": [0, 2],
        "dev_lines": [1],
        "test_lines": [3],
        "audit_lines": [4],
    }
    return records, metadata


def test_replay_keeps_group_membership_unselected_siblings_and_audit_references(selection):
    records, metadata = selection
    assignments, selected, audit = p.restore_selection(records, metadata)
    assert [row["group_id"] for row in assignments] == [0, 0, 1, 2, 2]
    assert [row["selected_split"] for row in assignments] == ["dev", None, "test", None, None]
    assert selected["dev"][0]["id"] == "teacher:1"
    assert selected["test"][0]["id"] == "teacher:3"
    assert audit[0]["group_id"] == 2
    assert audit[0]["same_error_reference_ids"] == ["teacher:4", "teacher:5"]
    assert audit[0]["selection_rank"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"audit_lines": [2]},  # devの未選択同groupも保留する。
        {"test_lines": [999]},  # 原本にない行は選べない。
        {"group_ids": [0, 0]},  # 候補列とgroup記録のずれを許さない。
    ],
)
def test_replay_rejects_reserved_groups_unknown_lines_and_incomplete_membership(selection, change):
    records, original = selection
    metadata = deepcopy(original)
    metadata.update(change)
    with pytest.raises(ValueError):
        p.restore_selection(records, metadata)
