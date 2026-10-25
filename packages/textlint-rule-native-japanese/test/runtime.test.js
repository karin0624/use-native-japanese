import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createConnection } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { verifyAssets } from "../src/assets.js";
import { Worker, configuration } from "../src/worker.js";

const fixture = fileURLToPath(new URL("./fixtures/worker.js", import.meta.url));

test("command省略時は並列要求の準備を共有し、準備後から応答期限を数える", async t => {
    const directory = await mkdtemp(join(tmpdir(), "native-japanese-runtime-"));
    let prepared = 0;
    const worker = new Worker(configuration(), {
        timeoutMs: 300,
        prepare: async () => {
            prepared++;
            await new Promise(resolve => setTimeout(resolve, 400));
            return { command: [process.execPath, fixture, "worker.log"], cwd: directory };
        }
    });
    t.after(async () => { worker.close(); await rm(directory, { recursive: true, force: true }); });
    const results = await Promise.all([worker.request(["雲を食べる"]), worker.request(["雲を食べる"])]);
    assert.equal(prepared, 1);
    assert.deepEqual(results.map(result => result[0].diagnostics.length), [1, 1]);
});

test("自動準備の失敗を呼出元へ返し、次の要求で再試行する", async t => {
    const directory = await mkdtemp(join(tmpdir(), "native-japanese-runtime-"));
    let prepared = 0;
    const worker = new Worker(configuration(), {
        prepare: async () => {
            if (++prepared === 1) throw new Error("人工的な準備失敗");
            return { command: [process.execPath, fixture, join(directory, "worker.log")] };
        }
    });
    t.after(async () => { worker.close(); await rm(directory, { recursive: true, force: true }); });
    const first = await Promise.allSettled([worker.request(["文"]), worker.request(["文"])]);
    assert.ok(first.every(result => result.status === "rejected" && /人工的な準備失敗/.test(result.reason.message)));
    assert.equal((await worker.request(["雲を食べる"]))[0].diagnostics.length, 1);
    assert.equal(prepared, 2);
});

test("資源の実体は通し、LFS pointerと同サイズの改変を拒否する", async t => {
    const directory = await mkdtemp(join(tmpdir(), "native-japanese-assets-"));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const reference = Buffer.from("参照の人工fixture");
    const model = Buffer.from("モデルの人工fixture");
    const entry = (file, payload) => ({ file, bytes: payload.length,
        sha256: createHash("sha256").update(payload).digest("hex"), license: "人工fixture" });
    await writeFile(join(directory, "manifest.json"), JSON.stringify({ schemaVersion: 1,
        reference: entry("reference.sqlite3", reference), model: entry("fasttext-ja-mini", model) }));
    await writeFile(join(directory, "reference.sqlite3"), reference);
    await writeFile(join(directory, "fasttext-ja-mini"), model);
    await verifyAssets(directory);
    await writeFile(join(directory, "fasttext-ja-mini"), "version https://git-lfs.github.com/spec/v1\n");
    await assert.rejects(verifyAssets(directory), /サイズ.*Git LFS/);
    await writeFile(join(directory, "fasttext-ja-mini"), Buffer.alloc(model.length));
    await assert.rejects(verifyAssets(directory), /SHA-256/);
});

test("親がSIGTERMで終了すると子を回収し、親の終了signalを保つ", {
    skip: process.platform === "win32", timeout: 5_000
}, async t => {
    const childCode = `import { createServer } from 'node:net';
        createServer().listen(0, '127.0.0.1', function () { process.stderr.write(String(this.address().port)); });`;
    const parentCode = `import { once } from 'node:events';
        import { Worker, configuration } from ${JSON.stringify(new URL("../src/worker.js", import.meta.url).href)};
        const worker = new Worker(configuration({ command: [process.execPath, '--input-type=module', '-e', ${JSON.stringify(childCode)}] }));
        await worker.start();
        const [port] = await once(worker.child.stderr, 'data');
        process.send({ pid: worker.child.pid, port: Number(port) });
        setInterval(() => {}, 1000);`;
    const parent = spawn(process.execPath, ["--input-type=module", "-e", parentCode], {
        stdio: ["ignore", "ignore", "inherit", "ipc"]
    });
    let childPid;
    t.after(() => {
        parent.kill("SIGKILL");
        if (childPid) { try { process.kill(childPid, "SIGKILL"); } catch {} }
    });
    const [{ pid, port }] = await once(parent, "message");
    childPid = pid;
    const listening = () => new Promise(resolve => {
        const socket = createConnection({ host: "127.0.0.1", port });
        socket.once("connect", () => { socket.destroy(); resolve(true); });
        socket.once("error", () => resolve(false));
    });
    assert.equal(await listening(), true);
    const exited = once(parent, "exit");
    parent.kill("SIGTERM");
    assert.deepEqual(await exited, [null, "SIGTERM"]);
    // zombieのPIDが残るOS環境でも、生きた子のsocketが残っていないことを確かめる。
    const deadline = Date.now() + 1000;
    while (await listening()) {
        assert.ok(Date.now() < deadline, "親の終了後も子が動作しています。");
        await new Promise(resolve => setTimeout(resolve, 20));
    }
});
