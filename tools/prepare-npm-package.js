import { cp, mkdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { verifyAssets } from "../packages/textlint-rule-native-japanese/src/assets.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const packageRoot = join(root, "packages/textlint-rule-native-japanese");
await verifyAssets(join(packageRoot, "assets"));
const backend = join(packageRoot, "backend");
await rm(backend, { recursive: true, force: true });
await mkdir(join(backend, "src"), { recursive: true });
for (const file of ["pyproject.toml", "uv.lock"]) await cp(join(root, file), join(backend, file));
await cp(join(root, "src/native_japanese"), join(backend, "src/native_japanese"), {
    recursive: true,
    filter: path => !path.includes("__pycache__") && !/\.py[co]$/.test(path)
});
