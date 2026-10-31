# 開発環境と検査

## 前提

Node.js 20.18以上、Git、Git LFSを用意します。依存版は[pyproject.toml](../../pyproject.toml)・[uv.lock](../../uv.lock)・[package-lock.json](../../package-lock.json)で固定されています。

## 手順

```sh
git clone https://github.com/karin0624/use-native-japanese.git
cd use-native-japanese
git lfs install
git lfs pull
npm ci
npm exec --no -- uv sync --locked --managed-python --python 3.12.13
npm exec --no -- uv run --locked python -E -X utf8 -m native_japanese setup-parser
```

リポジトリのルートでcheckoutのソースを検査します。

```sh
npx textlint --plugin textlint-rule-preset-native-japanese README.md
npx textlint --plugin textlint-rule-preset-native-japanese "docs/**/*.md"
```

テストと静的検査を実行します。

```sh
npm test
npm exec --no -- uv run --locked python -E -X utf8 -m pytest
npm exec --no -- uv run --locked ruff check src tests tools
```

### Codexのターン終了時の警告

ローカルのCodex CLI 0.163.0で、[Hook設定](../../.codex/hooks.json)の同期`Stop`コマンドを利用できます。Node.jsとGitがCodexを起動する環境のPATHに必要です。リポジトリのルートを作業ディレクトリに指定して起動します。

```sh
codex -C /path/to/use-native-japanese
```

プロジェクトを信頼したうえで、`/hooks`からHookの定義を確認し、信頼する操作を行います。プロジェクト自体の信頼と、Hookの現在の定義に対する信頼は別に管理されます。リポジトリの設定だけでは自動的に信頼されません。[検査スクリプト](../../tools/check-turn-completion.mjs)も実行するコードとして確認してください。定義を変更した場合は`/hooks`で現在の状態を確認します。

Hookは最後のアシスタント本文にある次の表現を、ターン終了前の照合候補として警告します。

- 未確認・未確定・未調査・未検討・未検証・未実施・未実装・未完了・未完成・未解決・未着手・未対応・未取得・未実行・未評価・未測定・未比較・未判定と、確認・修正・実証などをしていない報告や、完了したという扱いを否定する表現。
- 根拠・原因・方法・設計が不明な状態、調査・検証などが不十分な状態、不足や確認すべき点が残っている報告。
- 作業の持ち越し、調査などをまだ必要とする状態、これからの作業宣言、進行中の報告、追加の依頼を待つ表現。
- 説明の誤り、案の撤回、Issueへの記録で終わる報告。
- 実験だけの終了や、採用判断の保留。

警告はカテゴリと本文の行番号を示し、原依頼・有効な追加条件と、完了の証拠・残作業・次に実行可能な操作の照合を促します。本文の抜粋は警告へ転載しません。警告が出た場合は、該当箇所と依頼を照らして残作業を確認します。語句を削るだけで対応を済ませないでください。正当な外部待ちや説明にも一致するため、未達や停止理由の正当性を断定するものではありません。

コードfenceと`>`で始まる引用行、一致箇所のすぐ後ろにある明白な否定・解消済みの表現は検査対象から除きます。別件の未確認は検査します。表現による検査なので、言い換えの見落としや誤警告があります。原依頼や会話全体、ツールの実行記録は読みません。未完了を伏せた完了宣言や、原要求からの範囲の縮小は検出できません。

正常な入力では、候補があれば`systemMessage`を持つJSON、なければ空のJSONオブジェクトを標準出力へ返します。終了コードは0で、ターンの終了を禁止したり、モデルを自動継続したりしません。入力JSONや本文の型が不正な場合も、本文をログへ出さず検査失敗の警告を返します。本文が`null`の場合と`Stop`以外のイベントは警告しません。

`npm test`は架空の入力による検査と、ルート・子ディレクトリからのHookコマンドの起動を含みます。POSIXではGitルートを取得してスクリプトの場所を解決します。Windowsでは`commandWindows`を使い、Windows環境の`npm test`でその起動を検査します。WindowsのCodexランタイムでの接続は未検証です。クラウドでのCodex実行はローカルHookに対応していないため、この設定を置いただけでは警告されません。

仕様は[Codex Hooks](https://developers.openai.com/codex/hooks)と、対象版の[Stop入力](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/hooks/schema/generated/stop.command.input.schema.json)・[Stop出力](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/hooks/schema/generated/stop.command.output.schema.json)を参照してください。

## 結果

[.textlintrc.cjs](../../.textlintrc.cjs)がcheckoutのJavaScriptとeditable導入したPythonを接続します。変更はtextlintの再起動で反映され、tgzの再導入は不要です。

KWJAのbase解析モデルは`setup-parser`で公式配布元から取得し、KWJAのcacheに保存されます。名詞比較用の固定ベクトルモデルと参照DBは`packages/textlint-rule-preset-native-japanese/assets`に配置されます。通常の検証で再構築は不要です。用例追加は[CLI仕様](../reference/runtime.md)を参照してください。

[CI](../../.github/workflows/npm-package.yml)は、checkoutの検査と、実tgzを別プロジェクトへ導入する検査を行います。後者は初回準備・環境再利用・Python CLIとの出力一致を確認します。

## 問題への対処

- LFSポインタが残る場合：`git lfs pull`で資源実体を取得します。
- Python依存が不足する場合：準備手順の`uv sync`を実行します。
- ルールが見つからない場合：`.textlintrc.cjs`のあるリポジトリルートで実行します。
- ターン終了時にHookの警告が出ない場合：ローカルのCodexで`/hooks`を開き、Hookの検出・有効状態と現在の定義に対する信頼を確認します。起動場所、PATHのNode.js・Git、[Hook設定](../../.codex/hooks.json)と検査スクリプトも確認します。
