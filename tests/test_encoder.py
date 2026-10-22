"""位置・未知対応・window被覆・batchの実際の契約を小さいBERTで確かめる。"""

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("spacy_alignments")

import torch
from transformers import BertConfig, BertJapaneseTokenizer, BertModel

from native_japanese.encoder import EncoderFingerprint, FrozenBertEncoder, _align
from native_japanese.structure import Document, ParseMetadata, Sentence, Token

METADATA = ParseMetadata("fixture", "1", "fixture", "1", "1", "fixture", "1", "1")


def document(text, spans):
    tokens = tuple(
        Token(i, start, end, text[start:end], text[start:end], "X", "", "", i, "root")
        for i, (start, end) in enumerate(spans)
    )
    sentences = (Sentence(0, 0, len(tokens), tokens[0].start, tokens[-1].end),) if tokens else ()
    return Document("fixture", text, tokens, sentences, METADATA)


@pytest.fixture
def encoder(tmp_path):
    vocabulary = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "a", "b", "c", "abc", "##d", "é"]
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join(vocabulary) + "\n")
    tokenizer = BertJapaneseTokenizer(
        vocab_file=str(vocab), word_tokenizer_type="basic", do_lower_case=False
    )
    torch.set_num_threads(1)
    model = BertModel(
        BertConfig(
            vocab_size=len(vocabulary),
            hidden_size=16,
            num_hidden_layers=1,
            num_attention_heads=2,
            intermediate_size=24,
            max_position_embeddings=16,
        )
    )
    fingerprint = EncoderFingerprint("fixture", "fixture", type(tokenizer).__name__, (), (), 8, 2)
    return FrozenBertEncoder(model, tokenizer, fingerprint)


def test_short_empty_and_batch_keep_raw_positions_and_frozen_float32(encoder):
    first = document("a abcd", [(0, 1), (2, 6)])
    second = document("b", [(0, 1)])
    empty = document("", [])
    together = encoder.encode([first, second, empty], batch_size=2)
    separate = encoder.encode([first], batch_size=1)[0]
    assert together[0].offsets == ((0, 1), (2, 6))
    assert together[0].valid.tolist() == [True, True]
    assert together[0].vectors.shape == (2, 16)
    assert together[0].vectors.dtype == np.float32
    np.testing.assert_allclose(together[0].vectors, separate.vectors, atol=1e-6)
    assert together[2].vectors.shape == (0, 16)
    assert together[2].window_spans == ()
    assert encoder.encode([]) == ()
    assert not encoder.model.training
    assert all(not parameter.requires_grad for parameter in encoder.model.parameters())


def test_shared_subword_unknown_and_unicode_offsets_are_not_repaired(encoder):
    shared = document("abc", [(0, 1), (1, 3)])
    unknown = document("😀 a", [(0, 1), (2, 3)])
    unicode = document("é a", [(0, 1), (2, 3)])
    fullwidth = document("ａ", [(0, 1)])
    outputs = encoder.encode([shared, unknown, unicode, fullwidth])
    assert outputs[0].reasons == ("shared_subword", "shared_subword")
    assert not outputs[1].valid[0]
    assert outputs[1].valid[1]
    assert np.isnan(outputs[1].vectors[0]).all()
    assert outputs[2].offsets == ((0, 1), (2, 3))
    assert outputs[2].valid.all()
    # NFKCでaへ修復すれば有効化されるが、原文を変更しない契約では保留する。
    assert not outputs[3].valid[0]


def test_long_input_is_covered_and_overlap_selects_complete_token(encoder):
    text = " ".join(["a"] * 21)
    doc = document(text, [(i, i + 1) for i in range(0, len(text), 2)])
    output = encoder.encode([doc], batch_size=3)[0]
    assert output.valid.all()
    assert np.isfinite(output.vectors).all()
    assert len(output.window_spans) > 1
    assert output.window_spans[0][0] == 0
    assert output.window_spans[-1][1] == len(text)
    for (start, end), window in zip(output.offsets, output.token_windows, strict=True):
        left, right = output.window_spans[window]
        assert left <= start < end <= right
    # 内容長6、overlap2。位置4は第0windowの余白1、第1windowの余白0。
    assert output.token_windows[4] == 0
    assert output.token_windows[5] == 1
    wide = document("a a a a a a a", [(0, 13)])
    result = encoder.encode([wide])[0]
    assert result.reasons == ("incomplete_or_ambiguous",)


def test_raw_token_larger_than_window_is_explicitly_unavailable(encoder):
    # 一つのraw spanが複数subwordとなり、window内に収まらない。隙間文字はない。
    doc = document("abcdddddd", [(0, 9)])
    result = encoder.encode([doc])[0]
    assert result.reasons == ("cross_window",)
    assert result.token_windows.tolist() == [-1]
    assert np.isnan(result.vectors).all()


def test_alignment_does_not_fill_whitespace_gaps(encoder):
    doc = document("a b", [(0, 3)])
    ids = encoder.tokenizer("a b", add_special_tokens=False)["input_ids"]
    mapping, reasons, _ = _align(doc, ids, encoder.tokenizer)
    assert mapping == [(0, 1)]
    assert reasons == ["incomplete_or_ambiguous"]
    # 一つのsubwordが空白を越す対応を既存整列が返しても連続spanへ拡張しない。
    ids = encoder.tokenizer.convert_tokens_to_ids(["abc"])
    doc = document("a b c", [(0, 5)])
    _, reasons, _ = _align(doc, ids, encoder.tokenizer)
    assert reasons[0] is not None


def test_optional_token_type_ids_are_only_forwarded_when_returned(encoder):
    class NoTokenTypes(BertModel):
        """token_type_idsを受け取らない呼出契約を持つ小型モデル。"""

        def forward(self, input_ids, attention_mask):
            return super().forward(input_ids=input_ids, attention_mask=attention_mask)

    encoder.tokenizer.model_input_names = ["input_ids", "token_type_ids", "attention_mask"]
    observed = []
    hook = encoder.model.register_forward_pre_hook(
        lambda module, args, kwargs: observed.append(set(kwargs)), with_kwargs=True
    )
    encoder.encode([document("a", [(0, 1)])])
    hook.remove()
    assert "token_type_ids" in observed[0]
    encoder.tokenizer.model_input_names = ["input_ids", "attention_mask"]
    model = NoTokenTypes(encoder.model.config)
    no_types = FrozenBertEncoder(model, encoder.tokenizer, encoder.fingerprint)
    assert no_types.encode([document("a", [(0, 1)])])[0].valid.all()
    assert encoder.fingerprint.digest != replace(encoder.fingerprint, overlap=1).digest
    with pytest.raises(ValueError, match="overlap"):
        FrozenBertEncoder(model, encoder.tokenizer, replace(encoder.fingerprint, overlap=6))


@pytest.mark.parametrize(
    ("text", "spans"),
    [("ax a", [(0, 1), (1, 2), (3, 4)]), ("abcx abc", [(0, 3), (3, 4), (5, 8)])],
)
def test_unknown_word_does_not_move_later_piece_to_earlier_raw_token(encoder, text, spans):
    output = encoder.encode([document(text, spans)])[0]
    assert output.valid.tolist() == [False, False, True]
    assert output.reasons == ("unknown", "unknown", None)
    assert output.window_spans == ((spans[-1][0], spans[-1][1]),)


def test_added_special_tokens_keep_pre_wordpiece_boundaries(encoder):
    from transformers import AddedToken

    encoder.tokenizer.add_tokens([AddedToken("ax", lstrip=True, rstrip=True)])
    text = "ax a [MASK]a"
    doc = document(text, [(0, 2), (3, 4), (5, 11), (11, 12)])
    ids = encoder.tokenizer(text, add_special_tokens=False)["input_ids"]
    mapping, reasons, offsets = _align(doc, ids, encoder.tokenizer)
    assert mapping == [(0,), (1,), (2,), (3,)]
    assert reasons == [None, None, "special", None]
    assert offsets == [(0, 2), (3, 4), None, (11, 12)]


@pytest.mark.parametrize("directory_symlink", [False, True])
def test_nested_weight_shard_changes_fingerprint(encoder, tmp_path, directory_symlink):
    import json

    from safetensors.torch import load_file, save_file

    directory = tmp_path / "model"
    encoder.tokenizer.save_pretrained(directory)
    encoder.model.save_pretrained(directory, max_shard_size="2KB")
    index_path = directory / "model.safetensors.index.json"
    index = json.loads(index_path.read_text())
    nested = directory / "weights"
    if directory_symlink:
        external = tmp_path / "external"
        external.mkdir()
        nested.symlink_to(external, target_is_directory=True)
    else:
        nested.mkdir()
    for name in set(index["weight_map"].values()):
        (directory / name).rename(nested / name)
    index["weight_map"] = {key: "weights/" + name for key, name in index["weight_map"].items()}
    index_path.write_text(json.dumps(index))
    before = FrozenBertEncoder.load(
        cpu_threads=1, local_model_path=directory, window_length=8, overlap=2
    )
    shard = next(nested.glob("*.safetensors"))
    state = load_file(shard)
    key = next(iter(state))
    state[key] = state[key] + 1
    save_file(state, shard, metadata={"format": "pt"})
    after = FrozenBertEncoder.load(
        cpu_threads=1, local_model_path=directory, window_length=8, overlap=2
    )
    assert not torch.equal(before.model.state_dict()[key], after.model.state_dict()[key])
    assert before.fingerprint.digest != after.fingerprint.digest
    assert set(index["weight_map"].values()) <= dict(after.fingerprint.artifacts).keys()
