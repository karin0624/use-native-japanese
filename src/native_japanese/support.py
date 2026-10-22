"""既存の名詞合成と、通常frame内のalias/ID等票重心による支持。"""

import itertools
import math
import re
from dataclasses import dataclass, replace
from functools import lru_cache

import numpy as np
from scipy.linalg import norm

from .models import Query


@dataclass(frozen=True)
class Support:
    observed: bool
    score: float | None
    reason: str | None
    evidence: tuple[dict, ...] = ()
    comparable: int = 0
    total: int = 0


def codec(components):
    """既存代表表記の形式を読む。欠損・未確定の成分を補完しない。"""
    heads = []
    for raw in components:
        if raw.count("/") != 1:
            return None, "代表表記の見出し/読みが未対応です"
        head, reading = raw.split("/")
        if not head or not reading or re.search(r"[+?~\s]", raw):
            return None, "代表表記に空成分または未確認区切りがあります"
        heads.append(head)
    return (heads, None) if heads else (None, "構成語が空です")


class Vectors:
    """固定loaderのmodelを使い、旧adapterの検査・公開合成を保つ。"""

    def __init__(self, model):
        self.model = model
        # 入力語数に比例して常駐memoryを増やさない。
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
        magnitude = float(np.linalg.norm(vector))
        if not math.isfinite(magnitude) or magnitude == 0:
            return None
        return vector / magnitude

    def compose(self, components):
        heads, reason = codec(components)
        if reason:
            return None, reason
        vectors = [self.component(head) for head in heads]
        if any(vector is None for vector in vectors):
            return None, "成分vectorが取得できません"
        vector = self.model.get_mean_vector(
            vectors, pre_normalize=False, post_normalize=True, ignore_missing=False
        )
        magnitude = float(np.linalg.norm(vector))
        if not np.isfinite(vector).all() or not math.isfinite(magnitude) or magnitude == 0:
            return None, "合成vectorが非有限またはゼロです"
        return vector / magnitude, None


def item_mean(identifier, aliases, vectors):
    """組合せを逐次加算し、比較可能alias平均を再正規化せず返す。"""
    if "?" in identifier or not re.fullmatch(r"[0-9]+(?:\+[0-9]+)*", identifier):
        return None
    choices = [aliases(component) for component in identifier.split("+")]
    if any(not choice for choice in choices):
        return None
    total = np.zeros(vectors.model.vector_size, dtype=np.float64)
    count = 0
    for product in itertools.product(*choices):
        try:
            components = [raw.decode("utf-8") for raw in product]
        except UnicodeDecodeError:
            continue
        vector, _ = vectors.compose(components)
        if vector is not None:
            total += vector
            count += 1
    return total / count if count else None


def group_mean(units, aliases, vectors):
    """ID列の平均を一票ずつ加算する。入力語や頻度には依存しない。"""
    total = np.zeros(vectors.model.vector_size, dtype=np.float64)
    comparable = 0
    for identifier in units:
        mean = item_mean(identifier, aliases, vectors)
        if mean is not None:
            total += mean
            comparable += 1
    if not comparable:
        return None, 0, len(units), "比較可能な実語IDがありません"
    mean = total / comparable
    if not np.isfinite(mean).all():
        return None, comparable, len(units), "群の重心が非有限です"
    if norm(mean) == 0:
        return mean, comparable, len(units), "群の重心が厳密にゼロです"
    return mean, comparable, len(units), None


def direction(x, mean):
    """足切りを加えず、同じ重心の内積を安定な公開BLAS normで割る。"""
    if not np.isfinite(mean).all() or not np.isfinite(x).all():
        return None
    magnitude = float(norm(mean))
    if magnitude == 0 or not math.isfinite(magnitude):
        return None
    score = float(np.dot(x, mean)) / magnitude
    return score if math.isfinite(score) else None


def _evidence(frame, case):
    return {
        "source": "base",
        "frame_id": frame.frame_id,
        "case": case,
        "offset": frame.offset,
        "length": frame.length,
        "sha256": frame.sha256,
    }


def observed_reference(reference, query: Query) -> Support | None:
    if query.predicate_type not in ("動", "形", "判", "名"):
        return None
    identifiers = {
        identifier for _, identifier in reference.candidates(query.noun) if identifier is not None
    }
    for frame in reference.frames(query.predicate):
        if frame.predicate_type == query.predicate_type and identifiers.intersection(
            frame.units(query.case)
        ):
            return Support(True, None, None, (_evidence(frame, query.case),))
    return None


def assess_reference(reference, query: Query) -> Support:
    if query.predicate_type not in ("動", "形", "判", "名"):
        return Support(False, None, "未対応の用言型です")
    candidates = reference.candidates(query.noun)
    identifiers = {identifier for _, identifier in candidates if identifier is not None}
    frames = [
        frame
        for frame in reference.frames(query.predicate)
        if frame.predicate_type == query.predicate_type
    ]
    for frame in frames:
        if identifiers.intersection(frame.units(query.case)):
            return Support(True, None, None, (_evidence(frame, query.case),))
    ordinary = [frame for frame in frames if not frame.is_sum]
    if not ordinary:
        return Support(False, None, "同じ原述語・型の通常frameがありません")
    if not reference.prepared:
        raise RuntimeError("名詞alias索引が未準備です。prepareを実行してください")
    vectors = reference.vectors
    groups = []
    reasons = set()
    for frame in ordinary:
        cached = reference.group(frame, query.case)
        if cached is None:
            mean, comparable, total, reason = group_mean(
                frame.units(query.case), reference.aliases, vectors
            )
            reference.save_group(frame, query.case, mean, comparable, total, reason)
        else:
            blob, comparable, total, reason = cached
            mean = np.frombuffer(blob, dtype="<f8") if blob is not None else None
            if not 0 <= comparable <= total or (
                mean is not None
                and (mean.shape != (vectors.model.vector_size,) or not np.isfinite(mean).all())
            ):
                raise ValueError("保存された群cacheのvector/分母が不正です")
        if reason:
            reasons.add(reason)
        if comparable < total:
            reasons.add("群内の一部実語IDは比較不能です")
        groups.append((frame, mean, comparable, total))
    best = None
    for representation, _ in candidates:
        x, reason = vectors.compose(representation.split("+"))
        if x is None:
            reasons.add(reason)
            continue
        for frame, mean, comparable, total in groups:
            score = direction(x, mean) if mean is not None else None
            if score is not None and (best is None or score > best.score):
                best = Support(
                    False,
                    score,
                    None,
                    ({**_evidence(frame, query.case), "noun_candidate": representation},),
                    comparable,
                    total,
                )
    if best is not None:
        return replace(best, reason="；".join(sorted(reasons)) or None)
    return Support(
        False,
        None,
        "；".join(sorted(reasons)) or "群の方向を比較できません",
        comparable=sum(g[2] for g in groups),
        total=sum(g[3] for g in groups),
    )
