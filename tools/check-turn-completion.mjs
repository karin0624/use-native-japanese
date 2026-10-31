import { readFileSync } from "node:fs";

// 最終本文から照合候補を拾う。依頼の達成や、停止理由の正当性は判定しない。
const rules = [
    ["未確認・未完了", /未(?:確認|確定|調査|検討|検証|実施|実装|完了|完成|解決|着手|対応|取得|実行|評価|測定|比較|判定)|(?:確認|確定|調査|検討|検証|実施|実装|完了|完成|解決|着手|対応|取得|実行|評価|測定|比較|判定|証明)(?:は|を)?(?:まだ)?(?:して|できて|されて|行って)(?:いません|いない|おりません|おらず)|調べて(?:いません|いない)/gu],
    ["未確認・未完了", /(?:実証|立証|修正|変更)(?:は|を)?(?:まだ)?(?:して|できて|されて|行って)(?:いません|いない|おりません|おらず)|(?:確認|調査|検討|検証|実施|実装|修正|変更|取得|評価|測定|比較|判定|実証|立証|証明)(?:すべき|が必要な)(?:点|事項|項目|箇所|部分|作業)(?:が|は)(?:まだ)?残(?:っています|っている|っており)|(?:完了|完成|解消|解決)(?:した|済み)(?:(?:という|との)?扱い(?:には|に|は)?(?:していません|していない)|とは(?:扱いません|扱わない|扱っていません|扱っていない|言えません|言えない))/gu],
    ["判断材料の不足", /(?:根拠|原因|方法|設計)(?:が|は|も)?(?:まだ)?(?:不明|分かりません|わかりません|分かっていません|わかっていません|分からない|わからない)|(?:根拠|原因|方法|設計)(?:を|は)(?:確認|特定|確定)(?:できていません|できていない)|(?:根拠|調査|検討|検証|設計|評価|比較)(?:が|は)?(?:まだ)?不十分/gu],
    ["判断材料の不足", /不足(?:は|が)(?:まだ)?残(?:っています|っている|っており)/gu],
    ["作業の持ち越し", /(?:調査|設計|確認|検証|実装|対応|判断)(?:は|を)?(?:後回し|持ち越し|保留)|(?:調査|検討|検証|確認|設計|実装|評価|測定|比較|判定)(?:が|は)(?:まだ)?必要|(?:次に|後ほど)(?:調べ|調査|検討|検証|確認|設計|実装|対応|評価|測定|比較|判定)|(?:次は|次回|後で|必要なら|必要であれば|ご希望(?:なら|であれば)|続けて(?:と|の)?(?:言って|指示|依頼))/gu],
    ["作業の持ち越し", /(?:確認|調査|検討|検証|実施|実装|修正|変更|取得|評価|測定|比較|判定|実証|立証|証明)(?:(?:(?:を|は)?(?:続け|継続し)|を実施し|し)ます|しています|しております|中(?=です|であり|の状態|[。！!、,\s]|$))/gu],
    ["訂正・記録での終了", /(?:誤り|間違い)(?:でした|がありました)|(?:案|提案|説明|判断)(?:は|を)?撤回|(?:Issue|イシュー|課題)(?:に|へ)(?:記録|登録|起票)|(?:記録|登録|起票)(?:だけ|のみ)/gu],
    ["実験後の判断保留", /(?:採用|採否|選定)(?:の)?(?:判断|結論)?(?:は|を|が)?(?:まだ|保留|未定|残って|残し)|(?:実験|試行|計測)(?:だけ|のみ)(?:で|を)/gu]
];

function isResolved(match, rest) {
    // 一致箇所のすぐ後ろだけを見る。別件の完了報告で残作業を打ち消さない。
    if (/^(?:の?(?:事項|項目|箇所|部分|点))?(?:は|が)?(?:ありません|ない(?:です)?|なし|ゼロ|0件)(?:[。！!、,\s]|$)/u.test(rest)) return true;
    if (/^(?:では|じゃ)(?:ありません|ない|なく)/u.test(rest)) return true;
    if (/^を解消(?:済み|しました)/u.test(rest)) return true;
    const state = /^未(確認|確定|調査|検討|検証|実施|実装|完了|完成|解決|着手|対応|取得|実行|評価|測定|比較|判定)$/u.exec(match)?.[1];
    if (!state) return false;
    return new RegExp(`^(?:だった|であった)(?:事項|項目|箇所|部分|点)?(?:は|を)(?:現在は|今は)?${state}(?:済み|しました|できました)`, "u").test(rest);
}

function inspect(message) {
    const findings = new Map();
    let fence;
    for (const [index, line] of message.split(/\r?\n/u).entries()) {
        if (/^\s*>/u.test(line)) continue;
        const delimiter = /^\s{0,3}(`{3,}|~{3,})/u.exec(line)?.[1];
        if (fence) {
            if (delimiter?.[0] === fence[0] && delimiter.length >= fence.length && /^\s{0,3}(?:`+|~+)\s*$/u.test(line)) fence = undefined;
            continue;
        }
        if (delimiter) { fence = delimiter; continue; }
        const text = line.replace(/\*\*|__/gu, "");
        for (const [category, pattern] of rules) {
            for (const match of text.matchAll(pattern)) {
                if (isResolved(match[0], text.slice(match.index + match[0].length))) continue;
                if (!findings.has(category)) findings.set(category, new Set());
                findings.get(category).add(index + 1);
            }
        }
    }
    return findings;
}

function check(input) {
    if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("入力形式");
    if (typeof input.hook_event_name !== "string") throw new Error("イベント形式");
    if (input.hook_event_name !== "Stop") return {};
    if (input.last_assistant_message === null) return {};
    if (typeof input.last_assistant_message !== "string") throw new Error("本文形式");
    const findings = inspect(input.last_assistant_message);
    if (!findings.size) return {};
    const locations = [...findings].map(([category, lines]) => `${category}: ${[...lines].join("、")}行目`).join(" / ");
    return { systemMessage: `ターン終了前の照合候補があります（${locations}）。原依頼・有効な追加条件と、完了の証拠・残作業・次に実行可能な操作を照合してください。本文だけでは未達や停止理由の正当性を判定できません。正当な外部待ちや制約も含めて確認し、語句を削るだけで残作業を隠さないでください。` };
}

let output;
try {
    output = check(JSON.parse(readFileSync(0, "utf8")));
} catch {
    output = { systemMessage: "ターン終了前の照合に失敗しました。Hookの入力形式と実行環境を確認し、原依頼・完了の証拠・残作業を手動で照合してください。入力本文はログに出力していません。" };
}
process.stdout.write(`${JSON.stringify(output)}\n`);
