// npm packや自分のpackageの再導入をせず、checkoutの通常のtextlint経路を確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const assets = path.join(root, "packages/textlint-rule-native-japanese/assets");
const cli = path.join(root, "node_modules/textlint/bin/textlint.js");

function run(command, args, statuses = [0]) {
    const result = spawnSync(command, args, { cwd: root, encoding: "utf8", shell: false, timeout: 120_000 });
    assert.ifError(result.error);
    assert.ok(statuses.includes(result.status), `${command}の終了値${result.status}\n${result.stderr}\n${result.stdout}`);
    try { return JSON.parse(result.stdout); }
    catch (cause) {
        throw new Error(`${command}の実行結果がJSONではありません（終了値${result.status}）。\n${result.stderr}\n${result.stdout}`,
            { cause });
    }
}

function ordered(messages) {
    return messages.sort((a, b) => a.range[0] - b.range[0] || a.range[1] - b.range[1]);
}

function actualMessages(result) {
    return ordered(result.messages.map(({ range, message, ruleId }) => ({ range, message, ruleId })));
}

function expectedMessages(result) {
    assert.equal(result.offsetEncoding, "utf-16");
    const messages = result.diagnostics.map(({ range, message }) => ({ range, message, ruleId: "native-japanese" }));
    for (const item of result.unavailable) {
        const label = item.kind === "analysis" ? "判定保留（解析情報）" : "判定保留（比較不能）";
        messages.push(...item.ranges.map(range => ({ range, message: `${label}: ${item.reason}`, ruleId: "native-japanese" })));
    }
    return ordered(messages);
}

test("checkoutの設定でファイル・globを検査し、公開Python CLIのUTF-16範囲と一致する", { timeout: 300_000 }, async t => {
    const source = run(python, ["-E", "-X", "utf8", "-c",
        "import json,native_japanese; print(json.dumps(native_japanese.__file__))"]);
    assert.equal(await realpath(source), await realpath(path.join(root, "src/native_japanese/__init__.py")),
        "uv syncのeditable installがcheckoutのソースを参照する必要があります。");

    const directory = await mkdtemp(path.join(tmpdir(), "native-japanese-checkout-"));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const texts = ["😀 重複を畳む。", "電気を消す。"];
    const files = texts.map((_, i) => path.join(directory, `${i}.md`));
    await Promise.all(files.map((file, i) => writeFile(file, texts[i])));

    // cwdの設定を自動読込し、checkoutのソースをファイル指定で検査する。
    const first = run(process.execPath, [cli, "--format", "json", files[0]], [0, 1]);
    assert.equal(first.length, 1);
    const glob = `${directory.split(path.sep).join("/")}/*.md`;
    const results = run(process.execPath, [cli, "--format", "json", glob], [0, 1]);
    assert.equal(results.length, files.length);
    for (let i = 0; i < files.length; i++) {
        const result = results.find(item => path.basename(item.filePath) === `${i}.md`);
        assert.ok(result, `${i}.mdがglobで検査される必要があります。`);
        const direct = run(python, ["-E", "-X", "utf8", "-m", "native_japanese", "lint",
            "--reference", path.join(assets, "reference.sqlite3"), "--assets-dir", assets, files[i]]);
        assert.deepEqual(actualMessages(result), expectedMessages(direct));
        if (i === 0) assert.deepEqual(actualMessages(result), actualMessages(first[0]));
    }
});
