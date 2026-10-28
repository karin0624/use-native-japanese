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


@dataclass(frozen=True)
class LintResult:
    analysis: Analysis
    relations: tuple[RelationAssessment, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class JudgeResult:
    relations: tuple[RelationAssessment, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


class UsageJudge:
    """解析を受け取り、用法照合だけの局所viewと参照照会を行う。"""

    def __init__(self, reference: Reference, threshold: float):
        self.reference = reference
        self.threshold = threshold

    def assess(self, analysis: Analysis) -> JudgeResult:
        analysis.validate_comparison()
        assessments = []
        diagnostics = []
        for relation in analysis.comparison.relations:
            queries = []
            for query in relation.queries:
                support = self.reference.assess(query)
                if support.score is not None and not math.isfinite(support.score):
                    raise ValueError("用例との比較結果に、無限大や数値でない値が含まれています。")
                queries.append(QueryAssessment(query, support))
                if support.indexed:
                    break
            scores = [item.support.score for item in queries if item.support.score is not None]
            score = max(scores) if scores else None
            if any(item.support.indexed for item in queries):
                status = "indexed"
            elif score is None or score < self.threshold:
                status = "review"
                noun = relation.argument.text if relation.argument else "語"
                message = (
                    f"「{noun}」と「{relation.predicate.text}」の組み合わせに合う用例が見つかりません。"
                    "表現を見直してください。"
                )
                ranges = (
                    *relation.predicate.ranges,
                    *(relation.argument.ranges if relation.argument else ()),
                )
                diagnostics.extend(Diagnostic(span, message) for span in dict.fromkeys(ranges))
            else:
                status = "supported"
            assessments.append(RelationAssessment(relation, status, score, tuple(queries)))
        return JudgeResult(tuple(assessments), tuple(diagnostics))


class Inspector:
    """同じ解析器と参照を再利用する検査API。位置はPython文字添字。"""

    def __init__(
        self,
        reference_path: str | Path,
        assets_dir: str | Path,
        threshold: float = 0.35,
    ):
        if not math.isfinite(threshold) or not -1 <= threshold <= 1:
            raise ValueError(
                "thresholdには-1以上1以下の数値を指定してください。NaNや無限大は使えません。"
            )
        self.threshold = threshold
        self._stack = ExitStack()
        try:
            reference = self._stack.enter_context(Reference(reference_path, assets_dir))
            self.judges = (UsageJudge(reference, threshold),)
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
        results = tuple(judge.assess(analysis) for judge in self.judges)
        return LintResult(
            analysis,
            tuple(row for result in results for row in result.relations),
            tuple(row for result in results for row in result.diagnostics),
        )

    def lint_many(self, texts: Iterable[str]) -> tuple[LintResult, ...]:
        return tuple(self._assess(analysis) for analysis in self.parser.analyze_many(texts))
