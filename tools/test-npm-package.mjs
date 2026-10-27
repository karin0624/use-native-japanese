// 公開せず、実tarballの内容と通常のtextlint導入経路を3 OS共通で確認する。
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdtemp, mkdir, readFile, readdir, realpath, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const tarball = path.resolve(process.argv[2] ?? "");
assert.ok(process.argv[2]?.endsWith(".tgz"), "検証するnpm packのtgzを引数で指定してください。");
const sourcePackage = fileURLToPath(new URL("../packages/textlint-rule-preset-native-japanese/", import.meta.url));
const sourceManifest = JSON.parse(await readFile(path.join(sourcePackage, "assets/manifest.json"), "utf8"));
const temporary = await mkdtemp(path.join(tmpdir(), "native-japanese-package-"));
const project = path.join(temporary, "consumer");
const home = path.join(temporary, "home");
const texts = ["😀 重複を畳む。", "電気を消す。"];

function run(command, args, { cwd = project, env = process.env, input, statuses = [0] } = {}) {
    return new Promise((resolve, reject) => {
        const child = spawn(command, args, { cwd, env, shell: false, stdio: ["pipe", "pipe", "pipe"] });
        let stdout = "";
        let stderr = "";
        child.stdout.setEncoding("utf8");
        child.stderr.setEncoding("utf8");
        child.stdout.on("data", chunk => { stdout += chunk; });
        child.stderr.on("data", chunk => { stderr += chunk; });
        child.on("error", reject);
        child.on("close", status => {
            if (!statuses.includes(status)) {
                reject(new Error(`${command}の終了値${status}\n${stderr}\n${stdout}`));
            } else resolve({ stdout, stderr, status });
        });
        child.stdin.end(input);
    });
}

async function npmCli() {
    const candidates = [process.env.npm_execpath,
        path.join(path.dirname(process.execPath), "node_modules/npm/bin/npm-cli.js")];
    for (const directory of (process.env.PATH ?? "").split(path.delimiter)) {
        const command = path.join(directory, "npm");
        if (existsSync(command)) candidates.push(await realpath(command));
    }
    const cli = candidates.find(candidate => candidate?.endsWith("npm-cli.js") && existsSync(candidate));
    assert.ok(cli, "現在のNodeに対応するnpm-cli.jsが見つかりません。");
    return cli;
}

async function findEnvironments(directory) {
    const found = [];
    for (const item of await readdir(directory, { withFileTypes: true })) {
        if (item.isFile() && item.name === "pyvenv.cfg") found.push(directory);
        // Python管理領域や依存package内までは走査しない。
        if (item.isDirectory() && !["python", "uv", "lib", "Lib", "bin", "Scripts", "node_modules"].includes(item.name)) {
            found.push(...await findEnvironments(path.join(directory, item.name)));
        }
    }
    return found;
}

async function sha256(file) {
    return createHash("sha256").update(await readFile(file)).digest("hex");
}

function messages(result) {
    return result.messages.map(({ range, message, ruleId, severity }) => ({ range, message, ruleId, severity }))
        .sort((a, b) => a.range[0] - b.range[0] || a.range[1] - b.range[1]);
}

function expectedMessages(result) {
    assert.equal(result.offsetEncoding, "utf-16");
    const expected = result.diagnostics.map(({ range, message }) => ({ range, message, ruleId: "native-japanese/usage", severity: 2 }));
    return expected.sort((a, b) => a.range[0] - b.range[0] || a.range[1] - b.range[1]);
}

function jsonOutput(result) {
    assert.doesNotMatch(`${result.stderr}\n${result.stdout}`, /No rules found/i);
    try { return JSON.parse(result.stdout); }
    catch (cause) {
        throw new Error(`実行結果がJSONではありません。\n${result.stderr}\n${result.stdout}`, { cause });
    }
}

try {
    await mkdir(project);
    await mkdir(home);
    // WindowsのGNU tarがdriveの「:」をremote指定として扱わないよう、所在dirから開く。
    const listing = await run("tar", ["-tzf", path.basename(tarball)], { cwd: path.dirname(tarball) });
    const entries = listing.stdout.trim().split(/\r?\n/).filter(name => !name.endsWith("/"));
    const allowed = [
        /^package\/(?:package\.json|README\.md|THIRD_PARTY_NOTICES\.md)$/,
        /^package\/src\/[a-z-]+\.js$/,
        /^package\/licenses\/[A-Za-z0-9.-]+\.txt$/,
        /^package\/assets\/(?:manifest\.json|sources\.json|reference\.sqlite3|fasttext-ja-mini)$/,
        /^package\/backend\/(?:pyproject\.toml|uv\.lock)$/,
        /^package\/backend\/src\/native_japanese\/(?:[a-z_]+\.py|data\/native-assets\.json)$/
    ];
    for (const name of entries) assert.ok(
        allowed.some(pattern => pattern.test(name)),
        `同梱対象外のfile: ${name}`
    );
    assert.ok(entries.includes("package/THIRD_PARTY_NOTICES.md"));
    assert.ok(entries.includes("package/assets/sources.json"));
    const licenses = await readdir(path.join(sourcePackage, "licenses"));
    assert.deepEqual(entries.filter(name => name.startsWith("package/licenses/")).sort(),
        licenses.map(name => `package/licenses/${name}`).sort());

    await writeFile(path.join(project, "package.json"), JSON.stringify({ private: true, type: "module" }));
    console.log("repo外へ、install scriptsを無効にして通常のoptional dependenciesとともに導入します。");
    await run(process.execPath, [await npmCli(), "install", "--ignore-scripts", "--no-audit", "--no-fund", tarball, "textlint@15.8.0"]);
    const installed = path.join(project, "node_modules/textlint-rule-preset-native-japanese");
    const installedBytes = (await Promise.all(entries.map(name => stat(path.join(installed, name.slice("package/".length))))))
        .reduce((bytes, info) => bytes + info.size, 0);
    console.log(`tarball ${(await stat(tarball)).size} bytes、同梱package実体 ${installedBytes} bytes（npm依存・Python/cacheは別）。`);
    const lock = await readFile(path.join(installed, "backend/uv.lock"), "utf8");
    assert.equal(lock, await readFile(new URL("../uv.lock", import.meta.url), "utf8"));
    const assets = path.join(installed, "assets");
    const manifest = JSON.parse(await readFile(path.join(assets, "manifest.json"), "utf8"));
    assert.deepEqual(manifest, sourceManifest);
    for (const key of ["reference", "model"]) {
        const expected = sourceManifest[key];
        const content = await readFile(path.join(assets, expected.file));
        assert.equal(content.length, expected.bytes);
        assert.ok(!content.subarray(0, 100).toString().startsWith("version https://git-lfs.github.com/spec/v1"));
        assert.equal(await sha256(path.join(assets, expected.file)), expected.sha256);
    }
    assert.equal(await sha256(path.join(assets, sourceManifest.provenance.sourcesFile)), sourceManifest.provenance.sourcesSha256);
    for (const name of licenses) {
        assert.deepEqual(await readFile(path.join(installed, "licenses", name)),
            await readFile(path.join(sourcePackage, "licenses", name)));
    }
    console.log("資源実体の固定hash・出典・帰属と、raw corpus/構築正本/cacheを含まないfile一覧を確認しました。");

    const env = { ...process.env, HOME: home, USERPROFILE: home,
        LOCALAPPDATA: home, APPDATA: home, XDG_CACHE_HOME: path.join(home, "cache") };
    // 呼出元の開発環境を持ち込まず、製品がmanaged Pythonを選ぶことを確認する。
    for (const key of ["VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH", "UV_CACHE_DIR",
        "UV_PROJECT_ENVIRONMENT", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON", "UV_OFFLINE", "UV_PYTHON_DOWNLOADS"]) delete env[key];
    assert.deepEqual(await findEnvironments(home), []);
    await mkdir(path.join(project, "docs"));
    await writeFile(path.join(project, "input.md"), texts[0]);
    await Promise.all(texts.map((text, i) => writeFile(path.join(project, "docs", `${i}.md`), text)));
    const cli = path.join(project, "node_modules/textlint/bin/textlint.js");
    console.log("空の専用cacheから、--ruleだけの初回file検査を実行します（初回取得はnetworkを使います）。");
    const firstStarted = performance.now();
    const first = jsonOutput(await run(process.execPath, [cli, "--rule", "preset-native-japanese", "--format", "json", "input.md"],
        { env, statuses: [0, 1] }));
    console.log(`初回準備とfile検査: ${((performance.now() - firstStarted) / 1000).toFixed(2)}秒。`);
    assert.equal(first.length, 1);

    const environments = await findEnvironments(home);
    assert.equal(environments.length, 1, "初回準備は専用cacheの単一環境に入る必要があります。");
    const python = path.join(environments[0], process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    const pythonInfo = jsonOutput(await run(python, ["-E", "-c", "import json,sys; print(json.dumps({'version':sys.version.split()[0],'base':sys.base_prefix,'prefix':sys.prefix}))"], { env }));
    assert.equal(pythonInfo.version, "3.12.13");
    // macOSの/varと/private/varなど、同じ領域の別名を実パスへ揃える。
    const baseRelative = path.relative(await realpath(home), await realpath(pythonInfo.base));
    assert.ok(!baseRelative.startsWith("..") && !path.isAbsolute(baseRelative));
    assert.ok(baseRelative.split(path.sep).includes("python"), "PATH上のPythonではなく専用のmanaged Pythonを使います。");

    console.log("同じcacheとUV_OFFLINE=1で、引用したglobと同じ1引数のdocs/**/*.mdを検査します。");
    const offline = { ...env, UV_OFFLINE: "1" };
    const offlineStarted = performance.now();
    const second = jsonOutput(await run(process.execPath, [cli, "--rule", "preset-native-japanese", "--format", "json", "docs/**/*.md"],
        { env: offline, statuses: [0, 1] }));
    console.log(`offline再起動とglob検査: ${((performance.now() - offlineStarted) / 1000).toFixed(2)}秒。`);
    assert.equal(second.length, 2);
    const ordered = [0, 1].map(i => second.find(result => path.basename(result.filePath) === `${i}.md`));
    assert.ok(ordered.every(Boolean));
    assert.deepEqual(messages(ordered[0]), messages(first[0]));
    for (let i = 0; i < texts.length; i++) {
        const direct = jsonOutput(await run(python, ["-E", "-m", "native_japanese", "lint",
            "--reference", path.join(assets, sourceManifest.reference.file), "--assets-dir", assets,
            path.join(project, "docs", `${i}.md`)], { cwd: path.join(installed, "backend/src"), env: offline }));
        assert.deepEqual(messages(ordered[i]), expectedMessages(direct));
    }
    assert.deepEqual(await findEnvironments(home), environments);
    console.log("managed CPython 3.12.13の初回準備、uvのoffline再利用、公開Python CLIと全message/rangeの一致を確認しました。");
} finally {
    await rm(temporary, { recursive: true, force: true });
}
