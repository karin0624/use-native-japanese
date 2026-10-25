import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";

// 配布前と起動前で同じ検証を行い、LFSのpointerも実体として扱わない。
export async function verifyAssets(directory) {
    const manifest = JSON.parse(await readFile(join(directory, "manifest.json"), "utf8"));
    if (manifest.schemaVersion !== 1) throw new Error("native-japanese: 資源manifestの形式が不正です。");
    for (const [kind, file] of [["reference", "reference.sqlite3"], ["model", "fasttext-ja-mini"]]) {
        const item = manifest[kind];
        if (item?.file !== file || !Number.isSafeInteger(item.bytes) || item.bytes < 1 ||
            !/^[a-f0-9]{64}$/.test(item.sha256) || typeof item.license !== "string" || !item.license) {
            throw new Error(`native-japanese: ${kind}の資源manifestが不正です。`);
        }
        const path = join(directory, file);
        if ((await stat(path)).size !== item.bytes) {
            throw new Error(`native-japanese: ${file}のサイズが一致しません。Git LFSの実体が必要です。`);
        }
        const hash = createHash("sha256");
        for await (const chunk of createReadStream(path)) hash.update(chunk);
        if (hash.digest("hex") !== item.sha256) {
            throw new Error(`native-japanese: ${file}のSHA-256が一致しません。`);
        }
    }
}
