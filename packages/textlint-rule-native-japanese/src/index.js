import { StringSource } from "textlint-util-to-string";
import { configuration, getWorker } from "./worker.js";

// ASTの葉を使う。装飾文字の推測やMarkdownの再解析は行わない。
export function plainSegments(node) {
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
            // Code、Image、Html、未知の拡張ノードは評価対象を分離する。
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
    const segments = [];
    const { Syntax, report, RuleError, locator } = context;
    const collect = node => segments.push(...plainSegments(node));
    return {
        [Syntax.Paragraph]: collect,
        [Syntax.Header]: collect,
        async [`${Syntax.Document}:exit`]() {
            if (!segments.length) return;
            const results = await getWorker(config).request(segments.map(segment => segment.text));
            for (let i = 0; i < results.length; i++) {
                const segment = segments[i];
                if (results[i]?.offsetEncoding !== "utf-16" || !Array.isArray(results[i]?.diagnostics)) {
                    throw new Error("native-japanese: 応答にはoffsetEncoding: utf-16とdiagnosticsが必要です。");
                }
                for (const diagnostic of results[i].diagnostics) {
                    const range = diagnostic?.range;
                    if (!Array.isArray(range) || range.length !== 2 ||
                        !range.every(Number.isSafeInteger) || range[0] < 0 || range[0] >= range[1] ||
                        range[1] > segment.text.length || range.some(index => splitsSurrogate(segment.text, index))) {
                        throw new Error("native-japanese: 指摘のUTF-16範囲が不正です。");
                    }
                    const start = segment.source.originalIndexFromIndex(range[0]);
                    const end = segment.source.originalIndexFromIndex(range[1], true);
                    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= end ||
                        start < 0 || end > segment.node.raw.length) continue;
                    if (typeof diagnostic.message !== "string" || !diagnostic.message.trim()) {
                        throw new Error("native-japanese: 指摘のmessageが不正です。");
                    }
                    if (diagnostic.suggestions !== undefined && (!Array.isArray(diagnostic.suggestions) ||
                        diagnostic.suggestions.some(value => typeof value !== "string" || !value.trim()))) {
                        throw new Error("native-japanese: 指摘のsuggestionsには訂正候補の文字列配列が必要です。");
                    }
                    // 訂正候補は案内だけにする。装飾を跨ぐ原文範囲を自動置換しない。
                    const suggestions = diagnostic.suggestions?.length
                        ? ` 訂正候補: ${diagnostic.suggestions.map(value => JSON.stringify(value)).join("、")}` : "";
                    report(segment.node, new RuleError(diagnostic.message + suggestions, { padding: locator.range([start, end]) }));
                }
            }
        }
    };
}
