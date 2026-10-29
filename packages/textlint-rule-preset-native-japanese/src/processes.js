import { spawn } from "node:child_process";
import { join } from "node:path";

// 環境準備・検査に伴う子孫も回収する。POSIXは専用process group、WindowsはOSのtree終了を使う。
export function terminateProcessTree(child, signal = "SIGTERM") {
    if (!child.pid) return;
    if (process.platform === "win32") {
        const command = join(process.env.SystemRoot ?? "C:\\Windows", "System32", "taskkill.exe");
        const terminator = spawn(command, ["/PID", String(child.pid), "/T", "/F"], {
            stdio: "ignore", windowsHide: true
        });
        terminator.on("error", () => child.kill(signal));
        terminator.unref();
        return;
    }
    try { process.kill(-child.pid, signal); }
    catch (error) { if (error.code !== "ESRCH") throw error; }
}
