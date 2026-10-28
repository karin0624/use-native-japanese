"""固定語彙の全資源を共有し、文書のLexeme状態と独立したVocabへ復元する。"""

import copy
import hashlib
import io
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from itertools import islice

import numpy as np
import spacy
import srsly
from spacy.attrs import NORM
from spacy.tokens import Doc
from spacy.vocab import Vocab

# Vocab.to_bytes()が含めないLexemeCの全可変属性。文字列属性は参照先も残す。
_STRING_ATTRIBUTES = ("lower_", "norm_", "shape_", "prefix_", "suffix_", "lang_")
_VECTOR_METADATA = (
    "name", "mode", "minn", "maxn", "hash_count", "hash_seed", "bow", "eow", "attr"
)


def _vector_metadata(vectors) -> tuple[tuple[str, object], ...]:
    return tuple((name, getattr(vectors, name)) for name in _VECTOR_METADATA)


def _read_vector_metadata(value) -> tuple[tuple[str, object], ...]:
    try:
        metadata = dict(value)
        if (
            set(metadata) != set(_VECTOR_METADATA)
            or (metadata["name"] is not None and not isinstance(metadata["name"], str))
            or metadata["mode"] not in ("default", "floret")
            or any(type(metadata[key]) is not int or metadata[key] < 0
                   for key in ("minn", "maxn", "hash_count", "hash_seed", "attr"))
            or any(not isinstance(metadata[key], str) for key in ("bow", "eow"))
        ):
            raise ValueError
        return tuple((name, metadata[name]) for name in _VECTOR_METADATA)
    except (TypeError, ValueError) as error:
        raise ValueError("ベクトルの公開metadataが不正です。") from error


def _restore_vector_metadata(vocab: Vocab, metadata: tuple[tuple[str, object], ...]) -> None:
    if _vector_metadata(vocab.vectors) == metadata:
        return
    config = dict(metadata)
    name = config.pop("name")
    # defaultモードではnative to_bytesがmode以外のcfgを省くため、公開値を補う。
    vocab.vectors.from_bytes(
        srsly.msgpack_dumps({"vectors.cfg": srsly.json_dumps(config)}),
        # spaCyのexcludeは先頭部分で照合するため、vectorsを除くとvectors.cfgも落ちる。
        exclude=("strings", "key2row"),
    )
    vocab.vectors.name = name


def _lexeme_state(lexeme) -> tuple:
    return (
        lexeme.text,
        lexeme.flags,
        lexeme.rank,
        *(getattr(lexeme, name) for name in _STRING_ATTRIBUTES),
    )


def _restore_lexemes(vocab: Vocab, states: tuple[tuple, ...]) -> None:
    for text, flags, rank, *strings in states:
        lexeme = vocab[text]
        for name, value in zip(_STRING_ATTRIBUTES, strings, strict=True):
            setattr(lexeme, name, value)
        lexeme.flags = flags
        lexeme.rank = rank


def _read_states(value) -> tuple[tuple, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("語彙のLexeme状態が不正です。")
    states = []
    for row in value:
        if (
            not isinstance(row, (list, tuple))
            or len(row) != 3 + len(_STRING_ATTRIBUTES)
            or not isinstance(row[0], str)
            or any(type(number) is not int or not 0 <= number < 2**64 for number in row[1:3])
            or any(not isinstance(text, str) for text in row[3:])
        ):
            raise ValueError("語彙のLexeme状態が不正です。")
        states.append(tuple(row))
    return tuple(states)


@lru_cache(maxsize=8)
def _language_vocab(language: str) -> Vocab:
    # 固定spaCy版の言語設定を使う。返すVocab自体は判定機へ渡さない。
    return spacy.blank(language).vocab if language else Vocab()


@dataclass(frozen=True)
class VocabularyResources:
    """モデルの全strings/vectors/lookupsと初期Lexemeを一個の不変値にする。"""

    language: str
    snapshot: bytes = field(repr=False)
    lexemes: tuple[tuple, ...] = field(repr=False)
    writing_system: tuple[tuple[str, object], ...]
    config: tuple[tuple[str, object], ...]
    morphology: tuple[str, ...] = field(default=(), repr=False)
    vector_metadata: tuple[tuple[str, object], ...] = field(default=(), repr=False)

    @cached_property
    def resource_id(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def to_bytes(self) -> bytes:
        return srsly.msgpack_dumps(
            {
                "language": self.language,
                "snapshot": self.snapshot,
                "lexemes": self.lexemes,
                "writing_system": self.writing_system,
                "config": self.config,
                "morphology": self.morphology,
                "vector_metadata": self.vector_metadata,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "VocabularyResources":
        value = srsly.msgpack_loads(data)
        if (
            not isinstance(value, dict)
            or set(value) != {
                "language", "snapshot", "lexemes", "writing_system", "config", "morphology",
                "vector_metadata",
            }
            or not isinstance(value["language"], str)
            or not isinstance(value["snapshot"], bytes)
            or not isinstance(value["morphology"], (list, tuple))
            or any(not isinstance(features, str) for features in value["morphology"])
        ):
            raise ValueError("共有語彙資源の形式が不正です。")
        try:
            return cls(
                value["language"],
                value["snapshot"],
                _read_states(value["lexemes"]),
                tuple(dict(value["writing_system"]).items()),
                tuple(dict(value["config"]).items()),
                tuple(value["morphology"]),
                _read_vector_metadata(value["vector_metadata"]),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("共有語彙資源の形式が不正です。") from error


@dataclass(frozen=True, eq=False)
class VocabularyGeneration:
    """固定基底または前世代から変わった実出力だけを保持する。"""

    resources: VocabularyResources = field(repr=False)
    parent: "VocabularyGeneration | None" = field(repr=False)
    snapshot: bytes = field(repr=False)
    generation_id: str = field(init=False)

    def __post_init__(self):
        if self.parent is not None and self.parent.resource_id != self.resource_id:
            raise ValueError("語彙世代の基底資源が親と一致しません。")
        # 親のIDは親の構築時に計算済みなので、長い世代列でも再帰しない。
        identity = (
            bytes.fromhex(self.resource_id)
            + (bytes.fromhex(self.parent_id) if self.parent_id else bytes(32))
            + hashlib.sha256(self.snapshot).digest()
        )
        object.__setattr__(self, "generation_id", hashlib.sha256(identity).hexdigest())

    @property
    def resource_id(self) -> str:
        return self.resources.resource_id

    @property
    def parent_id(self) -> str | None:
        return self.parent.generation_id if self.parent is not None else None

    def ancestors(self, known_ids: set[str] | None = None) -> tuple["VocabularyGeneration", ...]:
        """既知の親で止め、未保存の祖先から自身までを親の順で返す。"""
        pending, current = [], self
        while current is not None and (known_ids is None or current.generation_id not in known_ids):
            pending.append(current)
            current = current.parent
        return tuple(reversed(pending))

    def to_bytes(self) -> bytes:
        return srsly.msgpack_dumps(
            {
                "generation_id": self.generation_id,
                "resource_id": self.resource_id,
                "parent_id": self.parent_id,
                "snapshot": self.snapshot,
            }
        )

    @staticmethod
    def _read(data: bytes) -> dict:
        value = srsly.msgpack_loads(data)
        if (
            not isinstance(value, dict)
            or set(value) != {"generation_id", "resource_id", "parent_id", "snapshot"}
            or not isinstance(value["snapshot"], bytes)
            or any(
                not isinstance(value[name], str) or len(value[name]) != 64
                for name in ("generation_id", "resource_id")
            )
            or (
                value["parent_id"] is not None
                and (not isinstance(value["parent_id"], str) or len(value["parent_id"]) != 64)
            )
        ):
            raise ValueError("共有語彙世代の形式が不正です。")
        return value

    @classmethod
    def references(cls, data: bytes) -> tuple[str, str | None, str]:
        value = cls._read(data)
        return value["resource_id"], value["parent_id"], value["generation_id"]

    @classmethod
    def from_bytes(
        cls,
        resources: VocabularyResources,
        data: bytes,
        *,
        parent: "VocabularyGeneration | None" = None,
    ) -> "VocabularyGeneration":
        value = cls._read(data)
        if (
            value["resource_id"] != resources.resource_id
            or value["parent_id"] != (parent.generation_id if parent is not None else None)
        ):
            raise ValueError("共有語彙世代の基底または親が一致しません。")
        _read_changes(value["snapshot"])
        generation = cls(resources, parent, value["snapshot"])
        if generation.generation_id != value["generation_id"]:
            raise ValueError("共有語彙世代の内容とIDが一致しません。")
        return generation


@dataclass(frozen=True, eq=False)
class VocabularySnapshot:
    """共有資源への参照と、解析した文書のLexeme・lookup状態。"""

    resources: VocabularyResources
    lexemes: tuple[tuple, ...]
    lookups: bytes = field(repr=False)
    generation: VocabularyGeneration | None = field(default=None, repr=False)

    @property
    def generation_id(self) -> str | None:
        return self.generation.generation_id if self.generation is not None else None

    def __eq__(self, other):
        if not isinstance(other, VocabularySnapshot):
            return NotImplemented
        return (
            self.resources.resource_id == other.resources.resource_id
            and self.generation_id == other.generation_id
            and self.lexemes == other.lexemes
            and self.lookups == other.lookups
        )

    def __hash__(self):
        return hash((self.resources.resource_id, self.generation_id, self.lexemes, self.lookups))

    def to_bytes(self) -> bytes:
        # 共有資源の全体を文書ごとの保存値へ含めない。
        return srsly.msgpack_dumps(
            {
                "lexemes": self.lexemes,
                "lookups": self.lookups,
                "generation_id": self.generation_id,
            }
        )

    @classmethod
    def from_bytes(
        cls,
        resources: VocabularyResources,
        data: bytes,
        *,
        generation: VocabularyGeneration | None = None,
    ) -> "VocabularySnapshot":
        value = srsly.msgpack_loads(data)
        if (
            not isinstance(value, dict)
            or set(value) != {"lexemes", "lookups", "generation_id"}
            or not isinstance(value["lookups"], bytes)
        ):
            raise ValueError("文書の語彙状態が不正です。")
        if (
            value["generation_id"] != (generation.generation_id if generation else None)
            or (generation is not None and generation.resource_id != resources.resource_id)
        ):
            raise ValueError("文書が参照する語彙世代が一致しません。")
        return cls(resources, _read_states(value["lexemes"]), value["lookups"], generation)


def capture_vocabulary_resources(vocab: Vocab) -> VocabularyResources:
    """固定パイプラインの読込み時に一回呼び、公開形式で全資源を保持する。"""
    return VocabularyResources(
        vocab.lang,
        vocab.to_bytes(),
        tuple(_lexeme_state(lexeme) for lexeme in vocab),
        tuple(vocab.writing_system.items()),
        tuple(vocab.cfg.items()),
        _morphology_values(vocab),
        _vector_metadata(vocab.vectors),
    )


def snapshot_vocabulary(
    doc: Doc,
    resources: VocabularyResources | None = None,
    *,
    recorder: "VocabularyRecorder | None" = None,
) -> VocabularySnapshot:
    """元Docを変更せず、解析時点の累積語彙と文書のLexemeを保存する。"""
    if recorder is not None:
        if resources is not None and resources.resource_id != recorder.resources.resource_id:
            raise ValueError("語彙Recorderと基底資源が一致しません。")
        return recorder.capture(doc)
    if resources is None:
        resources = capture_vocabulary_resources(doc.vocab)
    else:
        return VocabularyRecorder(resources).capture(doc)
    return VocabularySnapshot(
        resources,
        tuple(_lexeme_state(doc.vocab[orth]) for orth in dict.fromkeys(t.orth for t in doc)),
        doc.vocab.lookups.to_bytes(),
    )


def _new_vocab(resources: VocabularyResources) -> Vocab:
    defaults = _language_vocab(resources.language)
    return Vocab(
        lex_attr_getters=copy.deepcopy(defaults.lex_attr_getters),
        get_noun_chunks=defaults.get_noun_chunks,
        writing_system=dict(resources.writing_system),
        vectors_name=dict(resources.vector_metadata).get("name"),
    )


def _restore_lookups(vocab: Vocab, data: bytes, resources: VocabularyResources) -> None:
    # Lexeme.norm/norm_ setterが復元中に追加したtableも、元の公開状態へ正確に戻す。
    vocab.lookups.from_bytes(data)
    getter = copy.deepcopy(_language_vocab(resources.language).lex_attr_getters.get(NORM))
    if getter is None:
        vocab.lex_attr_getters.pop(NORM, None)
    else:
        if "lexeme_norm" in vocab.lookups:
            getter = spacy.util.add_lookups(getter, vocab.lookups.get_table("lexeme_norm"))
        vocab.lex_attr_getters[NORM] = getter


def _restore_document_state(vocab: Vocab, snapshot: VocabularySnapshot) -> Vocab:
    resources = snapshot.resources
    vocab.cfg.update(dict(resources.config))
    # prob/sentiment/clusterはLookupsへ格納される。
    vocab.from_bytes(
        srsly.msgpack_dumps({"lookups": snapshot.lookups}), exclude=("strings", "vectors")
    )
    _restore_lexemes(vocab, resources.lexemes)
    for features in resources.morphology:
        vocab.morphology.add(features)
    return vocab


def restore_vocabulary(snapshot: VocabularySnapshot) -> Vocab:
    """全固定資源も含む可変Vocabを毎回独立して作る。"""
    vocab = _new_vocab(snapshot.resources).from_bytes(
        snapshot.resources.snapshot, exclude=("lookups",)
    )
    # spaCyのbytes復元は読み取り専用配列を返すため、局所的な値の変更も許す。
    vocab.vectors.data = vocab.vectors.data.copy()
    _restore_vector_metadata(vocab, snapshot.resources.vector_metadata)
    _restore_document_state(vocab, snapshot)
    if snapshot.generation is not None:
        for generation in snapshot.generation.ancestors():
            _apply_changes(vocab, generation.snapshot)
    _restore_lexemes(vocab, snapshot.lexemes)
    _restore_lookups(vocab, snapshot.lookups, snapshot.resources)
    return vocab


def restore_lexeme_view(snapshot: VocabularySnapshot) -> Vocab:
    """用法照合の局所view用。全語彙資源の復元にはrestore_vocabularyを使う。"""
    vocab = _restore_document_state(_new_vocab(snapshot.resources), snapshot)
    _restore_lexemes(vocab, snapshot.lexemes)
    _restore_lookups(vocab, snapshot.lookups, snapshot.resources)
    return vocab


def _array_bytes(array: np.ndarray) -> bytes:
    output = io.BytesIO()
    np.save(output, array, allow_pickle=False)
    return output.getvalue()


def _morphology_values(vocab: Vocab) -> tuple[str, ...]:
    # spaCy自身の公開pickle protocolからFEATS値だけを読む。pickleデータは作らない。
    return tuple(vocab.morphology.__reduce__()[1][1])


def _read_changes(data: bytes) -> dict:
    value = srsly.msgpack_loads(data)
    expected = {
        "strings", "shape", "dtype", "vector_keys", "vector_rows", "vector_values",
        "lexemes", "config", "writing_system", "morphology", "vector_metadata",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("共有語彙世代の差分形式が不正です。")
    try:
        shape = tuple(value["shape"])
        dtype = np.dtype(value["dtype"])
        keys = tuple(tuple(entry) for entry in value["vector_keys"])
        rows = tuple(value["vector_rows"])
        if (
            len(shape) != 2
            or any(type(size) is not int or size < 0 for size in shape)
            or dtype.hasobject
            or not isinstance(value["strings"], (list, tuple))
            or any(not isinstance(text, str) for text in value["strings"])
            or any(
                len(entry) != 2
                or type(entry[0]) is not int
                or not 0 <= entry[0] < 2**64
                or (entry[1] is not None and (type(entry[1]) is not int or not 0 <= entry[1] < shape[0]))
                for entry in keys
            )
            or any(type(row) is not int or not 0 <= row < shape[0] for row in rows)
            or not isinstance(value["vector_values"], bytes)
            or (not rows and value["vector_values"] != b"")
            or not isinstance(value["morphology"], (list, tuple))
            or any(not isinstance(features, str) for features in value["morphology"])
        ):
            raise ValueError
        array = (
            np.load(io.BytesIO(value["vector_values"]), allow_pickle=False)
            if rows else np.empty((0, shape[1]), dtype=dtype)
        )
        if (
            not isinstance(array, np.ndarray)
            or array.shape != (len(rows), shape[1])
            or array.dtype != dtype
        ):
            raise ValueError
        value["lexemes"] = _read_states(value["lexemes"])
        value["config"] = dict(value["config"])
        value["writing_system"] = dict(value["writing_system"])
        value["vector_metadata"] = _read_vector_metadata(value["vector_metadata"])
        value["shape"], value["dtype"] = shape, dtype
        value["vector_keys"], value["vector_rows"], value["array"] = keys, rows, array
    except (TypeError, ValueError, KeyError, OSError, EOFError) as error:
        raise ValueError("共有語彙世代の差分形式が不正です。") from error
    return value


def _apply_changes(vocab: Vocab, data: bytes) -> None:
    changes = _read_changes(data)
    for text in changes["strings"]:
        vocab.strings.add(text)
    vectors = vocab.vectors
    _restore_vector_metadata(vocab, changes["vector_metadata"])
    if vectors.data.dtype != changes["dtype"]:
        vectors.data = vectors.data.astype(changes["dtype"])
    if vectors.shape != changes["shape"]:
        vectors.resize(changes["shape"], inplace=True)
    for key, row in changes["vector_keys"]:
        if row is None:
            vectors.key2row.pop(key, None)
        else:
            vectors.add(key, row=row)
    if changes["vector_rows"]:
        vectors.data[list(changes["vector_rows"])] = changes["array"]
    vocab.cfg.clear()
    vocab.cfg.update(changes["config"])
    vocab.writing_system.clear()
    vocab.writing_system.update(changes["writing_system"])
    _restore_lexemes(vocab, changes["lexemes"])
    for features in changes["morphology"]:
        vocab.morphology.add(features)


def _lexeme_signature(lexeme) -> tuple[int, ...]:
    return (
        lexeme.flags, lexeme.rank, lexeme.lower, lexeme.norm, lexeme.shape,
        lexeme.prefix, lexeme.suffix, lexeme.lang,
    )


class VocabularyRecorder:
    """Parser所有の作業状態から、各時点の不変な共有差分世代を作る。"""

    def __init__(self, resources: VocabularyResources):
        self.resources = resources
        self.generation: VocabularyGeneration | None = None
        self._previous = _new_vocab(resources).from_bytes(resources.snapshot)
        self._previous.vectors.data = self._previous.vectors.data.copy()
        _restore_vector_metadata(self._previous, resources.vector_metadata)
        lookups = self._previous.lookups.to_bytes()
        self._previous.cfg.update(dict(resources.config))
        _restore_lexemes(self._previous, resources.lexemes)
        for features in resources.morphology:
            self._previous.morphology.add(features)
        _restore_lookups(self._previous, lookups, resources)
        self._lexemes = {lexeme.orth: _lexeme_signature(lexeme) for lexeme in self._previous}
        self._morphology = set(resources.morphology)
        self._source: Vocab | None = None

    def capture(self, doc: Doc) -> VocabularySnapshot:
        self._capture_source(doc.vocab)
        return self._document_snapshot(doc)

    def capture_many(self, docs) -> tuple[VocabularySnapshot, ...]:
        """同じVocabを共有する処理済みDoc群の時点を、一回だけ記録する。"""
        docs = tuple(docs)
        if not docs:
            return ()
        source = docs[0].vocab
        if any(doc.vocab is not source for doc in docs):
            raise ValueError("まとめて記録するDocのVocabが一致しません。")
        self._capture_source(source)
        return tuple(self._document_snapshot(doc) for doc in docs)

    def _capture_source(self, source: Vocab) -> None:
        previous = self._previous
        if self._source is not None and source is not self._source:
            raise ValueError("同じ語彙Recorderで別の解析器のVocabは追跡できません。")
        self._source = source
        if len(source.strings) < len(previous.strings):
            raise ValueError("固定解析器のStringStoreから文字列が除去されています。")
        # 固定spaCyのStringStore.addは末尾へ追加する。解析済みの他文書に由来する追加分も全て読む。
        strings = (
            tuple(islice(source.strings, len(previous.strings), None))
            if len(source.strings) != len(previous.strings) else ()
        )
        current_vectors, old_vectors = source.vectors, previous.vectors
        keys = []
        if current_vectors.key2row != old_vectors.key2row:
            keys.extend(
                (key, row) for key, row in current_vectors.key2row.items()
                if old_vectors.key2row.get(key) != row
            )
            keys.extend((key, None) for key in old_vectors.key2row if key not in current_vectors.key2row)
        current, old = current_vectors.data, old_vectors.data
        if current.dtype != old.dtype or current.shape[1] != old.shape[1]:
            rows = np.arange(current.shape[0])
        else:
            common = min(current.shape[0], old.shape[0])
            width = current.shape[1] * current.dtype.itemsize
            changed = np.any(
                current[:common].view("u1").reshape(common, width)
                != old[:common].view("u1").reshape(common, width), axis=1,
            )
            rows = np.flatnonzero(changed)
            if current.shape[0] > common:
                added = np.flatnonzero(np.any(
                    current[common:].view("u1").reshape(current.shape[0] - common, width), axis=1
                )) + common
                rows = np.concatenate((rows, added))
        lexemes = []
        for lexeme in source:
            signature = _lexeme_signature(lexeme)
            if self._lexemes.get(lexeme.orth) != signature:
                lexemes.append(_lexeme_state(lexeme))
                self._lexemes[lexeme.orth] = signature
        all_morphology = set(_morphology_values(source))
        if not self._morphology <= all_morphology:
            raise ValueError("固定解析器のMorphologyから形態情報が除去されています。")
        morphology = tuple(sorted(all_morphology - self._morphology))
        if (
            strings or keys or len(rows) or lexemes or morphology or current.shape != old.shape
            or current.dtype != old.dtype or source.cfg != previous.cfg
            or source.writing_system != previous.writing_system
            or _vector_metadata(current_vectors) != _vector_metadata(old_vectors)
        ):
            changes = srsly.msgpack_dumps({
                "strings": strings,
                "shape": current.shape,
                "dtype": current.dtype.str,
                "vector_keys": keys,
                "vector_rows": tuple(int(row) for row in rows),
                "vector_values": _array_bytes(current[rows]) if len(rows) else b"",
                "lexemes": tuple(lexemes),
                "config": tuple(source.cfg.items()),
                "writing_system": tuple(source.writing_system.items()),
                "morphology": morphology,
                "vector_metadata": _vector_metadata(current_vectors),
            })
            self.generation = VocabularyGeneration(self.resources, self.generation, changes)
            _apply_changes(previous, changes)
        _restore_lookups(previous, source.lookups.to_bytes(), self.resources)
        self._morphology = all_morphology

    def _document_snapshot(self, doc: Doc) -> VocabularySnapshot:
        source = doc.vocab
        return VocabularySnapshot(
            self.resources,
            tuple(_lexeme_state(source[orth]) for orth in dict.fromkeys(t.orth for t in doc)),
            source.lookups.to_bytes(),
            self.generation,
        )
