"""不適切な日本語を位置と修正案で指摘するツール。現在は用法照合を開発中。

修正案の生成は未実装。APIはservice.Inspectorから利用する。
"""

from .models import Analysis, Endpoint, Query, Relation

__all__ = ["Analysis", "Endpoint", "Query", "Relation"]
