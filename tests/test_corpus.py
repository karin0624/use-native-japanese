"""解析正本の再開境界、原本照合、公開注釈の無改変保存を点検する。"""

import hashlib
import json
import sqlite3
import zlib
from dataclasses import asdict, replace

import pytest

from native_japanese import Document, ParseMetadata, Sentence, Token
from native_japanese.corpus import ParsedCorpus, parse_corpus

METADATA = ParseMetadata("fixture", "1", "fixture", "1", "1", "fixture", "1", "1")


class SavedParser:
    metadata = METADATA

    def __init__(self, *, fail_after=None, omit_last=False):
        self.seen = []
        self.fail_after = fail_after
        self.omit_last = omit_last

    def parse_many(self, records, *, batch_size):
        for source_id, text in records:
            if self.fail_after is not None and len(self.seen) == self.fail_after:
                raise RuntimeError("中断を模擬")
            self.seen.append(source_id)
            if self.omit_last and source_id == "last":
                return
            yield Document(
                source_id,
                text,
                (Token(0, 0, len(text), text, "fixture", "NOUN", "公開tag", "Case=Gen", 0, "ROOT"),)
                if text
                else (),
                (Sentence(0, 0, 1, 0, len(text)),) if text else (),
                self.metadata,
            )


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.jsonl"
    rows = [
        {"docid": "first", "article_id": "b", "text": "🧪 e\u0301　Ａ"},
        {"docid": "empty", "article_id": "a", "text": ""},
        {"docid": "last", "article_id": "b", "text": "公開原文"},
    ]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return path


def test_resume_parses_only_remaining_documents_and_preserves_raw_annotations(source, tmp_path):
    output = tmp_path / "parsed.sqlite"
    with pytest.raises(RuntimeError, match="中断"):
        parse_corpus(source, output, parser=SavedParser(fail_after=1))
    with pytest.raises(ValueError, match="未完了"):
        ParsedCorpus(output)
    parser = SavedParser()
    corpus = parse_corpus(source, output, parser=parser)
    assert parser.seen == ["empty", "last"]
    assert (corpus.document_count, corpus.token_count, corpus.vocabulary_size) == (3, 2, 1)
    documents = list(corpus.documents())
    assert [(a, d.source_id) for a, d in documents] == [
        ("b", "first"),
        ("a", "empty"),
        ("b", "last"),
    ]
    assert documents[0][1].text == "🧪 e\u0301　Ａ"
    token = documents[0][1].tokens[0]
    assert (token.start, token.end, token.surface, token.tag, token.morph) == (
        0,
        6,
        "🧪 e\u0301　Ａ",
        "公開tag",
        "Case=Gen",
    )
    assert documents[1][1].tokens == documents[1][1].sentences == ()
    assert list(corpus.documents()) == documents
    assert [d.source_id for _, d in corpus.documents(order="article")] == ["empty", "first", "last"]
    expected = sorted(
        documents, key=lambda pair: hashlib.sha256(f"42:{pair[1].source_id}".encode()).digest()
    )
    assert list(corpus.documents(order="seed", seed=42)) == expected
    expected_digest = hashlib.sha256()
    for article_id, document in documents:
        expected_digest.update(
            json.dumps(
                [article_id, asdict(document)],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            + b"\n"
        )
    assert corpus.content_sha256 == expected_digest.hexdigest()
    parser = SavedParser()
    assert parse_corpus(source, output, parser=parser).content_sha256 == corpus.content_sha256
    assert parser.seen == []


@pytest.mark.parametrize("change", ["source", "metadata", "limit"])
def test_resume_rejects_different_source_parser_or_snapshot(source, tmp_path, change):
    output = tmp_path / "parsed.sqlite"
    with pytest.raises(RuntimeError):
        parse_corpus(source, output, parser=SavedParser(fail_after=1))
    parser = SavedParser()
    kwargs = {}
    if change == "source":
        source.write_text(source.read_text().replace("公開原文", "異なる原文"))
    elif change == "metadata":
        parser.metadata = replace(METADATA, dictionary_version="2")
    else:
        kwargs["limit"] = 2
    with pytest.raises(ValueError, match="一致しません"):
        parse_corpus(source, output, parser=parser, **kwargs)
    assert parser.seen == []


@pytest.mark.parametrize("damage", ["missing", "text"])
def test_resume_rejects_missing_or_changed_stored_document(source, tmp_path, damage):
    output = tmp_path / "parsed.sqlite"
    with pytest.raises(RuntimeError):
        parse_corpus(source, output, parser=SavedParser(fail_after=2))
    with sqlite3.connect(output) as db:
        if damage == "missing":
            db.execute("DELETE FROM sources WHERE id=1")
        else:
            raw = json.loads(
                zlib.decompress(db.execute("SELECT document FROM sources WHERE id=1").fetchone()[0])
            )
            raw["text"] = "x" * len(raw["text"])
            raw["tokens"][0]["surface"] = raw["text"]
            db.execute(
                "UPDATE sources SET document=? WHERE id=1",
                (zlib.compress(json.dumps(raw).encode()),),
            )
    with pytest.raises(ValueError, match="一致しません|不一致"):
        parse_corpus(source, output, parser=SavedParser())


def test_duplicate_source_ids_and_wrong_expected_hash_or_count_are_not_ignored(source, tmp_path):
    parser = SavedParser()
    with pytest.raises(ValueError, match="SHA256"):
        parse_corpus(
            source, tmp_path / "hash.sqlite", parser=parser, expected_source_sha256="0" * 64
        )
    with pytest.raises(ValueError, match="予定文書数"):
        parse_corpus(source, tmp_path / "count.sqlite", parser=parser, expected_documents=4)
    source.write_text(source.read_text() + source.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="重複"):
        parse_corpus(source, tmp_path / "duplicate.sqlite", parser=parser)
    assert parser.seen == []


def test_short_parser_output_keeps_incomplete_boundary(source, tmp_path):
    output = tmp_path / "parsed.sqlite"
    with pytest.raises(ValueError, match="全文書"):
        parse_corpus(source, output, parser=SavedParser(omit_last=True))
    with pytest.raises(ValueError, match="未完了"):
        ParsedCorpus(output)
    parser = SavedParser()
    assert parse_corpus(source, output, parser=parser).document_count == 3
    assert parser.seen == ["last"]


def test_input_mutation_is_detected_before_completing(source, tmp_path):
    class MutatingParser(SavedParser):
        def parse_many(self, records, *, batch_size):
            for document in super().parse_many(records, batch_size=batch_size):
                source.write_bytes(source.read_bytes() + b"\n")
                yield document

    output = tmp_path / "parsed.sqlite"
    with pytest.raises(ValueError, match="変更"):
        parse_corpus(source, output, parser=MutatingParser())
    with pytest.raises(ValueError, match="未完了"):
        ParsedCorpus(output)


def test_prefix_snapshot_is_explicit_and_cannot_be_opened_as_full(source, tmp_path):
    output = tmp_path / "prefix.sqlite"
    corpus = parse_corpus(source, output, parser=SavedParser(), limit=1, expected_documents=1)
    assert corpus.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert corpus.manifest["source_documents"] == 3
    assert corpus.manifest["selection"] == "input_prefix"
    assert corpus.manifest["limit"] == corpus.document_count == 1
    with pytest.raises(ValueError, match="snapshot"):
        parse_corpus(source, output, parser=SavedParser())


def test_reader_rejects_document_deleted_after_opening(source, tmp_path):
    output = tmp_path / "parsed.sqlite"
    corpus = parse_corpus(source, output, parser=SavedParser())
    with sqlite3.connect(output) as db:
        db.execute("DELETE FROM sources WHERE id=3")
    with pytest.raises(ValueError, match="境界"):
        list(corpus.documents())


def test_initialization_failure_rolls_back_schema_and_can_restart(source, tmp_path, monkeypatch):
    import native_japanese.corpus as module

    output = tmp_path / "parsed.sqlite"
    original = module._save_manifest

    def fail_initial_manifest(db, manifest):
        raise OSError("初期保存の中断を模擬")

    monkeypatch.setattr(module, "_save_manifest", fail_initial_manifest)
    with pytest.raises(OSError, match="初期保存"):
        parse_corpus(source, output, parser=SavedParser())
    monkeypatch.setattr(module, "_save_manifest", original)
    parser = SavedParser()
    assert parse_corpus(source, output, parser=parser).document_count == 3
    assert parser.seen == ["first", "empty", "last"]


def test_article_order_sorts_source_ids_within_article(source, tmp_path):
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    rows[0]["docid"], rows[2]["docid"] = "z-first", "a-last"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    corpus = parse_corpus(source, tmp_path / "parsed.sqlite", parser=SavedParser())
    assert [doc.source_id for _, doc in corpus.documents(order="article")] == [
        "empty",
        "a-last",
        "z-first",
    ]
