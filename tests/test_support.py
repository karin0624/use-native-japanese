"""lemma合成・等票平均・不能と支持ゼロの違いを確認する。"""

import numpy as np
import pytest
from gensim.models import KeyedVectors
from gensim.models.fasttext import FastTextKeyedVectors

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
    result = vectors.compose(("東", "北"))
    assert result[:2] == pytest.approx((1 / np.sqrt(2),) * 2)
    assert vectors.compose(("a/+ b",))[0] == pytest.approx(1)
    assert vectors.compose(("東", "未収録")) is None


def test_distinct_nouns_get_equal_votes_and_fixed_direction():
    vectors = vector_model()
    # distinct化は正本が行う。ここへは逐次入力を渡し、比較不能を分母へ含める。
    mean, comparable, total = group_mean(iter([("東",), ("北",), ("零",)]), vectors)
    assert (comparable, total) == (2, 3)
    assert mean[:2] == pytest.approx((0.5, 0.5))
    x = vectors.compose(("東",))
    assert direction(x, mean) == pytest.approx(1 / np.sqrt(2))
    assert float(x @ mean) == pytest.approx(0.5)


def test_empty_zero_nonfinite_and_singleton_direction():
    vectors = vector_model()
    assert group_mean(iter(()), vectors)[:3] == (None, 0, 0)
    for noun in ("零", "未収録"):
        assert vectors.compose((noun,)) is None
    assert vectors.compose(("東", "未収録", "零", "未収録")) is None
    assert vectors.compose(("東", "西")) is None
    with pytest.raises(ValueError, match="語がありません"):
        vectors.compose(())
    with pytest.raises(ValueError, match="語が空"):
        vectors.compose(("",))
    with pytest.raises(ValueError, match="非有限"):
        vectors.compose(("非有限",))
    with pytest.raises(ValueError):
        group_mean(iter([("東",), ("非有限",)]), vectors)
    x = vectors.compose(("東",))
    assert direction(x, x) == pytest.approx(1)
    assert direction(x, np.zeros(300)) is None
    assert direction(np.zeros(300), x) is None
    with pytest.raises(ValueError):
        direction(x, np.full(300, np.nan))
    with pytest.raises(ValueError):
        direction(x[:2], x)
    assert direction(x, x * 1e-300) == pytest.approx(1)
    mean, count, total = group_mean(iter([("東",), ("西",)]), vectors)
    assert count == total == 2 and direction(x, mean) is None


def test_fasttext_unknown_words_keep_subword_vectors_and_report_zero_coverage():
    model = FastTextKeyedVectors(vector_size=300, min_n=5, max_n=5, bucket=64)
    model.vectors_ngrams = np.ones((64, 300), dtype=np.float32)
    vectors = Vectors(model)
    assert not model.has_index_for("未知の語句")
    result = vectors.compose(("未知の語句",))
    assert np.linalg.norm(result) == pytest.approx(1)
    # 二文字語からは、このモデルが使う長さ5の文字ngramを作れない。
    result = vectors.compose(("短語",))
    assert result is None
    model.vectors_ngrams[:] = 0
    result = Vectors(model).compose(("未知の語句",))
    assert result is None


@pytest.mark.parametrize("method", ["get_vector", "get_mean_vector"])
@pytest.mark.parametrize("error", [KeyError, ValueError, TypeError])
def test_dependency_failures_are_not_reported_as_missing_information(monkeypatch, method, error):
    vectors = vector_model()

    def fail(*args, **kwargs):
        raise error("依存処理の異常")

    monkeypatch.setattr(vectors.model, method, fail)
    with pytest.raises(error, match="依存処理の異常"):
        vectors.compose(("東",))


def test_damaged_vector_dimensions_stop_comparison():
    vectors = vector_model()
    vectors.model.vectors = vectors.model.vectors[:, :2]
    with pytest.raises(ValueError, match="次元数"):
        vectors.compose(("東",))
