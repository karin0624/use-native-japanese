import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";

// 配布前と起動前で同じ検証を行い、LFSのpointerも実体として扱わない。
export async function verifyAssets(directory) {
    const manifest = JSON.parse(await readFile(join(directory, "manifest.json"), "utf8"));
    if (manifest.schemaVersion !== 1) throw new Error("native-japanese: モデル・用例データの形式に対応していません（manifest.jsonのschemaVersionは1が必要です）。");
    for (const [kind, file] of [["reference", "reference.sqlite3"], ["model", "fasttext-ja-mini"]]) {
        const item = manifest[kind];
        if (item?.file !== file || !Number.isSafeInteger(item.bytes) || item.bytes < 1 ||
            !/^[a-f0-9]{64}$/.test(item.sha256) || typeof item.license !== "string" || !item.license) {
            throw new Error(`native-japanese: manifest.jsonの${kind}に必要な項目がないか、値の形式が正しくありません（file、bytes、sha256、license）。`);
        }
        const path = join(directory, file);
        if ((await stat(path)).size !== item.bytes) {
            throw new Error(`native-japanese: ${file}のサイズがmanifest.jsonに記録された値と一致しません。`);
        }
        const hash = createHash("sha256");
        for await (const chunk of createReadStream(path)) hash.update(chunk);
        if (hash.digest("hex") !== item.sha256) {
            throw new Error(`native-japanese: ${file}のSHA-256ハッシュ値がmanifest.jsonの記録と一致しません。`);
        }
    }
}
