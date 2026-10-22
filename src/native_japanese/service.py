"""解析結果と近似集合・比較可能な支持を結び、原文の端点を返す。"""

import math
from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .models import Analysis, Query, Relation
from .parser import Parser
from .reference import Reference
from .support import Support


@dataclass(frozen=True)
class Diagnostic:
    range: tuple[int, int]
    message: str


@dataclass(frozen=True)
class QueryAssessment:
    query: Query
    support: Support


@dataclass(frozen=True)
class RelationAssessment:
    relation: Relation
    status: str
    score: float | None
    queries: tuple[QueryAssessment, ...]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LintResult:
    analysis: Analysis
    relations: tuple[RelationAssessment, ...]
    diagnostics: tuple[Diagnostic, ...]


class Inspector:
    """同じ解析器と参照を再利用する検査API。位置はPython文字添字。"""

    def __init__(
        self,
        reference_path: str | Path,
        assets_dir: str | Path,
        threshold: float = 0.35,
    ):
        if not math.isfinite(threshold) or not -1 <= threshold <= 1:
            raise ValueError("thresholdには有限の-1以上1以下の数を指定してください。")
        self.threshold = threshold
        self._stack = ExitStack()
        try:
            self.reference = self._stack.enter_context(Reference(reference_path, assets_dir))
            self.parser = Parser()
            self._stack.callback(self.parser.close)
        except BaseException:
            self._stack.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._stack.close()

    def analyze(self, text: str) -> Analysis:
        return self.parser.analyze(text)

    def lint(self, text: str) -> LintResult:
        analysis = self.analyze(text)
        return self._assess(analysis)

    def _assess(self, analysis: Analysis) -> LintResult:
        assessments = []
        diagnostics = []
        for relation in analysis.relations:
            queries = []
            for query in relation.queries:
                support = self.reference.assess(query)
                queries.append(QueryAssessment(query, support))
                if support.indexed:
                    break
            scores = [item.support.score for item in queries if item.support.score is not None]
            score = max(scores) if scores else None
            reasons = tuple(
                dict.fromkeys(
                    (
                        *relation.unavailable_reasons,
                        *(item.support.reason for item in queries if item.support.reason),
                    )
                )
            )
            if any(item.support.indexed for item in queries):
                status = "indexed"
            elif score is None:
                status = "unavailable"
                if not reasons:
                    reasons = ("比較可能な照会がありません。",)
            elif score < self.threshold:
                status = "review"
                noun = relation.argument.text if relation.argument else "項"
                message = (
                    f"「{noun}」と「{relation.predicate.text}」の組合せを確認してください。"
                    "参照索引に一致せず、比較できる用例群からの支持が低い状態です。"
                )
                ranges = (
                    *relation.predicate.ranges,
                    *(relation.argument.ranges if relation.argument else ()),
                )
                diagnostics.extend(Diagnostic(span, message) for span in dict.fromkeys(ranges))
            else:
                status = "supported"
            assessments.append(RelationAssessment(relation, status, score, tuple(queries), reasons))
        return LintResult(analysis, tuple(assessments), tuple(diagnostics))

    def lint_many(self, texts: Iterable[str]) -> tuple[LintResult, ...]:
        return tuple(self._assess(analysis) for analysis in self.parser.analyze_many(texts))
