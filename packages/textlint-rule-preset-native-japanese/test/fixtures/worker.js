// 通信・位置・寿命の試験専用。日本語の検出精度を評価する実装ではない。
import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";

const log = process.argv[2];
createInterface({ input: process.stdin }).on("line", line => {
    const { id, texts } = JSON.parse(line);
    appendFileSync(log, `${JSON.stringify({ id, texts, pid: process.pid, args: process.argv.slice(2) })}\n`);
    if (texts.includes("終了")) process.exit(7);
    if (texts.includes("待機")) return;
    if (texts.includes("不正JSON")) { process.stdout.write("JSONではない\n"); return; }
    if (texts.includes("件数不正")) { process.stdout.write(`${JSON.stringify({ id, results: [] })}\n`); return; }
    if (texts.includes("エラー")) {
        process.stdout.write(`${JSON.stringify({ id, error: { message: "人工的な処理失敗" } })}\n`);
        return;
    }
    const results = texts.map(text => ({ offsetEncoding: "utf-16", diagnostics: Array.from(text.matchAll(/雲を食べる|Pod(?:を削除する)?|bを削除する/g), match => ({
        range: [match.index, match.index + match[0].length],
        message: "人工fixtureの指摘", suggestions: ["ご飯を食べる"]
    })) }));
    if (texts.includes("🍣範囲不正")) results[0].diagnostics = [{ range: [1, 3], message: "不正な範囲" }];
    if (texts.includes("範囲外")) results[0].diagnostics = [{ range: [0, 100], message: "不正な範囲" }];
    if (texts.includes("encoding不正")) results[0].offsetEncoding = "unicode-code-points";
    if (texts.includes("候補不正")) results[0].diagnostics = [{ range: [0, 1], message: "指摘", suggestions: [42] }];
    setTimeout(() => process.stdout.write(`${JSON.stringify({ id, results })}\n`), texts.some(text => text.includes("遅延")) ? 60 : 1);
});
