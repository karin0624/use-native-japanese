import { extname } from "node:path";
import { Processor } from "./markdown.js";
import { configuration, getWorker } from "./worker.js";

// text parserが行単位に分けたParagraphを、空行までの本文へ戻す。
function textParagraphs(document) {
    const units = [], children = document.children;
    for (let i = 0; i < children.length; i++) {
        const first = children[i];
        if (first.type !== "Paragraph") continue;
        let end = first.range[1];
        while (children[i + 1]?.type === "Break" && children[i + 2]?.type === "Paragraph") {
            end = children[i + 2].range[1];
            i += 2;
        }
        const text = document.raw.slice(first.range[0], end);
        if (text.trim()) units.push({ text, offset: first.range[0] });
    }
    return units;
}

function splitsSurrogate(text, index) {
    return index > 0 && index < text.length && /[\uD800-\uDBFF]/u.test(text[index - 1]) &&
        /[\uDC00-\uDFFF]/u.test(text[index]);
}

function usage(context, options = {}) {
    const config = configuration(options);
    const { Syntax, report, RuleError, locator } = context;
    return {
        async [`${Syntax.Document}:exit`](document) {
            const isText = [".txt", ".text"].includes(extname(context.getFilePath?.() ?? ""));
            if (!isText && !document.nativeJapanese) {
                throw new Error("native-japanese: Markdownの検査には --plugin textlint-rule-preset-native-japanese を指定してください。");
            }
            const segments = isText ? textParagraphs(document) : document.children;
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
                    const start = segment.starts ? segment.starts[range[0]] : segment.offset + range[0];
                    const end = segment.ends ? segment.ends[range[1] - 1] : segment.offset + range[1];
                    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= end ||
                        start < 0 || end > document.raw.length) {
                        throw new Error("native-japanese: 指摘の位置を原文の範囲に対応付けられません。");
                    }
                    report(document, new RuleError(message, { padding: locator.range([start, end]) }));
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
    Processor,
    rules: {
        usage
    },
    rulesConfig: {
        usage: { severity: "error" }
    }
};
