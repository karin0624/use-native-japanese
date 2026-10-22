"""既存Juman++・KNPの公開関係を、原文位置と照会キーへ接続する。"""

import math
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from cohesion_tools.extractors import PasExtractor
from rhoknp import KNP, Document, Jumanpp, RegexSenter, Sentence

from .models import Analysis, Endpoint, Query, Relation, Unavailable


class LiteralSenter(RegexSenter):
    """既存の文分割を使い、本文をコメントとして再解釈しない。"""

    def apply_to_document(self, document: Document | str, timeout: int = 10) -> Document:
        if isinstance(document, str):
            document = Document(text=document)
        result = Document()
        result.sentences = [Sentence(text=text) for text in self._split_document(document.text)]
        return result


def position_map(analyzed: str, original: str) -> tuple[int, ...]:
    """既検証の空白差だけの位置写像。非空白の欠落や順序変更は拒否する。"""
    positions = []
    cursor = 0
    for start, target, size in SequenceMatcher(
        None, analyzed, original, autojunk=False
    ).get_matching_blocks():
        if start != len(positions) or target < cursor:
            raise ValueError("解析文字の全被覆または順序を保持できません。")
        if any(not character.isspace() for character in original[cursor:target]):
            raise ValueError("原文の非空白文字が解析結果と対応しません。")
        positions.extend(range(target, target + size))
        cursor = target + size
    if len(positions) != len(analyzed) or "".join(original[i] for i in positions) != analyzed:
        raise ValueError("解析文字と原文位置が一致しません。")
    return tuple(positions)


def original_ranges(span: tuple[int, int], mapping: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    start, end = span
    if not 0 <= start <= end <= len(mapping):
        raise ValueError("解析範囲が本文の範囲外です。")
    ranges = []
    for position in mapping[start:end]:
        if ranges and ranges[-1][1] == position:
            ranges[-1] = (ranges[-1][0], position + 1)
        else:
            ranges.append((position, position + 1))
    return tuple(ranges)


def predicate_candidates(features: dict) -> tuple[str, ...]:
    """確認済みF・条件N/H・NPAのCだけを同じ基本句から読む。"""
    keys = []

    def add(value):
        if not isinstance(value, str) or not value:
            raise ValueError("公開された述語候補の形式が不正です。")
        if value not in keys:
            keys.append(value)

    full = features.get("用言代表表記")
    canonical = features.get("正規化代表表記")
    normalized = features.get("標準用言代表表記")
    head = features.get("非CV用言見出候補")
    if isinstance(full, str) and full:
        for key in full.split("?"):
            add(key)
    if (
        isinstance(normalized, str)
        and normalized
        and normalized == canonical
        and "態" not in features
    ):
        add(normalized)
    if "非用言格解析" in features and isinstance(canonical, str) and canonical:
        add(canonical)
    if isinstance(head, str) and head and head == canonical and "態" not in features:
        for key in head.split("?"):
            add(key)
    return tuple(keys)


def argument_representation(features: dict) -> tuple[str | None, str | None]:
    for name in ("主辞’代表表記", "主辞代表表記", "正規化代表表記"):
        if name in features:
            value = features[name]
            if not isinstance(value, str):
                raise ValueError("公開された元項の表記が文字列ではありません。")
            return name, value
    return None, None


def sentence_state(comment: str) -> str:
    if "ERROR:" in comment:
        raise ValueError("nativeの文headerにERRORがあります。")
    warning = re.search(r"(?:^|\s)WARNING:(.*)$", comment)
    if warning is None:
        return "case_available"
    if warning.group(1) != "Fell back to dependency analysis":
        raise ValueError("nativeに未対応のWARNINGがあります。")
    return "case_unavailable"


def adapt_sentence(sentence: Sentence, original: str, mapping: tuple[int, ...]):
    """rhoknp/cohesion-toolsの公開PASを読む。語彙・格を補完しない。"""

    def endpoint(bp):
        morphemes = bp.morphemes
        ranges = original_ranges((morphemes[0].span[0], morphemes[-1].span[1]), mapping)
        if "".join(original[start:end] for start, end in ranges) != bp.text:
            raise ValueError("公開端点の表層が原文位置と一致しません。")
        return Endpoint(bp.text, ranges)

    if sentence_state(sentence.misc_comment) == "case_unavailable":
        ranges = ((mapping[0], mapping[-1] + 1),) if mapping else ()
        return (), (Unavailable(ranges, "KNPが係り受け解析へfallbackし、格関係を取得できません。"),)

    relations, unavailable = [], []
    for bp in sentence.base_phrases:
        target = PasExtractor.is_pas_target(bp, verbal=True, nominal=True)
        arguments = bp.pas.get_all_arguments(relax=False)
        if bp.features.get("格解析結果") and bp.pas.predicate.cfid is None:
            raise ValueError("公開された格解析結果をrhoknpが読み取れません。")
        if target and not any(arguments.values()):
            unavailable.append(
                Unavailable(endpoint(bp).ranges, "公開された格・項の関係がありません。")
            )
        keys = predicate_candidates(bp.features)
        predicate_type = bp.features.get("用言", bp.features.get("非用言格解析"))
        representations = tuple(
            (name, bp.features[name])
            for name in (
                "用言代表表記",
                "正規化代表表記",
                "標準用言代表表記",
                "非CV用言見出候補",
                "態",
            )
            if isinstance(bp.features.get(name), str)
        )
        for case, values in arguments.items():
            for argument in values:
                arg_bp = getattr(argument, "base_phrase", None)
                if arg_bp is not None and arg_bp.sentence.sid != sentence.sid:
                    raise ValueError("別の文の元項へ同じ文の位置を付けられません。")
                field, noun = (
                    argument_representation(arg_bp.features) if arg_bp is not None else (None, None)
                )
                reasons = []
                if not target:
                    reasons.append("公開PASの対象ではありません。")
                if arg_bp is None:
                    reasons.append("原文内の元項がありません。")
                elif not noun:
                    reasons.append("元項の公開表記がありません。")
                if not keys:
                    reasons.append("原語の照会候補がありません。")
                if not isinstance(predicate_type, str) or not predicate_type:
                    reasons.append("公開された述語型がありません。")
                queries = (
                    tuple(Query(key, predicate_type, case, noun) for key in keys)
                    if not reasons
                    else ()
                )
                source = representations + ((("項:" + field, noun),) if field is not None else ())
                relations.append(
                    Relation(
                        endpoint(bp),
                        endpoint(arg_bp) if arg_bp is not None else None,
                        queries,
                        tuple(reasons),
                        source,
                    )
                )
    return tuple(relations), tuple(unavailable)


class Parser:
    """一組の既存native processorを保持する。呼出しは直列で行う。"""

    def __init__(self, runtime: dict, timeout: float = 30.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("解析の期限には正の有限秒数を指定してください。")
        self.timeout = timeout
        self.senter = LiteralSenter()
        prefix = Path(runtime["prefix"])
        self.jumanpp = Jumanpp(executable=str(prefix / "bin/jumanpp"), senter=self.senter)
        self.knp = KNP(
            executable=str(prefix / "bin/knp"),
            options=list(runtime["options"]),
            jumanpp=self.jumanpp,
            senter=self.senter,
        )
        if not self.jumanpp.is_available() or not self.knp.is_available():
            raise RuntimeError("Juman++またはKNPを起動できません。")

    def analyze(self, text: str) -> Analysis:
        if self.jumanpp is None or self.knp is None:
            raise RuntimeError("解析器は終了しています。")
        deadline = time.monotonic() + self.timeout

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("解析の期限を超えました。")
            return seconds

        prepared = self.senter.apply_to_document(Document(text=text))
        source_map = position_map(prepared.text, text)
        relations, unavailable, offset = [], [], 0
        for index, sentence in enumerate(prepared.sentences):
            sentence.sid = f"native-{index}"
            morph = self.jumanpp.apply_to_sentence(sentence, timeout=remaining())
            local_map = position_map(morph.text, sentence.text)
            mapping = tuple(source_map[offset + position] for position in local_map)
            native = self.knp.apply_to_sentence(morph, timeout=remaining())
            if native.sid != morph.sid or [(m.text, m.span) for m in native.morphemes] != [
                (m.text, m.span) for m in morph.morphemes
            ]:
                raise ValueError("nativeと入力の文ID・形態素表層・順序・位置が一致しません。")
            rows, missing = adapt_sentence(native, text, mapping)
            relations.extend(rows)
            unavailable.extend(missing)
            offset += len(sentence.text)
        return Analysis(text, tuple(relations), tuple(unavailable))

    def close(self):
        """所有参照を解放し、既存processorの終了処理へ委ねる。"""
        self.knp = None
        self.jumanpp = None
