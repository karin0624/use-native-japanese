import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { once } from "node:events";
import { createLinter, loadTextlintrc } from "textlint";
import preset from "../src/index.js";
import { configuration, getWorker, Worker } from "../src/worker.js";

const fixture = fileURLToPath(new URL("./fixtures/worker.js", import.meta.url));

async function setup(t, options = {}) {
    const dir = await mkdtemp(join(tmpdir(), "native-japanese-"));
    const path = join(dir, "人工記録 ; $() .log");
    const config = configuration({ command: [process.execPath, fixture, path], ...options });
    const configFilePath = join(dir, ".textlintrc.json");
    await writeFile(configFilePath, JSON.stringify({ rules: { "preset-native-japanese": {
        usage: { ...preset.rulesConfig.usage, ...config }
    } } }));
    const descriptor = await loadTextlintrc({ configFilePath });
    const linter = createLinter({ descriptor });
    t.after(async () => { getWorker(config).close(); await rm(dir, { recursive: true, force: true }); });
    return { linter, config, log: async () => (await readFile(path, "utf8")).trim().split("\n").map(JSON.parse) };
}

test("強調・リンクを跨いで一括送信し、見出しと絵文字の位置を原文へ戻す", async t => {
    const { linter, log } = await setup(t);
    const text = "# 🍣 **雲**を[食べる](https://example.com)\n\n前文。\n🍣 雲を**食べる**。\n";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => [message.index, message.range]), [
        [text.indexOf("雲"), [text.indexOf("雲"), text.indexOf("](https")]],
        [text.lastIndexOf("雲"), [text.lastIndexOf("雲"), text.lastIndexOf("**。")]]
    ]);
    assert.equal(messages[1].line, 4);
    assert.equal(messages[1].column, 4);
    assert.match(messages[0].message, /訂正候補: "ご飯を食べる"/);
    assert.equal(messages[0].fix, undefined);
    const requests = await log();
    assert.equal(requests.length, 1);
    assert.deepEqual(requests[0].texts, ["🍣 雲を食べる", "前文。\n🍣 雲を食べる。"]);
});

test("同じ文字を含む入れ子装飾でも該当する葉の位置を使う", async t => {
    const { linter } = await setup(t);
    const text = "**雲*雲***を食べる。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.equal(messages.length, 1);
    assert.equal(messages[0].index, text.indexOf("雲", 3));
    assert.equal(messages[0].range[1], text.indexOf("。"));
});

test("inline codeを連結し、code内部と周囲を跨ぐ指摘を元位置へ戻す", async t => {
    const { linter, log } = await setup(t);
    const text = "# 🍣 `Pod`と`Pod`を[削除する](url)\n\n`雲を食べる`。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [
        [text.indexOf("Pod"), text.indexOf("Pod") + 3],
        [text.lastIndexOf("Pod"), text.indexOf("](url)")],
        [text.indexOf("雲"), text.indexOf("雲") + "雲を食べる".length]
    ]);
    assert.equal(messages[0].column, 7);
    assert.deepEqual((await log())[0].texts, ["🍣 PodとPodを削除する", "雲を食べる。"]);
});

test("指摘のない本文は無出力にし、指摘をerrorとして文書ごとに一括送信する", async t => {
    const { linter, log } = await setup(t);
    const text = "ご飯を食べる。\n\n雲を食べる。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.equal(messages.length, 1);
    assert.equal(messages[0].index, text.indexOf("雲"));
    assert.equal(messages[0].message, '人工fixtureの指摘 訂正候補: "ご飯を食べる"');
    assert.equal(messages[0].severity, 2);
    assert.equal(messages[0].ruleId, "native-japanese/usage");
    assert.deepEqual((await log())[0].texts, ["ご飯を食べる。", "雲を食べる。"]);
    assert.equal((await log()).length, 1);
    assert.deepEqual((await linter.lintText("ご飯を食べる。", "文書.md")).messages, []);
});

test("同じ本文とパスを再検査しても前の判定を再利用しない", async t => {
    const { linter, log } = await setup(t);
    const text = "雲を食べる。";
    const first = await linter.lintText(text, "文書.md");
    const second = await linter.lintText(text, "文書.md");
    assert.deepEqual(second.messages, first.messages);
    assert.equal((await log()).length, 2);
});

test("codeのpadding・複数backtickと引用・リスト継続行の位置を保持する", async t => {
    const { linter, log } = await setup(t);
    const text = ["`  Pod  `を削除する。", "`` `Pod` ``を削除する。", "> `a\r\n> b`を削除する。",
        "- `a\n  b`を削除する。"].join("\n\n");
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [
        [text.indexOf("Pod"), text.indexOf("Pod") + 3],
        [text.lastIndexOf("Pod"), text.lastIndexOf("Pod") + 3],
        [text.indexOf("b"), text.indexOf("。", text.indexOf("b"))],
        [text.lastIndexOf("b"), text.lastIndexOf("。")]
    ]);
    assert.deepEqual((await log())[0].texts, [" Pod を削除する。", "`Pod`を削除する。", "a\r\nbを削除する。", "a\nbを削除する。"]);
});

test("codeのAST・原文・tokenが一致しない場合は未検査にせずエラーにする", async () => {
    const text = "`Pod`を削除する。";
    for (const change of [{ value: "別名" }, { range: [1, 4] }, { raw: "`別名`" }]) {
        const listeners = preset.rules.usage({ Syntax: { Paragraph: "Paragraph", Header: "Header", Document: "Document" } },
            { command: [process.execPath] });
        listeners.Paragraph({ type: "Paragraph", raw: text, range: [0, text.length],
            children: [{ type: "Code", raw: "`Pod`", value: "Pod", range: [0, 5], ...change }] });
        await assert.rejects(listeners["Document:exit"]({ raw: text }), /インラインコードの解析結果が原文と一致/);
    }
});

test("画像・HTMLの前後を連結せず、fenceを除外する", async t => {
    const { linter, log } = await setup(t);
    const text = "雲を`x`食べる。\n\n`雲を食べる`\n\n```txt\n雲を食べる\n```\n\n雲を![画像](url)食べる。\n\n雲を<span>食べる</span>。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [[text.indexOf("`雲") + 1, text.indexOf("`雲") + 6]]);
    assert.deepEqual((await log())[0].texts, ["雲をx食べる。", "雲を食べる", "雲を", "食べる。", "雲を", "食べる", "。"]);
});

test("entityとescapeのある葉は未検査にし、後の安全な葉を正しく写像する", async t => {
    const { linter, log } = await setup(t);
    const text = "&amp; 雲を食べる。\n\n\\* 雲を食べる。\n\n&amp; **雲を食べる**。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.equal(messages.length, 1);
    assert.equal(messages[0].index, text.lastIndexOf("雲"));
    assert.equal(messages[0].range[1], text.lastIndexOf("**。"));
    assert.deepEqual((await log())[0].texts, ["雲を食べる。"]);
});

test("並列文書の応答を混同せず同じプロセスを再利用し、commandをshell展開しない", async t => {
    const { linter, log, config } = await setup(t);
    const documents = ["遅延 🍣 **雲を食べる**", "雲を食べる"];
    const results = await Promise.all(documents.map((text, i) => linter.lintText(text, `${i}.md`)));
    assert.equal(results[0].messages[0].index, documents[0].indexOf("雲"));
    assert.equal(results[1].messages[0].index, 0);
    const requests = await log();
    assert.equal(new Set(requests.map(request => request.pid)).size, 1);
    assert.deepEqual(requests[0].args, config.command.slice(2));
});

test("処理エラーを返したworkerは引き続き利用できる", async t => {
    const { linter, log } = await setup(t);
    await assert.rejects(linter.lintText("エラー", "文書.md"), /人工的な処理失敗/);
    assert.equal((await linter.lintText("雲を食べる", "文書.md")).messages.length, 1);
    assert.equal(new Set((await log()).map(request => request.pid)).size, 1);
});

for (const trigger of ["終了", "不正JSON", "件数不正"]) {
    test(`${trigger}時に全pendingを拒否して次の要求で再起動する`, async t => {
        const { config } = await setup(t);
        const worker = getWorker(config);
        const results = await Promise.allSettled([worker.request(["待機"]), worker.request([trigger])]);
        assert.deepEqual(results.map(result => result.status), ["rejected", "rejected"]);
        assert.equal((await worker.request(["雲を食べる"]))[0].diagnostics.length, 1);
    });
}

test("idleで終了し、応答期限で保留要求を拒否する", { timeout: 5_000 }, async t => {
    const { config } = await setup(t);
    const worker = new Worker(config, { idleMs: 20, timeoutMs: 10_000 });
    t.after(() => worker.close());
    await worker.request(["雲を食べる"]);
    await once(worker.child, "close");
    assert.equal(worker.child, null);
    worker.timeoutMs = 100;
    await assert.rejects(worker.request(["待機"]), /時間内/);
    assert.equal(worker.pending.size, 0);
});

test("外部commandの起動失敗と設定した応答期限を伝える", async t => {
    const absent = await setup(t, { command: ["/存在しない/実行ファイル"] });
    await assert.rejects(getWorker(absent.config).request(["文"]), /起動できません/);
    const { linter } = await setup(t, { requestTimeoutMs: 100 });
    await assert.rejects(linter.lintText("待機", "文書.md"), /時間内/);
});

test("UTF-16のサロゲート境界・範囲外・encoding・訂正候補の不正を拒否する", async t => {
    const { linter } = await setup(t);
    for (const [text, error] of [["🍣範囲不正", /UTF-16範囲/], ["範囲外", /UTF-16範囲/],
        ["encoding不正", /offsetEncoding/], ["候補不正", /suggestions/]]) {
        await assert.rejects(linter.lintText(text, "文書.md"), error);
    }
});

test("commandと期限の設定を検証する", () => {
    assert.deepEqual(configuration(), { requestTimeoutMs: 120_000 });
    for (const command of [null, "python", [], [""], ["node", 3], ["node\0"]]) {
        assert.throws(() => configuration({ command }), /command/);
    }
    assert.throws(() => configuration({ command: ["node"], requestTimeoutMs: 0 }), /requestTimeoutMs/);
});
