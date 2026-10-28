import { decodeString } from "micromark-util-decode-string";
import { extname } from "node:path";
import { parse, preprocess, postprocess } from "micromark";
import { configuration, getWorker } from "./worker.js";

// 文書全体を解析し、引用・リストの継続行もparserの位置情報で扱う。
function inlineSources(text) {
    const events = postprocess(parse().document().write(preprocess()(text, undefined, true)));
    const spans = new Map();
    const tokens = [];
    let current;
    let reference;
    for (const [kind, token, context] of events) {
        const range = [token.start.offset, token.end.offset];
        const raw = text.slice(...range);
        if (kind === "enter" && ["data", "lineEnding", "characterReference", "characterEscape", "autolinkProtocol", "autolinkEmail"].includes(token.type)) {
            const atomic = ["characterReference", "characterEscape"].includes(token.type);
            const value = atomic ? decodeString(raw) : context.sliceSerialize(token);
            const leaf = { range, raw, value, atomic };
            // textlint 15のMarkdown parserは補助平面の数値参照をfromCharCodeで復号する。
            // 比較用の値だけを互換化し、解析機へは正しいUnicode本文を渡す。
            if (token.type === "characterReference" && raw.startsWith("&#") && value.length === 2 &&
                value.codePointAt(0) > 0xFFFF) {
                leaf.astValue = String.fromCharCode(value.codePointAt(0));
            }
            tokens.push(leaf);
            if (token.type === "characterReference") reference = leaf;
        }
        if (kind === "enter" && token.type === "characterReferenceValue" && reference) {
            // 同parserのStr終端は末尾の参照値までで、終端の';'を含まない。
            reference.astEnd = token.end.offset;
        }
        if (kind === "exit" && token.type === "characterReference") reference = undefined;
        if (kind === "enter" && token.type === "codeText") {
            current = { raw, children: [] };
            spans.set(`${range[0]}:${range[1]}`, current);
        } else if (kind === "exit" && token.type === "codeText") {
            current = undefined;
        } else if (current && kind === "enter" && ["codeTextData", "lineEnding"].includes(token.type)) {
            current.children.push({ range, raw, value: context.sliceSerialize(token) });
        }
    }
    return { spans, tokens };
}

// 各UTF-16コード単位に原文範囲を保持する。文字参照内の部分範囲も参照全体へ戻す。
function segmentFromLeaves(node, leaves) {
    const starts = [];
    const ends = [];
    const text = leaves.map(leaf => leaf.value).join("");
    for (const leaf of leaves) {
        const start = leaf.range[0] - node.range[0];
        const end = leaf.range[1] - node.range[0];
        if (start < 0 || end > node.raw.length || start >= end ||
            node.raw.slice(start, end) !== leaf.raw ||
            (!leaf.atomic && end - start !== leaf.value.length)) {
            throw new Error("native-japanese: 本文の解析結果が原文と一致しないため、指摘位置を特定できません。");
        }
        for (let i = 0; i < leaf.value.length; i++) {
            starts.push(leaf.atomic ? start : start + i);
            ends.push(leaf.atomic ? end : start + i + 1);
        }
    }
    return { node, text, source: {
        originalIndexFromIndex: (index, isEnd = false) => isEnd ? ends[index - 1] : starts[index]
    } };
}

// ASTの葉とparserが返したtokenを照合し、Markdownの装飾位置を推測しない。
export function plainSegments(node, codeSpans, tokens = []) {
    const segments = [];
    let leaves = [];
    const flush = () => {
        if (leaves.length) {
            const segment = segmentFromLeaves(node, leaves);
            if (segment.text.trim()) segments.push(segment);
        }
        leaves = [];
    };
    const visit = child => {
        if (["Strong", "Emphasis", "Delete", "Link", "LinkReference"].includes(child.type)) {
            child.children?.forEach(visit);
        } else if (child.type === "Code") {
            const [start, end] = child.range;
            const span = codeSpans.get(`${start}:${end}`);
            if (!span || span.raw !== child.raw ||
                node.raw.slice(start - node.range[0], end - node.range[0]) !== span.raw ||
                span.children.map(leaf => leaf.value).join("") !== child.value) {
                throw new Error("native-japanese: インラインコードの解析結果が原文と一致しないため、指摘位置を特定できません。");
            }
            leaves.push(...span.children);
        } else if (child.type === "Str" || child.type === "Break") {
            const [start, end] = child.range;
            const parts = tokens.flatMap(leaf => {
                if (leaf.range[0] >= start && (leaf.range[1] <= end || leaf.astEnd === end)) return [leaf];
                // GFMのASTが通常dataを分割する場合は、両parserの原文範囲の交差だけを採る。
                // 文字参照・escapeは不可分とし、復号後の長さから位置を推定しない。
                if (!leaf.atomic && leaf.raw === leaf.value && leaf.range[1] - leaf.range[0] === leaf.value.length &&
                    leaf.range[0] < end && leaf.range[1] > start) {
                    const range = [Math.max(start, leaf.range[0]), Math.min(end, leaf.range[1])];
                    const value = leaf.value.slice(range[0] - leaf.range[0], range[1] - leaf.range[0]);
                    return [{ ...leaf, range, raw: value, value }];
                }
                return [];
            });
            const value = parts.map(leaf => leaf.value).join("");
            const astValue = parts.map(leaf => leaf.astValue ?? leaf.value).join("");
            if (!parts.length || parts[0].range[0] < start ||
                (parts.at(-1).range[1] !== end && parts.at(-1).astEnd !== end) ||
                child.raw !== node.raw.slice(start - node.range[0], end - node.range[0]) ||
                (child.type === "Str" && (parts[0].range[0] !== start ||
                    (value !== child.value && astValue !== child.value))) ||
                (child.type === "Break" && (parts.length !== 1 || !/^[\r\n]+$/u.test(value)))) {
                throw new Error("native-japanese: 本文の解析結果が原文と一致しないため、指摘位置を特定できません。");
            }
            leaves.push(...parts);
        } else {
            // Image、Html、未知の拡張ノードは評価対象を分離する。
            flush();
        }
    };
    node.children?.forEach(visit);
    flush();
    return segments;
}

// text parserが行単位に分けたParagraphを、空行までの本文へ戻す。
function textParagraphs(document) {
    const nodes = [];
    const children = document.children;
    for (let i = 0; i < children.length; i++) {
        const first = children[i];
        if (first.type !== "Paragraph") continue;
        let last = first;
        const leaves = [...first.children];
        while (children[i + 1]?.type === "Break" && children[i + 2]?.type === "Paragraph") {
            leaves.push(children[i + 1], ...children[i + 2].children);
            last = children[i + 2];
            i += 2;
        }
        nodes.push({ ...first, range: [first.range[0], last.range[1]],
            raw: document.raw.slice(first.range[0], last.range[1]),
            loc: { start: first.loc.start, end: last.loc.end }, children: leaves });
    }
    return nodes;
}

function splitsSurrogate(text, index) {
    return index > 0 && index < text.length && /[\uD800-\uDBFF]/u.test(text[index - 1]) &&
        /[\uDC00-\uDFFF]/u.test(text[index]);
}

function usage(context, options = {}) {
    const config = configuration(options);
    const nodes = [];
    const { Syntax, report, RuleError, locator } = context;
    const collect = node => nodes.push(node);
    return {
        [Syntax.Paragraph]: collect,
        [Syntax.Header]: collect,
        async [`${Syntax.Document}:exit`](document) {
            if (!nodes.length) return;
            const isText = [".txt", ".text"].includes(extname(context.getFilePath?.() ?? ""));
            let segments;
            if (isText) {
                // 通常テキストはMarkdown parserへ渡さず、全ての記号をそのまま解析する。
                segments = textParagraphs(document).filter(node => node.raw.trim()).map(node =>
                    segmentFromLeaves(node, [{ range: node.range, raw: node.raw, value: node.raw }]));
            } else {
                const { spans, tokens } = inlineSources(document.raw);
                segments = nodes.flatMap(node => plainSegments(node, spans, tokens));
            }
            const results = segments.length
                ? await getWorker(config).request(segments.map(segment => segment.text)) : [];
            for (let i = 0; i < results.length; i++) {
                const segment = segments[i];
                if (results[i]?.offsetEncoding !== "utf-16" || !Array.isArray(results[i]?.diagnostics)) {
                    throw new Error("native-japanese: 解析処理の応答でoffsetEncodingがutf-16でないか、diagnosticsが指摘の配列になっていません。");
                }
                const reportRange = (range, message) => {
                    if (!Array.isArray(range) || range.length !== 2 ||
                        !range.every(Number.isSafeInteger) || range[0] < 0 || range[0] >= range[1] ||
                        range[1] > segment.text.length || range.some(index => splitsSurrogate(segment.text, index))) {
                        throw new Error("native-japanese: 指摘のUTF-16範囲を読み取れないか、範囲が本文または文字の境界に合っていません。");
                    }
                    const start = segment.source.originalIndexFromIndex(range[0]);
                    const end = segment.source.originalIndexFromIndex(range[1], true);
                    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= end ||
                        start < 0 || end > segment.node.raw.length) {
                        throw new Error("native-japanese: 指摘の位置を原文の範囲に対応付けられません。");
                    }
                    report(segment.node, new RuleError(message, { padding: locator.range([start, end]) }));
                };
                for (const diagnostic of results[i].diagnostics) {
                    if (typeof diagnostic?.message !== "string" || !diagnostic.message.trim()) {
                        throw new Error("native-japanese: 指摘の説明文（message）が空か、文字列ではありません。");
                    }
                    if (diagnostic.suggestions !== undefined && (!Array.isArray(diagnostic.suggestions) ||
                        diagnostic.suggestions.some(value => typeof value !== "string" || !value.trim()))) {
                        throw new Error("native-japanese: 指摘のsuggestionsには、空でない文字列を並べた訂正候補の配列が必要です。");
                    }
                    // 訂正候補は案内だけにする。装飾を跨ぐ原文範囲を自動置換しない。
                    const suggestions = diagnostic.suggestions?.length
                        ? ` 訂正候補: ${diagnostic.suggestions.map(value => JSON.stringify(value)).join("、")}` : "";
                    reportRange(diagnostic.range, diagnostic.message + suggestions);
                }
            }
        }
    };
}

export default {
    rules: {
        usage
    },
    rulesConfig: {
        usage: { severity: "error" }
    }
};
