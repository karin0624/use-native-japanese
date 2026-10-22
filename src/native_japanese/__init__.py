"""用例に基づく日本語の語の組合せ検査。APIはservice.Inspectorから利用する。"""

from .models import Analysis, Endpoint, Query, Relation, Unavailable

__all__ = ["Analysis", "Endpoint", "Query", "Relation", "Unavailable"]
