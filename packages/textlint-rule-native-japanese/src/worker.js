import { spawn } from "node:child_process";

const workers = new Map();
const children = new Set();
const MAX_OUTPUT = 16 * 1024 * 1024;

export function configuration(options) {
    if (!Array.isArray(options.command) || options.command.length === 0 ||
        options.command.some((part, i) => typeof part !== "string" || part.includes("\0") || (i === 0 && !part))) {
        throw new Error("native-japanese: commandには実行ファイル名と引数の文字列配列を指定してください。");
    }
    const config = { command: [...options.command] };
    const requestTimeoutMs = options.requestTimeoutMs === undefined ? 120_000 : options.requestTimeoutMs;
    if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1 || requestTimeoutMs > 2_147_483_647) {
        throw new Error("native-japanese: requestTimeoutMsには1〜2147483647ミリ秒の整数を指定してください。");
    }
    config.requestTimeoutMs = requestTimeoutMs;
    return config;
}

export class Worker {
    constructor(config, { idleMs = 30_000, timeoutMs = 120_000, onIdle = () => {} } = {}) {
        this.config = config;
        this.idleMs = idleMs;
        this.timeoutMs = timeoutMs;
        this.onIdle = onIdle;
        this.pending = new Map();
        this.sequence = 0;
        this.child = null;
    }

    start() {
        if (this.child) return;
        const [command, ...args] = this.config.command;
        const child = spawn(command, args, { shell: false, stdio: ["pipe", "pipe", "pipe"] });
        children.add(child);
        this.child = child;
        this.buffer = "";
        this.stderr = "";
        child.stdout.setEncoding("utf8");
        child.stderr.setEncoding("utf8");
        child.stdout.on("data", chunk => {
            if (this.child !== child) return;
            this.buffer += chunk;
            if (this.buffer.length > MAX_OUTPUT) {
                this.close(new Error("native-japanese: ワーカーの応答が上限を超えました。"));
                return;
            }
            let newline;
            while ((newline = this.buffer.indexOf("\n")) !== -1) {
                const line = this.buffer.slice(0, newline);
                this.buffer = this.buffer.slice(newline + 1);
                try { this.receive(JSON.parse(line)); }
                catch (error) { this.close(new Error(`native-japanese: 不正なワーカー応答: ${error.message}`)); return; }
            }
        });
        child.stderr.on("data", chunk => { if (this.child === child) this.stderr = (this.stderr + chunk).slice(-4096); });
        child.on("error", error => { if (this.child === child) this.close(new Error(`native-japanese: ワーカーを起動できません: ${error.message}`)); });
        child.stdin.on("error", error => { if (this.child === child) this.close(new Error(`native-japanese: ワーカーへの送信に失敗しました: ${error.message}`)); });
        child.on("close", (code, signal) => {
            children.delete(child);
            if (this.child === child) {
                this.close(new Error(`native-japanese: ワーカーが終了しました (${code ?? signal})。${this.stderr.trim()}`));
            }
        });
    }

    request(texts) {
        clearTimeout(this.idleTimer);
        this.start();
        this.reference(true);
        const id = String(++this.sequence);
        return new Promise((resolveRequest, reject) => {
            const timer = setTimeout(() => this.close(new Error("native-japanese: ワーカーの応答が時間内に返りませんでした。")), this.timeoutMs);
            this.pending.set(id, { resolve: resolveRequest, reject, timer, count: texts.length });
            this.child.stdin.write(`${JSON.stringify({ id, texts })}\n`);
        });
    }

    receive(response) {
        const pending = this.pending.get(response?.id);
        if (!pending) throw new Error("要求IDが一致しません。");
        if (response.error !== undefined && (typeof response.error?.message !== "string" || response.results !== undefined)) {
            throw new Error("処理エラーの形式が不正です。");
        }
        if (!response.error && (!Array.isArray(response.results) || response.results.length !== pending.count)) {
            throw new Error("結果の件数が要求と一致しません。");
        }
        clearTimeout(pending.timer);
        this.pending.delete(response.id);
        if (response.error) pending.reject(new Error(`native-japanese: ${response.error.message ?? "ワーカーの処理に失敗しました。"}`));
        else pending.resolve(response.results);
        if (this.pending.size === 0) {
            this.reference(false);
            this.idleTimer = setTimeout(() => { this.close(); this.onIdle(); }, this.idleMs);
            this.idleTimer.unref();
        }
    }

    reference(active) {
        for (const handle of [this.child, this.child?.stdin, this.child?.stdout, this.child?.stderr]) {
            handle?.[active ? "ref" : "unref"]?.();
        }
    }

    close(error = new Error("native-japanese: ワーカーを終了しました。")) {
        clearTimeout(this.idleTimer);
        for (const pending of this.pending.values()) {
            clearTimeout(pending.timer);
            pending.reject(error);
        }
        this.pending.clear();
        const child = this.child;
        this.child = null;
        if (child) {
            child.stdin.destroy();
            child.kill();
            const force = setTimeout(() => child.kill("SIGKILL"), 1000);
            force.unref();
            child.once("close", () => clearTimeout(force));
            for (const handle of [child, child.stdout, child.stderr]) handle?.unref?.();
        }
    }
}

export function getWorker(config) {
    const key = JSON.stringify(config);
    if (!workers.has(key)) {
        workers.set(key, new Worker(config, { timeoutMs: config.requestTimeoutMs, onIdle: () => workers.delete(key) }));
    }
    return workers.get(key);
}

// 親の終了時は、終了処理中の子も含め、タイマーに頼らず回収する。
process.once("exit", () => { for (const child of children) child.kill("SIGKILL"); });
