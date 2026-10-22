"""aliasの等分・IDの一票・不能と方向の分離を小さいvectorで確認する。"""

import numpy as np
import pytest
from gensim.models import KeyedVectors

from native_japanese.support import Vectors, direction, group_mean, item_mean


def vector_model():
    model = KeyedVectors(vector_size=300)
    values = np.zeros((5, 300), dtype=np.float32)
    values[0, :2] = (1, 0)
    values[1, :2] = (0, 1)
    values[2, :2] = (-1, 0)
    values[3, :2] = (0, 0)
    values[4, :2] = (np.nan, 0)
    model.add_vectors(["東", "北", "西", "零", "非有限"], values)
    return Vectors(model)


def test_alias_mean_then_equal_ids_without_renormalizing():
    vectors = vector_model()
    aliases = {
        "1": (b"\xff", "東/ひがし".encode(), "北/きた".encode()),
        "2": ("西/にし".encode(),),
        "3": ("零/れい".encode(),),
    }
    first = item_mean("1", lambda key: aliases.get(key, ()), vectors)
    assert first[:2] == pytest.approx((0.5, 0.5))
    mean, comparable, total, reason = group_mean(("1", "2", "3", "1?2"), aliases.get, vectors)
    assert (comparable, total, reason) == (2, 4, None)
    assert mean[:2] == pytest.approx((-0.25, 0.25))
    x, _ = vectors.compose(("東/ひがし",))
    assert direction(x, mean) == pytest.approx(-1 / np.sqrt(2))
    assert float(x @ mean) == pytest.approx(-0.25)


def test_full_compound_product_is_streamed_and_missing_component_is_not_shortened():
    vectors = vector_model()
    aliases = {
        "1": ("東/ひがし".encode(), "北/きた".encode()),
        "2": ("東/ひがし".encode(), "西/にし".encode()),
    }
    # 東+西のゼロ合成は不能。他の三つを等分し、成分平均へ置換しない。
    result = item_mean("1+2", aliases.get, vectors)
    expected = np.mean(
        [
            vectors.compose(pair)[0]
            for pair in [
                ("東/ひがし", "東/ひがし"),
                ("北/きた", "東/ひがし"),
                ("北/きた", "西/にし"),
            ]
        ],
        axis=0,
        dtype=np.float64,
    )
    assert result == pytest.approx(expected)
    assert item_mean("1+9", lambda key: aliases.get(key, ()), vectors) is None
    assert item_mean("1?2", aliases.get, vectors) is None
    assert item_mean("6", lambda key: ("<主体準>".encode(),), vectors) is None


def test_empty_zero_nonfinite_and_singleton_direction():
    vectors = vector_model()
    assert group_mean((), lambda key: (), vectors)[:3] == (None, 0, 0)
    assert vectors.compose(("非有限/ひゆうげん",))[0] is None
    assert vectors.compose(("零/れい",))[0] is None
    x, _ = vectors.compose(("東/ひがし",))
    assert direction(x, x) == pytest.approx(1)
    assert direction(x, np.zeros(300)) is None
    assert direction(x, np.full(300, np.nan)) is None
    assert direction(x, x.astype(np.float64) * 1e-300) == pytest.approx(1)
    mean, count, total, reason = group_mean(
        ("1", "2"), {"1": ("東/ひがし".encode(),), "2": ("西/にし".encode(),)}.get, vectors
    )
    assert count == total == 2
    assert reason and direction(x, mean) is None
