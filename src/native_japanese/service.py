"""解析結果と直接観測・比較可能な支持を結び、原文の端点を返す。"""

import math
from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .caseframes import Reference
from .examples import Examples
from .models import Analysis, Query, Relation
from .parser import Parser
from .resources import read_runtime
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
        config_path: str | Path,
        cache_path: str | Path,
        examples_path: str | Path | None = None,
        threshold: float = 0.35,
        timeout: float = 30,
    ):
        if not math.isfinite(threshold) or not -1 <= threshold <= 1:
            raise ValueError("thresholdには有限の-1以上1以下の数を指定してください。")
        self.threshold = threshold
        self._stack = ExitStack()
        try:
            runtime = read_runtime(Path(config_path))
            self.reference = self._stack.enter_context(Reference(runtime, cache_path))
            self.examples = (
                self._stack.enter_context(Examples(examples_path))
                if examples_path is not None
                else None
            )
            self.parser = Parser(runtime, timeout=timeout)
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
        assessments = []
        diagnostics = []
        for relation in analysis.relations:
            queries = []
            # 追加集合の直接観測があれば、基底のモデル計算を必要としない。
            if self.examples is not None:
                for query in relation.queries:
                    evidence = self.examples.lookup(query)
                    if evidence:
                        queries.append(QueryAssessment(query, Support(True, None, None, evidence)))
            if not queries:
                for query in relation.queries:
                    observed = self.reference.observed(query)
                    if observed is not None:
                        queries.append(QueryAssessment(query, observed))
                        break
            if not queries:
                for query in relation.queries:
                    support = self.reference.assess(query)
                    queries.append(QueryAssessment(query, support))
                    if support.observed:
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
            if any(item.support.observed for item in queries):
                status = "observed"
            elif score is None:
                status = "unavailable"
                if not reasons:
                    reasons = ("比較可能な照会がありません。",)
            elif score < self.threshold:
                status = "review"
                noun = relation.argument.text if relation.argument else "項"
                message = (
                    f"「{noun}」と「{relation.predicate.text}」の組合せを確認してください。"
                    "直接の用例がなく、比較できる用例群からの支持が低い状態です。"
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
        return tuple(self.lint(text) for text in texts)
