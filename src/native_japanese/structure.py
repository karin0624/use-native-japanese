"""公開解析の正本と、原文順を保つ2〜4 nodeの連結誘導fragment。"""

from dataclasses import dataclass

import igraph as ig

SCHEMA_VERSION = "native-japanese.structure.v1"


@dataclass(frozen=True, slots=True)
class ParseMetadata:
    """解析器・モデル・辞書の来歴。異なるschema間の同値性は保証しない。"""

    parser_name: str
    parser_version: str
    model_name: str
    model_version: str
    spacy_version: str
    dictionary_name: str
    dictionary_version: str
    tokenizer_version: str
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class Token:
    """offsetは原文のUnicode codepointによる半開区間、id/headはdoc全体の番号。"""

    id: int
    start: int
    end: int
    surface: str
    lemma: str
    upos: str
    tag: str
    morph: str
    head: int
    dep: str


@dataclass(frozen=True, slots=True)
class Sentence:
    """doc.sentsの文境界。token_endおよびendは含まない。"""

    id: int
    token_start: int
    token_end: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Document:
    """原文と未加工の公開解析を保存し、基本依存木／森として検証する。"""

    source_id: str
    text: str
    tokens: tuple[Token, ...]
    sentences: tuple[Sentence, ...]
    metadata: ParseMetadata

    def __post_init__(self) -> None:
        previous_end = 0
        for expected_id, token in enumerate(self.tokens):
            if token.id != expected_id:
                raise ValueError("token IDはdoc全体の0始まりの連番で指定してください")
            if not previous_end <= token.start < token.end <= len(self.text):
                raise ValueError("tokenの原文offsetが範囲外、重複、または原文順ではありません")
            if self.text[token.start : token.end] != token.surface:
                raise ValueError("token表層が原文の指定範囲と一致しません")
            previous_end = token.end

        next_token = 0
        for expected_id, sentence in enumerate(self.sentences):
            if sentence.id != expected_id or sentence.token_start != next_token:
                raise ValueError("文IDとtoken境界は原文順で全tokenを一度ずつ覆ってください")
            if not sentence.token_start < sentence.token_end <= len(self.tokens):
                raise ValueError("文のtoken境界が不正です")
            tokens = self.tokens[sentence.token_start : sentence.token_end]
            if (sentence.start, sentence.end) != (tokens[0].start, tokens[-1].end):
                raise ValueError("文の原文境界が先頭・末尾tokenの境界と一致しません")
            for token in tokens:
                if not sentence.token_start <= token.head < sentence.token_end:
                    raise ValueError("依存先headが同じ文のtoken範囲にありません")
            next_token = sentence.token_end
        if next_token != len(self.tokens):
            raise ValueError("文境界に属さないtokenがあります")

        # 自己headはrootの公開表現として残し、graphの辺には変換しない。
        graph = ig.Graph(
            n=len(self.tokens),
            edges=[(t.head, t.id) for t in self.tokens if t.head != t.id],
            directed=True,
        )
        if not graph.is_dag():
            raise ValueError("基本依存木／森には自己head以外のcycleを含められません")


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """原文順の局所node番号によるhead→childの有向関係。"""

    head: int
    child: int
    dep: str


@dataclass(frozen=True, slots=True)
class FragmentShape:
    """lemmaを除いた構造。nodeの順序は原文順で固定する。"""

    upos: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]


def shape_to_tuple(
    shape: FragmentShape,
) -> tuple[tuple[str, ...], tuple[tuple[int, int, str], ...]]:
    """shapeを固定順の標準tupleに変換する。絶対位置やlemmaは含まない。"""
    return shape.upos, tuple((e.head, e.child, e.dep) for e in shape.edges)


@dataclass(frozen=True, slots=True)
class FragmentKey:
    """宣言した抽象化での同一性。surface/morph/絶対位置は含めない。"""

    shape: FragmentShape
    lemmas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Fragment:
    """一つのnode集合の出現。anchor slotごとに出現を複製しない。"""

    source_id: str
    sentence: Sentence
    node_ids: tuple[int, ...]
    lemmas: tuple[str, ...]
    shape: FragmentShape

    @property
    def key(self) -> FragmentKey:
        """同一性はshapeとlemma列だけで定義する。"""
        return FragmentKey(self.shape, self.lemmas)


class FragmentBudgetExceeded(RuntimeError):
    """予算超過で完走していないことを示す。部分集合は返さない。"""

    def __init__(self, source_id: str, sentence_id: int, max_fragments: int) -> None:
        self.source_id = source_id
        self.sentence_id = sentence_id
        self.max_fragments = max_fragments
        super().__init__(
            f"出現元 {source_id!r} の文 {sentence_id} はfragment上限"
            f" {max_fragments} 件を超えました。列挙は未完了です"
        )


def enumerate_fragments(
    document: Document, *, sentence_id: int, max_fragments: int
) -> tuple[Fragment, ...]:
    """指定文の全2〜4 node弱連結誘導fragmentを列挙する。予算はこの1文に適用。

    語彙・品詞によるnode選別や日本語固有の結合は行わない。4 node外の文脈、
    活用・語義の同一性、誤り位置・正誤はこのkeyで評価しない。
    """
    if type(max_fragments) is not int or max_fragments < 0:
        raise ValueError("max_fragmentsは0以上の整数を必ず指定してください")
    if type(sentence_id) is not int or not 0 <= sentence_id < len(document.sentences):
        raise ValueError("sentence_idが文の範囲外です")
    sentence = document.sentences[sentence_id]
    tokens = document.tokens[sentence.token_start : sentence.token_end]
    offset = sentence.token_start
    graph = ig.Graph(
        n=len(tokens),
        edges=[(t.head - offset, t.id - offset) for t in tokens if t.head != t.id],
        directed=True,
    )
    fragments: list[Fragment] = []

    def append_nodes(local_ids: list[int] | tuple[int, int]) -> None:
        if len(fragments) >= max_fragments:
            raise FragmentBudgetExceeded(document.source_id, sentence_id, max_fragments)
        node_ids = tuple(sorted(i + offset for i in local_ids))
        positions = {node_id: i for i, node_id in enumerate(node_ids)}
        selected = tuple(document.tokens[i] for i in node_ids)
        edges = tuple(
            sorted(
                (
                    DependencyEdge(positions[t.head], positions[t.id], t.dep)
                    for t in selected
                    if t.head != t.id and t.head in positions
                ),
                key=lambda edge: (edge.head, edge.child, edge.dep),
            )
        )
        fragments.append(
            Fragment(
                document.source_id,
                sentence,
                node_ids,
                tuple(t.lemma for t in selected),
                FragmentShape(tuple(t.upos for t in selected), edges),
            )
        )

    for edge in graph.get_edgelist():
        append_nodes(edge)

    def collect(_graph: ig.Graph, vertices: list[int], _isoclass: int) -> None:
        # callbackのID順や無ラベルisoclassは同一性に使わない。
        append_nodes(vertices)

    for size in (3, 4):
        if size <= graph.vcount():
            graph.motifs_randesu(size=size, cut_prob=None, callback=collect)
    return tuple(sorted(fragments, key=lambda f: (len(f.node_ids), f.node_ids)))
