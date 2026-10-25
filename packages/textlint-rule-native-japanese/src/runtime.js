import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { access, chmod, copyFile, mkdir, mkdtemp, readFile, rename, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { release } from "node:os";
import envPaths from "env-paths";
import { onExit } from "signal-exit";
import { verifyAssets } from "./assets.js";

const packageRoot = fileURLToPath(new URL("../", import.meta.url));
const PYTHON_VERSION = "3.12.13";
const PREPARE_TIMEOUT_MS = 10 * 60_000;
const children = new Set();
let preparation;

async function executableFromPackage(source, cache) {
    // wrapperがpostinstallで付ける実行権限を、必要なときだけ書込み可能なcache側で付ける。
    try { await access(source, constants.X_OK); return source; }
    catch (error) { if (error.code !== "EACCES") throw error; }
    const directory = join(cache, "uv-bin", `0.12.0-${process.platform}-${process.arch}`);
    await mkdir(directory, { recursive: true });
    const executable = join(directory, "uv");
    try { await access(executable, constants.X_OK); return executable; }
    catch (error) { if (!["EACCES", "ENOENT"].includes(error.code)) throw error; }
    const temporary = await mkdtemp(join(directory, ".prepare-"));
    try {
        const candidate = join(temporary, "uv");
        await copyFile(source, candidate);
        await chmod(candidate, 0o700);
        await rename(candidate, executable);
    } finally { await rm(temporary, { recursive: true, force: true }); }
    return executable;
}

function checkPlatform() {
    const { platform, arch } = process;
    let supported = ["x64", "arm64"].includes(arch) && ["linux", "darwin"].includes(platform);
    if (platform === "win32") supported = arch === "x64" && Number(release().split(".")[0]) >= 10;
    if (platform === "darwin") supported &&= Number(release().split(".")[0]) >= 22;
    if (platform === "linux") {
        const glibc = process.report.getReport().header.glibcVersionRuntime?.split(".").map(Number);
        supported &&= Boolean(glibc && (glibc[0] > 2 || (glibc[0] === 2 && glibc[1] >= 28)));
    }
    if (!supported) {
        throw new Error(`native-japanese: 自動準備の対応外です (${platform}/${arch})。Linux glibc 2.28以上・macOS 13以上のx64/arm64、Windows 10以上のx64が必要です。`);
    }
}

function syncEnvironment(command, args, env) {
    return new Promise((resolve, reject) => {
        const child = spawn(command, args, { env, shell: false, stdio: ["ignore", "ignore", "pipe"] });
        children.add(child);
        let stderr = "";
        let timedOut = false;
        child.stderr.setEncoding("utf8");
        child.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(-8192); });
        const timer = setTimeout(() => {
            timedOut = true;
            child.kill("SIGKILL");
            child.stderr.destroy();
            child.unref();
            reject(new Error("native-japanese: 実行環境の準備が10分以内に完了しませんでした。"));
        }, PREPARE_TIMEOUT_MS);
        child.once("error", error => {
            clearTimeout(timer);
            children.delete(child);
            reject(new Error(`native-japanese: 実行環境を準備できません: ${error.message}`));
        });
        child.once("close", code => {
            clearTimeout(timer);
            children.delete(child);
            if (timedOut) return;
            if (code !== 0) reject(new Error(`native-japanese: 実行環境の準備に失敗しました (${code})。${stderr.trim()}`));
            else resolve();
        });
    });
}

async function prepare() {
    checkPlatform();
    const backend = join(packageRoot, "backend");
    const assets = join(packageRoot, "assets");
    await verifyAssets(assets);
    const hash = createHash("sha256").update(`${PYTHON_VERSION}:uv0.12.0:${process.platform}:${process.arch}`);
    for (const file of ["pyproject.toml", "uv.lock"]) hash.update(await readFile(join(backend, file)));
    const cache = envPaths("native-japanese", { suffix: "" }).cache;
    const environment = join(cache, "environments", hash.digest("hex"));
    await mkdir(cache, { recursive: true });
    // npm側の固定optional binaryを直接使い、wrapperの孫processを残さない。
    const require = createRequire(import.meta.url);
    const uvRequire = createRequire(require.resolve("@dataiku/uv/package.json"));
    let uv;
    try {
        uv = uvRequire.resolve(`@dataiku/uv-${process.platform}-${process.arch}/bin/${process.platform === "win32" ? "uv.exe" : "uv"}`);
    } catch (error) {
        throw new Error("native-japanese: uvのOS別optional dependencyがありません。optional dependenciesを含めてnpm installしてください。", { cause: error });
    }
    if (process.platform !== "win32") uv = await executableFromPackage(uv, cache);
    await syncEnvironment(uv, ["--no-config", "sync", "--project", backend, "--locked", "--no-dev",
        "--no-install-project", "--managed-python", "--python", PYTHON_VERSION], {
        ...process.env,
        UV_PROJECT_ENVIRONMENT: environment,
        UV_PYTHON_INSTALL_DIR: join(cache, "python"),
        UV_CACHE_DIR: join(cache, "uv"),
        UV_NO_PROGRESS: "1"
    });
    const python = process.platform === "win32" ? join(environment, "Scripts/python.exe") : join(environment, "bin/python");
    return {
        command: [python, "-E", "-m", "native_japanese", "serve", "--reference", join(assets, "reference.sqlite3"), "--assets-dir", assets],
        cwd: join(backend, "src")
    };
}

export function prepareRuntime() {
    // 同時の文書検査は初回準備を共有する。失敗後は次の要求で再試行できる。
    preparation ??= prepare().catch(error => { preparation = undefined; throw error; });
    return preparation;
}

onExit(() => { for (const child of children) child.kill("SIGKILL"); });
