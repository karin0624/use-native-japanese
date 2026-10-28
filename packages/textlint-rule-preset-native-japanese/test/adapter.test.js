import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { once } from "node:events";
import { spawnSync } from "node:child_process";
import { createLinter, loadTextlintrc } from "textlint";
import preset from "../src/index.js";
import { configuration, getWorker, Worker } from "../src/worker.js";

const fixture = fileURLToPath(new URL("./fixtures/worker.js", import.meta.url));

async function setup(t, options = {}, fixtureArgs = []) {
    const dir = await mkdtemp(join(tmpdir(), "native-japanese-"));
    const path = join(dir, "人工記録 ; $() .log");
    const config = configuration({ command: [process.execPath, fixture, path, ...fixtureArgs], ...options });
    const configFilePath = join(dir, ".textlintrc.json");
    await writeFile(configFilePath, JSON.stringify({ rules: { "preset-native-japanese": {
        usage: { ...preset.rulesConfig.usage, ...config }
    } } }));
    const descriptor = await loadTextlintrc({ configFilePath });
    const linter = createLinter({ descriptor: descriptor.shallowMerge({
        plugins: [{ pluginId: "native-japanese-markdown", plugin: preset, options: true }, ...descriptor.toKernelOptions().plugins]
    }) });
    t.after(async () => { getWorker(config).close(); await rm(dir, { recursive: true, force: true }); });
    return { linter, config, dir, configFilePath,
        log: async () => (await readFile(path, "utf8")).trim().split("\n").map(JSON.parse) };
}

test("通常CLIのplugin指定で変換本文を検査し、--fixでもMarkdown原文を保持する", async t => {
    const { dir, configFilePath, log } = await setup(t);
    const path = join(dir, "原文.md");
    const text = "# 🍣 **&#x96F2;を食べる**。\r\n\r\n\\* 雲を食べる。";
    await writeFile(path, text);
    const cli = fileURLToPath(new URL("../../../node_modules/textlint/bin/textlint.js", import.meta.url));
    for (const extra of [[], ["--fix"]]) {
        const result = spawnSync(process.execPath, [cli, "--plugin", "textlint-rule-preset-native-japanese",
            "--config", configFilePath, "--format", "json", ...extra, path], { encoding: "utf8", timeout: 10_000 });
        assert.equal(result.status, extra.length ? 0 : 1, result.stderr);
        const [output] = JSON.parse(result.stdout);
        assert.deepEqual(output.messages.map(message => message.range), extra.length ? [] : [
            [text.indexOf("&#x96F2;"), text.indexOf("**。")],
            [text.indexOf("雲"), text.indexOf("雲") + "雲を食べる".length]
        ]);
        assert.equal(await readFile(path, "utf8"), text);
    }
    // textlintの--fixは修正可能なルールだけを実行する。
    assert.deepEqual((await log()).map(request => request.texts), [["🍣 雲を食べる。", "* 雲を食べる。"]]);
});

test("標準Markdown拡張子で本文を解析し、frontmatterの全形式を除外する", async t => {
    const { linter, log } = await setup(t);
    for (const extension of ["md", "markdown", "mdown", "mkdn", "mkd", "mdwn", "mkdown", "ron"]) {
        const text = "**雲を食べる**。";
        assert.deepEqual((await linter.lintText(text, `文書.${extension}`)).messages.map(message => message.range), [[2, 7]]);
    }
    for (const header of ["---\n雲を食べる\n---", "+++\n雲を食べる\n+++", ";;;\n雲を食べる\n;;;",
        "---json\n雲を食べる\n---", "{\n雲を食べる\n}"]) {
        const text = `${header}\n\n雲を食べる。`;
        assert.deepEqual((await linter.lintText(text, "文書.md")).messages.map(message => message.range),
            [[text.lastIndexOf("雲"), text.lastIndexOf("雲") + "雲を食べる".length]]);
    }
    assert.deepEqual((await log()).map(request => request.texts), Array.from({ length: 13 }, () => ["雲を食べる。"]));
});

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

test("取り消し線・自動リンク・タスクリストの本文を保持し、原文位置へ戻す", async t => {
    // textlintが対応するGFMの表示本文を保ち、拡張構文がある段落も解析する。
    const { linter, log } = await setup(t);
    const paragraphs = ["~~雲を食べる~~。", "https://example.com 雲を食べる。",
        "www.example.com 雲を食べる。", "雲をexample@example.com食べる。",
        "<https://example.com> 雲を食べる。", "<a@example.com> 雲を食べる。",
        "- [x] 雲を食べる。", "- [ ] 雲を食べる。", "~~&#x96F2;を食べる~~。"];
    const text = paragraphs.join("\n\n");
    const { messages } = await linter.lintText(text, "文書.md");
    const expected = [...text.matchAll(/雲を食べる/gu)].map(match => [match.index, match.index + match[0].length]);
    expected.push([text.indexOf("&#x96F2;"), text.lastIndexOf("~~。")]);
    assert.deepEqual(messages.map(message => message.range), expected);
    assert.deepEqual((await log())[0].texts, ["雲を食べる。", "https://example.com 雲を食べる。",
        "www.example.com 雲を食べる。", "雲をexample@example.com食べる。",
        "https://example.com 雲を食べる。", "a@example.com 雲を食べる。",
        "雲を食べる。", "雲を食べる。", "雲を食べる。"]);
});

test("取り消し線と自動リンクの本文自体への指摘を原文へ戻す", async t => {
    const { linter } = await setup(t, {}, ["--diagnostic-ranges", JSON.stringify([[0, 1]])]);
    for (const [text, start] of [["~~雲を食べる~~。", 2], ["https://example.com 雲を食べる。", 0],
        ["www.example.com 雲を食べる。", 0], ["雲をexample@example.com食べる。", 0],
        ["<https://example.com> 雲を食べる。", 1], ["<a@example.com> 雲を食べる。", 1]]) {
        const { messages } = await linter.lintText(text, "文書.md");
        assert.deepEqual(messages.map(message => message.range), [[start, start + 1]]);
    }
});

test("同じ文字を含む入れ子装飾でも該当する葉の位置を使う", async t => {
    const { linter } = await setup(t);
    const text = "**雲*雲***を食べる。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.equal(messages.length, 1);
    assert.equal(messages[0].index, text.indexOf("雲", 3));
    assert.equal(messages[0].range[1], text.indexOf("。"));
});

test("Markdownとテキストの単改行を同じ本文で送り、空行と見出しで分ける", async t => {
    const { linter, log } = await setup(t);
    const text = "前文。\n🍣 雲を食べる。\n\n後文。";
    for (const extension of ["md", "txt", "text"]) {
        const { messages } = await linter.lintText(text, `文書.${extension}`);
        assert.deepEqual(messages.map(message => message.range), [
            [text.indexOf("雲"), text.indexOf("雲") + "雲を食べる".length]
        ]);
        assert.equal(messages[0].line, 2);
        assert.equal(messages[0].column, 4);
    }
    const heading = "前文。\n続き。\n# 見出し\n後文。";
    await linter.lintText(heading, "見出し.md");
    assert.deepEqual((await log()).map(request => request.texts), [
        ["前文。\n🍣 雲を食べる。", "後文。"],
        ["前文。\n🍣 雲を食べる。", "後文。"],
        ["前文。\n🍣 雲を食べる。", "後文。"],
        ["前文。\n続き。", "見出し", "後文。"]
    ]);
});

test("リスト継続行の本文を一括送信し、改行前後の指摘を原文へ戻す", async t => {
    const { linter, log } = await setup(t);
    const text = "- 雲を食べる。\n   🍣 Podを削除する。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual((await log())[0].texts, ["雲を食べる。\n🍣 Podを削除する。"]);
    assert.deepEqual(messages.map(message => message.range), [
        [text.indexOf("雲"), text.indexOf("雲") + "雲を食べる".length],
        [text.indexOf("Pod"), text.indexOf("Pod") + "Podを削除する".length]
    ]);
    assert.equal(messages[1].line, 2);
    assert.equal(messages[1].column, 7);
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

test("同じ本文とパスの再検査で外部commandの新しい判定を返す", async t => {
    // 外部commandの応答を現在の検査結果へ反映する契約を確かめる。
    const { linter } = await setup(t, {}, ["--clear-diagnostics-after-first-request"]);
    const text = "雲を食べる。";
    const first = await linter.lintText(text, "文書.md");
    const second = await linter.lintText(text, "文書.md");
    assert.equal(first.messages.length, 1);
    assert.deepEqual(second.messages, []);
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

test("画像・HTMLの前後を連結せず、fenceを除外する", async t => {
    const { linter, log } = await setup(t);
    const text = "雲を`x`食べる。\n\n`雲を食べる`\n\n```txt\n雲を食べる\n```\n\n雲を![画像](url)食べる。\n\n雲を<span>食べる</span>。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [[text.indexOf("`雲") + 1, text.indexOf("`雲") + 6]]);
    assert.deepEqual((await log())[0].texts, ["雲をx食べる。", "雲を食べる", "雲を", "食べる。", "雲を", "食べる", "。"]);
});

// 期待はMarkdownの可視本文と原文の対応、および通常テキストをそのまま渡す契約から定める。
test("entityとescapeを含む全段落を解析し、変換後の指摘を原文へ戻す", async t => {
    const { linter, log } = await setup(t);
    const text = "&amp; 雲を食べる。\n\n\\* 雲を食べる。\n\n&amp; **雲を食べる**。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [...text.matchAll(/雲を食べる/gu)].map(match =>
        [match.index, match.index + match[0].length]));
    assert.deepEqual((await log())[0].texts, ["& 雲を食べる。", "* 雲を食べる。", "& 雲を食べる。"]);
});

test("数値参照と装飾を跨ぐ指摘の両端に元の文字参照全体を含める", async t => {
    const { linter, log } = await setup(t);
    const text = "&#x96F2;を**食べ&#x308B;**。\n\n**&#38642;を食べる**。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [
        [0, text.indexOf("**。")], [text.indexOf("&#38642;"), text.lastIndexOf("**。")]
    ]);
    assert.deepEqual((await log())[0].texts, ["雲を食べる。", "雲を食べる。"]);
});

test("複数文字参照の部分指摘と補助平面・escapeの指摘を元の表記全体へ戻す", async t => {
    // CommonMark §6.2: fjligは2文字、NotEqualTildeは結合文字を含む2文字、Afrは補助平面文字。
    const ranges = [[0, 1], [3, 4], [6, 8], [9, 11], [12, 14], [15, 16]];
    const { linter, log } = await setup(t, {}, ["--diagnostic-ranges", JSON.stringify(ranges)]);
    const entities = ["&fjlig;", "&NotEqualTilde;", "&Afr;", "&#x1F363;", "&#127843;", "\\*"];
    const text = entities.join(" ");
    const { messages } = await linter.lintText(text, "文書.md");
    const expected = entities.map(entity => [text.indexOf(entity), text.indexOf(entity) + entity.length]);
    assert.deepEqual(messages.map(message => message.range), expected);
    assert.deepEqual((await log())[0].texts, ["fj ≂̸ 𝔄 🍣 🍣 *"]);
});

test("複数文字参照の後半だけの指摘も参照全体へ戻す", async t => {
    const { linter } = await setup(t, {}, ["--diagnostic-ranges", JSON.stringify([[1, 2], [4, 5]])]);
    const text = "&fjlig; &NotEqualTilde;";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [[0, 7], [8, text.length]]);
});

test("文字参照は一段だけ復号し、無効な参照とcode内部の表記を保持する", async t => {
    const { linter, log } = await setup(t);
    const text = "\\&amp; 雲を食べる。\n\n&amp;amp; 雲を食べる。\n\n&bogus; &amp 雲を食べる。\n\n`&amp; \\*` 雲を食べる。\n\n&amp;";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [...text.matchAll(/雲を食べる/gu)].map(match =>
        [match.index, match.index + match[0].length]));
    assert.deepEqual((await log())[0].texts, ["&amp; 雲を食べる。", "&amp; 雲を食べる。",
        "&bogus; &amp 雲を食べる。", "&amp; \\* 雲を食べる。", "&"]);
});

test("引用とリスト継続行でCRLFと変換を併用しても原文位置を保持する", async t => {
    const { linter, log } = await setup(t);
    const text = "> &amp; 🍣\r\n> &#x96F2;を**食べる**。\r\n\r\n- \\* &#127843;\r\n  &#38642;を食べ&#x308B;。";
    const { messages } = await linter.lintText(text, "文書.md");
    assert.deepEqual(messages.map(message => [message.range, message.line, message.column]), [
        [[text.indexOf("&#x96F2;"), text.indexOf("**。")], 2, 3],
        [[text.indexOf("&#38642;"), text.lastIndexOf("。")], 5, 3]
    ]);
    assert.deepEqual((await log())[0].texts, ["& 🍣\r\n雲を食べる。", "* 🍣\r\n雲を食べる。"]);
});

test("Markdownのhard breakを改行へ変換し、txtとtextでは構文を文字通り保持する", async t => {
    const { linter, log } = await setup(t);
    const text = "&amp; \\* **雲を食べる**。  \r\n# `Pod`を[削除する](url)\\\r\n続き。";
    for (const extension of ["txt", "text"]) {
        const { messages } = await linter.lintText(text, `文書.${extension}`);
        assert.deepEqual(messages.map(message => message.range), [
            [text.indexOf("雲"), text.indexOf("雲") + "雲を食べる".length],
            [text.indexOf("Pod"), text.indexOf("Pod") + "Pod".length]
        ]);
    }
    const markdown = "&amp; 雲を食べる。  \r\n\\* Podを削除する。\\\r\n続き。";
    const { messages } = await linter.lintText(markdown, "文書.md");
    assert.deepEqual(messages.map(message => message.range), [
        [markdown.indexOf("雲"), markdown.indexOf("雲") + "雲を食べる".length],
        [markdown.indexOf("Pod"), markdown.indexOf("Pod") + "Podを削除する".length]
    ]);
    assert.deepEqual((await log()).map(request => request.texts), [[text], [text],
        ["& 雲を食べる。\r\n* Podを削除する。\r\n続き。"]]);
});

test("pluginのないMarkdownは設定案内を返し、構文を解析機へ送らない", async () => {
    const listeners = preset.rules.usage({ Syntax: { Paragraph: "Paragraph", Header: "Header", Document: "Document" },
        getFilePath: () => "文書.md" }, { command: [process.execPath] });
    await assert.rejects(listeners["Document:exit"]({ raw: "**雲を食べる**" }), /--plugin textlint-rule-preset-native-japanese/);
});

test("並列文書の応答を混同せず同じプロセスを再利用し、commandをshell展開しない", async t => {
    const { linter, log, config } = await setup(t);
    const documents = ["遅延 🍣 **雲を食べる**", "雲を食べる"];
    const results = await Promise.all(documents.map(text => linter.lintText(text, "並列.md")));
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
    for (const [text, error] of [["🍣範囲不正", /UTF-16範囲/], ["&#x1F363;範囲不正", /UTF-16範囲/], ["範囲外", /UTF-16範囲/],
        ["encoding不正", /offsetEncoding/], ["候補不正", /suggestions/], ["説明不正", /message/]]) {
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
