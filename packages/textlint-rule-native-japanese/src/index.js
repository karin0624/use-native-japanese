import { StringSource } from "textlint-util-to-string";
import { parse, preprocess, postprocess } from "micromark";
import { configuration, getWorker } from "./worker.js";

// 文書全体を解析し、引用・リストの継続行も既存parserの位置情報で扱う。
function inlineCodeSpans(text) {
    const events = postprocess(parse().document().write(preprocess()(text, undefined, true)));
    const spans = new Map();
    let current;
    for (const [kind, token, context] of events) {
        if (kind === "enter" && token.type === "codeText") {
            current = { raw: text.slice(token.start.offset, token.end.offset), children: [] };
            spans.set(`${token.start.offset}:${token.end.offset}`, current);
        } else if (kind === "exit" && token.type === "codeText") {
            current = undefined;
        } else if (current && kind === "enter" && ["codeTextData", "lineEnding"].includes(token.type)) {
            const range = [token.start.offset, token.end.offset];
            current.children.push({ type: "Str", range, raw: text.slice(...range), value: context.sliceSerialize(token) });
        }
    }
    return spans;
}

// ASTの葉とparserが返したtokenだけを接続し、Markdownの装飾位置を推測しない。
export function plainSegments(node, codeSpans) {
    const segments = [];
    let leaves = [];
    const flush = () => {
        if (leaves.length) {
            // StringSourceの親要素による装飾位置の推定を避け、元のStr範囲を保持する。
            const source = new StringSource({ ...node, type: "Paragraph", children: leaves });
            const text = source.toString();
            if (text.trim()) segments.push({ node, source, text });
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
                span.children.map(leaf => leaf.value).join("") !== child.value ||
                span.children.some(leaf => leaf.raw !== leaf.value || leaf.range[0] < start ||
                    leaf.range[1] > end || leaf.range[1] - leaf.range[0] !== leaf.value.length)) {
                throw new Error("native-japanese: inline codeのASTと原文tokenが一致しません。");
            }
            leaves.push(...span.children);
        } else if (child.type === "Str" || child.type === "Break") {
            const value = child.type === "Break" ? child.raw : child.value;
            const [start, end] = child.range;
            // entity/escapeなど、復元できない葉は境界として扱い、前後を連結しない。
            if (typeof value !== "string" || child.raw !== value || end - start !== value.length ||
                node.raw.slice(start - node.range[0], end - node.range[0]) !== value) {
                flush();
            } else {
                leaves.push({ ...child, type: "Str", value });
            }
        } else {
            // Image、Html、未知の拡張ノードは評価対象を分離する。
            flush();
        }
    };
    node.children?.forEach(visit);
    flush();
    return segments;
}

function splitsSurrogate(text, index) {
    return index > 0 && index < text.length && /[\uD800-\uDBFF]/u.test(text[index - 1]) &&
        /[\uDC00-\uDFFF]/u.test(text[index]);
}

export default function rule(context, options = {}) {
    const config = configuration(options);
    const nodes = [];
    const { Syntax, report, RuleError, locator } = context;
    const collect = node => nodes.push(node);
    return {
        [Syntax.Paragraph]: collect,
        [Syntax.Header]: collect,
        async [`${Syntax.Document}:exit`](document) {
            if (!nodes.length) return;
            const codeSpans = inlineCodeSpans(document.raw);
            const segments = nodes.flatMap(node => plainSegments(node, codeSpans));
            if (!segments.length) return;
            const results = await getWorker(config).request(segments.map(segment => segment.text));
            for (let i = 0; i < results.length; i++) {
                const segment = segments[i];
                if (results[i]?.offsetEncoding !== "utf-16" || !Array.isArray(results[i]?.diagnostics)) {
                    throw new Error("native-japanese: 応答にはoffsetEncoding: utf-16とdiagnosticsが必要です。");
                }
                const reportRange = (range, message) => {
                    if (!Array.isArray(range) || range.length !== 2 ||
                        !range.every(Number.isSafeInteger) || range[0] < 0 || range[0] >= range[1] ||
                        range[1] > segment.text.length || range.some(index => splitsSurrogate(segment.text, index))) {
                        throw new Error("native-japanese: 指摘のUTF-16範囲が不正です。");
                    }
                    const start = segment.source.originalIndexFromIndex(range[0]);
                    const end = segment.source.originalIndexFromIndex(range[1], true);
                    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= end ||
                        start < 0 || end > segment.node.raw.length) {
                        throw new Error("native-japanese: 指摘の原文範囲への写像が不正です。");
                    }
                    report(segment.node, new RuleError(message, { padding: locator.range([start, end]) }));
                };
                for (const diagnostic of results[i].diagnostics) {
                    if (typeof diagnostic?.message !== "string" || !diagnostic.message.trim()) {
                        throw new Error("native-japanese: 指摘のmessageが不正です。");
                    }
                    if (diagnostic.suggestions !== undefined && (!Array.isArray(diagnostic.suggestions) ||
                        diagnostic.suggestions.some(value => typeof value !== "string" || !value.trim()))) {
                        throw new Error("native-japanese: 指摘のsuggestionsには訂正候補の文字列配列が必要です。");
                    }
                    // 訂正候補は案内だけにする。装飾を跨ぐ原文範囲を自動置換しない。
                    const suggestions = diagnostic.suggestions?.length
                        ? ` 訂正候補: ${diagnostic.suggestions.map(value => JSON.stringify(value)).join("、")}` : "";
                    reportRange(diagnostic.range, diagnostic.message + suggestions);
                }
                const unavailable = results[i].unavailable;
                if (unavailable !== undefined && !Array.isArray(unavailable)) {
                    throw new Error("native-japanese: unavailableには配列が必要です。");
                }
                for (const item of unavailable ?? []) {
                    if (!["analysis", "reference"].includes(item?.kind) ||
                        typeof item.reason !== "string" || !item.reason.trim() ||
                        !Array.isArray(item.ranges) || !item.ranges.length) {
                        throw new Error("native-japanese: unavailableのkind・reason・rangesが不正です。");
                    }
                    const label = item.kind === "analysis" ? "判定保留（解析情報）" : "判定保留（比較不能）";
                    for (const range of item.ranges) reportRange(range, `${label}: ${item.reason}`);
                }
            }
        }
    };
}
