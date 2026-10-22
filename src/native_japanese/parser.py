"""GiNZAの公開出力を変更せず標準dataclassへ移すadapter。"""

from collections.abc import Iterable, Iterator
from importlib.metadata import version

from spacy.tokens import Doc

from .structure import Document, ParseMetadata, Sentence, Token


def from_spacy(doc: Doc, *, source_id: str, metadata: ParseMetadata) -> Document:
    """既存Docを変換する。lemma・表層・文境界を独自に補完しない。"""
    if len(doc):
        missing = [
            name
            for name in ("DEP", "SENT_START", "LEMMA", "POS")
            if not doc.has_annotation(name, require_complete=True)
        ]
        if missing:
            raise ValueError(f"必須の公開注釈が全tokenで揃っていません: {', '.join(missing)}")
    return Document(
        source_id=source_id,
        text=doc.text,
        tokens=tuple(
            Token(
                id=t.i,
                start=t.idx,
                end=t.idx + len(t.text),
                surface=t.text,
                lemma=t.lemma_,
                upos=t.pos_,
                tag=t.tag_,
                morph=str(t.morph),
                head=t.head.i,
                dep=t.dep_,
            )
            for t in doc
        ),
        sentences=tuple(
            Sentence(i, s.start, s.end, s.start_char, s.end_char) for i, s in enumerate(doc.sents)
        ),
        metadata=metadata,
    )


class GinzaParser:
    """インストール済みの固定版だけをロードする。モデルの自動取得は行わない。"""

    def __init__(self) -> None:
        import spacy

        required = {"ginza": "5.3.0", "ja-ginza": "5.3.0", "spacy": "3.8.16"}
        for name, expected in required.items():
            if version(name) != expected:
                raise ValueError(f"{name}は固定版 {expected} が必要です")
        self._nlp = spacy.load("ja_ginza")
        self.metadata = ParseMetadata(
            parser_name="ginza",
            parser_version=version("ginza"),
            model_name="ja_ginza",
            model_version=self._nlp.meta["version"],
            spacy_version=spacy.__version__,
            dictionary_name="SudachiDict-core",
            dictionary_version=version("SudachiDict-core"),
            tokenizer_version=version("SudachiPy"),
        )

    def parse(self, text: str, *, source_id: str) -> Document:
        """入力全文を解析し、原文の改変がないことを確認する。"""
        return next(self.parse_many(((source_id, text),)))

    def parse_many(
        self, records: Iterable[tuple[str, str]], *, batch_size: int = 32
    ) -> Iterator[Document]:
        """(出現元ID, 原文)をnlp.pipeで順に処理し、文書単位で返す。"""
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_sizeは正の整数を指定してください")
        inputs = ((text, (source_id, text)) for source_id, text in records)
        for doc, (source_id, text) in self._nlp.pipe(inputs, as_tuples=True, batch_size=batch_size):
            if doc.text != text:
                raise ValueError("解析出力の全文が入力原文と一致しません")
            yield from_spacy(doc, source_id=source_id, metadata=self.metadata)
