"""原文と公開依存構造を保持する部品。誤用検出器の完成品ではない。"""

from .structure import (
    SCHEMA_VERSION,
    DependencyEdge,
    Document,
    Fragment,
    FragmentBudgetExceeded,
    FragmentKey,
    FragmentShape,
    ParseMetadata,
    Sentence,
    Token,
    enumerate_fragments,
    shape_to_tuple,
)

__all__ = [
    "SCHEMA_VERSION",
    "DependencyEdge",
    "Document",
    "Fragment",
    "FragmentBudgetExceeded",
    "FragmentKey",
    "FragmentShape",
    "ParseMetadata",
    "Sentence",
    "Token",
    "enumerate_fragments",
    "shape_to_tuple",
]
