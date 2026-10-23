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


class Vectors:
    """公開合成APIへlemma列を渡す。独自の表記分割はしない。"""

    def __init__(self, model):
        self.model = model
        self.component = lru_cache(maxsize=4096)(self._component)

    def _component(self, noun):
        if not noun:
            return None
        try:
            vector = np.asarray(self.model.get_vector(noun), dtype=np.float64)
        except (KeyError, ValueError, TypeError):
            return None
        if vector.shape != (self.model.vector_size,) or not np.isfinite(vector).all():
            return None
        magnitude = float(norm(vector))
        if not math.isfinite(magnitude) or magnitude == 0:
            return None
        return vector / magnitude

    def compose(self, components):
        if not components:
            return None, "構成語が空です"
        vectors = [self.component(lemma) for lemma in components]
        if any(vector is None for vector in vectors):
            return None, "成分vectorが取得できません"
        try:
            vector = np.asarray(
                self.model.get_mean_vector(
                    vectors, pre_normalize=False, post_normalize=True, ignore_missing=False
                ),
                dtype=np.float64,
            )
        except (KeyError, ValueError, TypeError):
            return None, "合成vectorが取得できません"
        if vector.shape != (self.model.vector_size,) or not np.isfinite(vector).all():
            return None, "合成vectorの形状が不正または非有限です"
        magnitude = float(norm(vector))
        if not math.isfinite(magnitude) or magnitude == 0:
            return None, "合成vectorが非有限またはゼロです"
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
        return None, 0, total, "比較可能な名詞列がありません"
    if not np.isfinite(mean).all():
        return None, comparable, total, "群の平均が非有限です"
    if norm(mean) == 0:
        return mean, comparable, total, "群の平均が厳密にゼロです"
    reason = "群内の一部名詞列は比較不能です" if comparable < total else None
    return mean, comparable, total, reason


def direction(x, mean):
    """同じ固定平均の方向への内積を返す。不能を支持ゼロへ変換しない。"""
    if x.shape != mean.shape or not np.isfinite(mean).all() or not np.isfinite(x).all():
        return None
    magnitude = float(norm(mean))
    x_magnitude = float(norm(x))
    if magnitude == 0 or x_magnitude == 0 or not all(map(math.isfinite, (magnitude, x_magnitude))):
        return None
    score = float(np.dot(x, mean)) / magnitude
    return score if math.isfinite(score) else None
