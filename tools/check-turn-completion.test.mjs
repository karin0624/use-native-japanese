import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const script = fileURLToPath(new URL("./check-turn-completion.mjs", import.meta.url));
const hooks = JSON.parse(readFileSync(new URL("../.codex/hooks.json", import.meta.url), "utf8"));
const payload = message => ({
    hook_event_name: "Stop", last_assistant_message: message, stop_hook_active: false,
    cwd: root, model: "example", permission_mode: "default", session_id: "example-session",
    transcript_path: null, turn_id: "example-turn"
});

function run(input, options = {}) {
    const result = spawnSync(process.execPath, [script], {
        input: typeof input === "string" ? input : JSON.stringify(input), encoding: "utf8", timeout: 5000,
        ...options
    });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr, "");
    const output = JSON.parse(result.stdout);
    // Stopの公開契約: systemMessageだけで警告し、blockや自動継続を要求しない。
    assert.ok(Object.keys(output).every(key => key === "systemMessage"));
    return output;
}

// 受入根拠: 未完了の表現・持ち越し・判断保留を照合候補として利用者へ示す。
// 入力はすべて独立した架空例であり、会話や監査対象の転載・改作ではない。
test("未確認・未完了の明示と否定形の報告を警告する", () => {
    for (const state of ["確認", "確定", "調査", "検討", "検証", "実施", "実装", "完了", "完成", "解決",
        "着手", "対応", "取得", "実行", "評価", "測定", "比較", "判定"]) {
        assert.match(run(payload(`展示用ライトの切替は未${state}です。`)).systemMessage, /未確認・未完了: 1行目/u);
    }
    for (const message of ["色の変更を確認していません。", "操作を検証できていない状態です。", "配色の理由を調べていません。",
        "通電の確認はしていません。", "点灯の検証をまだ行っていません。", "耐久性を証明できていない状態です。", "調光装置は完成していない状態です。"]) {
        assert.match(run(payload(message)).systemMessage, /未確認・未完了/u);
    }
});

test("判断材料不足、持ち越し、訂正・記録、実験後の判断保留を警告する", () => {
    const cases = [
        ["発光時間の根拠は不明です。", "判断材料の不足"],
        ["ちらつきの原因が分かっていません。", "判断材料の不足"],
        ["配線の方法はわかりません。", "判断材料の不足"],
        ["調光回路の設計が不明です。", "判断材料の不足"],
        ["電池寿命の根拠が不十分です。", "判断材料の不足"],
        ["調査は持ち越しです。", "作業の持ち越し"],
        ["色むらについて追加の調査が必要です。", "作業の持ち越し"],
        ["次は点滅周期を調べます。", "作業の持ち越し"],
        ["次に調べます。", "作業の持ち越し"],
        ["後ほど検証します。", "作業の持ち越し"],
        ["必要なら配線図を作ります。", "作業の持ち越し"],
        ["必要であれば実施できます。", "作業の持ち越し"],
        ["続けてと指示してください。", "作業の持ち越し"],
        ["配色の説明は誤りでした。", "訂正・記録での終了"],
        ["照明案を撤回します。", "訂正・記録での終了"],
        ["調光の課題をIssueに記録しました。", "訂正・記録での終了"],
        ["電球の採用判断は保留です。", "実験後の判断保留"],
        ["今日は点灯の実験だけで終了します。", "実験後の判断保留"]
    ];
    for (const [message, category] of cases) assert.ok(run(payload(message)).systemMessage.includes(category), message);
});

test("これからの作業・進行中・残件・完了否定を、完了報告と区別する", () => {
    const cases = [
        ["電球の寿命を実証できていません。", "未確認・未完了"],
        ["照明の安定性を立証できていない状態です。", "未確認・未完了"],
        ["点灯時間の上限を証明できていません。", "未確認・未完了"],
        ["配線の位置を修正していません。", "未確認・未完了"],
        ["点滅周期を変更していない状態です。", "未確認・未完了"],
        ["次の通電で検証します。", "作業の持ち越し"],
        ["電池の消耗について調査を続けます。", "作業の持ち越し"],
        ["照度の確認を継続します。", "作業の持ち越し"],
        ["色温度の確認を実施します。", "作業の持ち越し"],
        ["電源の仕様を確認しています。", "作業の持ち越し"],
        ["発熱の原因は調査中です。", "作業の持ち越し"],
        ["配線には確認すべき点が残っています。", "未確認・未完了"],
        ["照度データの取得不足は残っています。", "判断材料の不足"],
        ["照明図の設計が完成したという扱いにはしていません。", "未確認・未完了"],
        ["点滅の問題を全件解消したとは扱いません。", "未確認・未完了"]
    ];
    for (const [message, category] of cases) assert.ok(run(payload(message)).systemMessage.includes(category), message);
    for (const message of ["電源の仕様を確認しました。", "配線位置の修正を完了しました。", "照度データの不足はありません。",
        "全件の配線検査を完了しました。", "照明図のDraft PRを作成しました。"]) {
        assert.deepEqual(run(payload(message)), {}, message);
    }
});

test("警告にカテゴリ・原文行番号・照合指示を含め、本文は転載しない", () => {
    const body = "照明台を設置しました。\r\n\r\n調光ダイヤルは未検証です。";
    const { systemMessage } = run(payload(body));
    assert.match(systemMessage, /未確認・未完了: 3行目/u);
    assert.match(systemMessage, /原依頼.*完了の証拠・残作業/u);
    assert.match(systemMessage, /判定できません/u);
    assert.match(systemMessage, /語句を削るだけで残作業を隠さない/u);
    assert.ok(!systemMessage.includes("調光ダイヤル"));
    assert.deepEqual(run(payload("電球を交換し、点灯検査を完了しました。")), {});
    assert.deepEqual(run(payload("交換には新しい電球が必要です。")), {});
});

// 対照条件: 引用や例のコードは作業報告ではない。局所的な解消と別件の残作業を区別する。
test("コードfenceとblockquoteを除外し、後続の本文は検査する", () => {
    for (const fence of ["```", "~~~~"]) {
        const body = `> 電池残量は未確認です。\n${fence}text\n配線は未検証です。\n${fence}\n点灯しました。`;
        assert.deepEqual(run(payload(body)), {});
        assert.match(run(payload(`${body}\nスイッチは未確認です。`)).systemMessage, /6行目/u);
    }
    assert.deepEqual(run(payload("````text\n```\n配線は未検証です。\n````")), {});
});

test("明白な否定・対応する解消は除外し、別の未確認を消さない", () => {
    for (const body of ["未確認事項はありません。", "**未確認**事項はありません。", "__未実装__の箇所はなし。", "未検証の箇所はなし。", "原因不明ではありません。",
        "未確認だった箇所は確認済みです。", "未実装だった部分は実装しました。", "原因不明を解消しました。"]) {
        assert.deepEqual(run(payload(body)), {}, body);
    }
    for (const body of ["未確認事項はありません。\n\n電池残量は未検証です。",
        "未確認事項はありませんが、通電は未検証です。", "未実装だった部分は確認済みです。",
        "電池残量は未確認です。別の照明は確認済みです。"]) {
        assert.match(run(payload(body)).systemMessage, /未確認・未完了/u, body);
    }
});

test("本文がないStopと他イベントは静かに終了し、不正な入力は本文を出さず警告する", () => {
    for (const body of [null, ""]) assert.deepEqual(run(payload(body)), {});
    assert.deepEqual(run({ hook_event_name: "SessionStart" }), {});
    for (const input of ["{private-example", "null", "[]", "{}", payload(123), payload({}),
        { ...payload("点灯しました。"), hook_event_name: 123 }, { hook_event_name: "Stop" }]) {
        const { systemMessage } = run(input);
        assert.match(systemMessage, /照合に失敗/u);
        assert.ok(!systemMessage.includes("private-example"));
    }
});

// 公開契約: Hook commandの作業ディレクトリはsession cwd。Gitルートから解決する。
test("設定済みHook commandはルートと子ディレクトリから同じスクリプトを起動する", t => {
    const command = hooks.hooks.Stop[0].hooks[0];
    assert.equal(command.type, "command");
    assert.equal(command.async, undefined);
    const windows = process.platform === "win32";
    const executable = windows ? (process.env.ComSpec || "cmd.exe") : "/bin/sh";
    // Codex 0.163.0はcmd /Cに、外側を引用したcommandをraw_argで渡す。
    // Node既定のWindows引数escapeを止め、同じコマンドラインを検査する。
    // https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/hooks/src/engine/command_runner.rs
    // https://nodejs.org/docs/latest-v24.x/api/child_process.html#child_processspawnsynccommand-args-options
    const args = windows ? ["/C", `"${command.commandWindows}"`] : ["-lc", command.command];
    if (!windows) t.diagnostic("Windows cmdでの実行はWindows環境でのみ検証する。");
    for (const cwd of [root, fileURLToPath(new URL("../docs/guide/", import.meta.url))]) {
        const result = spawnSync(executable, args, {
            cwd, input: JSON.stringify(payload("照明の耐久性は未検証です。")), encoding: "utf8", timeout: 5000,
            windowsVerbatimArguments: windows
        });
        assert.equal(result.error, undefined);
        assert.equal(result.status, 0, result.stderr);
        assert.equal(result.stderr, "");
        assert.match(JSON.parse(result.stdout).systemMessage, /未確認・未完了/u);
    }
});
