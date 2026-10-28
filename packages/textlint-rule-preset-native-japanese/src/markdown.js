import { fromMarkdown } from "mdast-util-from-markdown";
import { gfm } from "micromark-extension-gfm";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { frontmatter } from "micromark-extension-frontmatter";
import { frontmatterFromMarkdown } from "mdast-util-frontmatter";

const tokens = new Set(["data", "characterReference", "characterEscape", "codeTextData", "lineEnding",
    "autolinkProtocol", "autolinkEmail", "literalAutolinkHttp", "literalAutolinkEmail", "literalAutolinkWww"]);
const formats = ["yaml", "toml", { type: "json", fence: { open: ";;;", close: ";;;" } },
    { type: "json", fence: { open: "---json", close: "---" } },
    { type: "json", fence: { open: "{", close: "}" } }];
const location = point => ({ line: point.line, column: point.column - 1 });

function textNode(text, span) {
    return { type: "Str", value: span.value, raw: text.slice(span.start.offset, span.end.offset),
        range: [span.start.offset, span.end.offset],
        loc: { start: location(span.start), end: location(span.end) }, sourceAtomic: span.atomic };
}

function paragraphs(node, spans, text) {
    const result = [];
    let children = [];
    const flush = () => {
        if (children.length && children.some(child => child.value.trim())) {
            const first = children[0], last = children.at(-1);
            const starts = [], ends = [];
            for (const child of children) {
                for (let i = 0; i < child.value.length; i++) {
                    starts.push(child.range[0] + (child.sourceAtomic ? 0 : i));
                    ends.push(child.sourceAtomic ? child.range[1] : child.range[0] + i + 1);
                }
            }
            result.push({ type: node.type === "heading" ? "Header" : "Paragraph", depth: node.depth,
                raw: text.slice(first.range[0], last.range[1]), range: [first.range[0], last.range[1]],
                loc: { start: first.loc.start, end: last.loc.end }, children,
                text: children.map(child => child.value).join(""), starts, ends });
        }
        children = [];
    };
    const visit = child => {
        if (["emphasis", "strong", "delete", "link", "linkReference"].includes(child.type)) {
            child.children.forEach(visit);
        } else if (["text", "inlineCode", "break"].includes(child.type)) {
            const values = [];
            for (const span of spans.get(child) ?? []) {
                // compilerが確定した葉の範囲と交差させ、本文に含まれる部分だけを接続する。
                const start = span.start.offset < child.position.start.offset ? child.position.start : span.start;
                const end = span.end.offset > child.position.end.offset ? child.position.end : span.end;
                if (start.offset >= end.offset) continue;
                if ((span.atomic && (start !== span.start || end !== span.end)) ||
                    (!span.atomic && span.end.offset - span.start.offset !== span.value.length)) {
                    throw new Error("native-japanese: 本文の文字と原文範囲を対応付けられません。");
                }
                const value = span.atomic ? span.value : span.value.slice(start.offset - span.start.offset, end.offset - span.start.offset);
                values.push(value);
                children.push(textNode(text, { ...span, start, end, value }));
            }
            if (child.value !== undefined && values.join("") !== child.value) {
                throw new Error("native-japanese: parserの本文と位置情報が一致しません。");
            }
        } else {
            // 画像・HTML・拡張ノードの前後は別の解析対象にする。
            flush();
        }
    };
    node.children.forEach(visit);
    flush();
    return result;
}

export function parseMarkdown(input) {
    // textlintのSourceCodeはBOMを除いた原文を基準にする。
    const text = input.charCodeAt(0) === 0xfeff ? input.slice(1) : input;
    const spans = new WeakMap(), pending = new WeakMap(), children = [];
    const observer = {
        beforeEnter(token) {
            if (!tokens.has(token.type)) return;
            const parent = this.stack.at(-1), tail = parent.children?.at(-1);
            pending.set(token, { parent, tail, length: tail?.value?.length ?? 0,
                code: this.stack.findLast(node => node.type === "inlineCode") });
        },
        afterExit(token) {
            const before = pending.get(token);
            if (!before) return;
            const tail = before.parent.children?.at(-1);
            if (!tail) return;
            const value = tail.type === "break" && token.type === "lineEnding" ? this.sliceSerialize(token)
                : (tail.value ?? "").slice(tail === before.tail ? before.length : 0);
            if (!value) return;
            const node = before.code ?? tail;
            const parts = spans.get(node) ?? [];
            spans.set(node, parts);
            parts.push({ value, start: token.start, end: token.end,
                atomic: token.type === "characterReference" || token.type === "characterEscape" });
        },
        transforms: [tree => {
            // 後続のGFM transformが葉を分割する前に、本文と原文位置を確定する。
            const visit = node => {
                if (node.type === "paragraph" || node.type === "heading") children.push(...paragraphs(node, spans, text));
                else node.children?.forEach(visit);
            };
            visit(tree);
        }]
    };
    const tree = fromMarkdown(text, { extensions: [gfm(), frontmatter(formats)],
        mdastExtensions: [observer, gfmFromMarkdown(), frontmatterFromMarkdown(formats)] });
    return { type: "Document", raw: text, range: [0, text.length], nativeJapanese: true,
        loc: { start: { line: 1, column: 0 }, end: location(tree.position.end) }, children };
}

export class Processor {
    constructor(options = {}) { this.extensions = options.extensions ?? []; }
    availableExtensions() {
        return [".md", ".markdown", ".mdown", ".mkdn", ".mkd", ".mdwn", ".mkdown", ".ron", ...this.extensions];
    }
    processor() {
        return { preProcess: parseMarkdown, postProcess: (messages, filePath) => ({ messages, filePath: filePath ?? "<markdown>" }) };
    }
}
