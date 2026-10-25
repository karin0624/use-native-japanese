"""既存の成分正規化と名詞合成を、固定群の等票平均へ接続する。"""

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.linalg import norm


@dataclass(frozen=True)
class Support:
    indexed: bool
    score: float | None
    reason: str | None
    evidence: tuple[dict, ...] = ()
    comparable: int = 0
    total: int = 0


def _checked_vector(value, size, subject):
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (size,):
        raise ValueError(f"{subject}の次元数が不正です。")
    if not np.isfinite(vector).all():
        raise ValueError(f"{subject}に、無限大や数値でない値が含まれています。")
    return vector


class Vectors:
    """公開合成APIへlemma列を渡す。独自の表記分割はしない。"""

    def __init__(self, model):
        self.model = model
        self.component = lru_cache(maxsize=4096)(self._component)

    def _component(self, noun):
        if not noun:
            raise ValueError("比較する語が空です。解析結果を確認してください。")
        # FastTextは未知語にもTrueを返すため、既存の文字ngramによる合成を利用できる。
        if noun not in self.model:
            return None
        vector = _checked_vector(
            self.model.get_vector(noun), self.model.vector_size, f"単語辞書の「{noun}」の値"
        )
        magnitude = float(norm(vector))
        if not math.isfinite(magnitude):
            raise ValueError(f"単語辞書の「{noun}」の値を正規化できません。")
        if magnitude == 0:
            return None
        return vector / magnitude

    def compose(self, components):
        if not components:
            raise ValueError("比較する語がありません。解析結果を確認してください。")
        vectors = [self.component(lemma) for lemma in components]
        missing = dict.fromkeys(
            lemma for lemma, vector in zip(components, vectors) if vector is None
        )
        if missing:
            words = "、".join(f"「{lemma}」" for lemma in missing)
            return None, f"{words}を比較するための情報が、付属の単語辞書にありません。"
        vector = _checked_vector(
            self.model.get_mean_vector(
                vectors, pre_normalize=False, post_normalize=True, ignore_missing=False
            ),
            self.model.vector_size,
            "語句を比較するための計算結果",
        )
        magnitude = float(norm(vector))
        if not math.isfinite(magnitude):
            raise ValueError("語句を比較するための計算結果を正規化できません。")
        if magnitude == 0:
            return None, "語を組み合わせた計算結果がゼロになり、用例と比較できません。"
        return vector / magnitude, None


def group_mean(nouns, vectors):
    """distinct名詞列を一票ずつ逐次平均する。頻度重みは加えない。"""
    mean = np.zeros(vectors.model.vector_size, dtype=np.float64)
    comparable = total = 0
    for noun in nouns:
        total += 1
        vector, _ = vectors.compose(noun)
        if vector is not None:
            comparable += 1
            mean += (vector - mean) / comparable
    if not comparable:
        reason = "収録用例を比較に使えません。" if total else "比較に使う用例がありません。"
        return None, 0, total, reason
    if not np.isfinite(mean).all():
        raise ValueError("用例を比較するための計算結果に、無限大や数値でない値が含まれています。")
    if norm(mean) == 0:
        return mean, comparable, total, "用例を比較するための計算結果がゼロになりました。"
    reason = "収録用例の一部は比較に使えません。" if comparable < total else None
    return mean, comparable, total, reason


def direction(x, mean):
    """同じ固定平均の方向への内積を返す。不能を支持ゼロへ変換しない。"""
    if x.ndim != 1 or mean.shape != x.shape:
        raise ValueError("用例との比較に使う値の次元数が一致しません。")
    x = _checked_vector(x, x.size, "比較対象の値")
    mean = _checked_vector(mean, x.size, "比較する用例の値")
    magnitude = float(norm(mean))
    x_magnitude = float(norm(x))
    if not all(map(math.isfinite, (magnitude, x_magnitude))):
        raise ValueError("用例との比較に使う値を正規化できません。")
    if magnitude == 0 or x_magnitude == 0:
        return None
    score = float(np.dot(x, mean)) / magnitude
    if not math.isfinite(score):
        raise ValueError("用例との比較結果に、無限大や数値でない値が含まれています。")
    return score
