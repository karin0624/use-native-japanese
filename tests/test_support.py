"""lemma合成・等票平均・不能と支持ゼロの違いを確認する。"""

import numpy as np
import pytest
from gensim.models import KeyedVectors

from native_japanese.support import Vectors, direction, group_mean


def vector_model():
    model = KeyedVectors(vector_size=300)
    values = np.zeros((7, 300), dtype=np.float32)
    values[:3, :2] = ((5, 0), (0, 2), (-3, 0))
    values[4, 0] = np.nan
    values[5, :2] = (1, 1)
    values[6, 0] = 2
    model.add_vectors(["東", "北", "西", "零", "非有限", "入力", "a/+ b"], values)
    return Vectors(model)


def test_lemmas_are_not_reparsed_and_components_have_equal_norm():
    vectors = vector_model()
    result, reason = vectors.compose(("東", "北"))
    assert reason is None and result[:2] == pytest.approx((1 / np.sqrt(2),) * 2)
    assert vectors.compose(("a/+ b",))[0][0] == pytest.approx(1)
    assert vectors.compose(("東", "未収録"))[0] is None


def test_distinct_nouns_get_equal_votes_and_fixed_direction():
    vectors = vector_model()
    # distinct化は正本が行う。ここへは逐次入力を渡し、比較不能を分母へ含める。
    mean, comparable, total, reason = group_mean(iter([("東",), ("北",), ("零",)]), vectors)
    assert (comparable, total) == (2, 3)
    assert reason
    assert mean[:2] == pytest.approx((0.5, 0.5))
    x, _ = vectors.compose(("東",))
    assert direction(x, mean) == pytest.approx(1 / np.sqrt(2))
    assert float(x @ mean) == pytest.approx(0.5)


def test_empty_zero_nonfinite_and_singleton_direction():
    vectors = vector_model()
    assert group_mean(iter(()), vectors)[:3] == (None, 0, 0)
    for noun in ("零", "非有限", "未収録"):
        assert vectors.compose((noun,))[0] is None
    assert vectors.compose(())[0] is None
    assert vectors.compose(("東", "西"))[0] is None
    x, _ = vectors.compose(("東",))
    assert direction(x, x) == pytest.approx(1)
    assert direction(x, np.zeros(300)) is None
    assert direction(np.zeros(300), x) is None
    assert direction(x, np.full(300, np.nan)) is None
    assert direction(x, x * 1e-300) == pytest.approx(1)
    mean, count, total, reason = group_mean(iter([("東",), ("西",)]), vectors)
    assert count == total == 2 and reason and direction(x, mean) is None
