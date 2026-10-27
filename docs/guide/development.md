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
npm exec --no -- uv sync --locked --extra setup --managed-python --python 3.12.13
```

リポジトリのルートでcheckoutのソースを検査します。

```sh
npx textlint README.md
npx textlint "docs/**/*.md"
```

テストと静的検査を実行します。

```sh
npm test
npm exec --no -- uv run --locked pytest
npm exec --no -- uv run --locked ruff check src tests tools
```

## 結果

[.textlintrc.cjs](../../.textlintrc.cjs)がcheckoutのJavaScriptとeditable導入したPythonを接続します。変更はtextlintの再起動で反映され、tgzの再導入は不要です。

固定モデルと参照DBは`packages/textlint-rule-preset-native-japanese/assets`に配置されます。通常の検証で再構築は不要です。用例追加は[CLI仕様](../reference/runtime.md)を参照してください。

[CI](../../.github/workflows/npm-package.yml)は、checkoutの検査と、実tgzを別プロジェクトへ導入する検査を行います。後者は初回準備・環境再利用・Python CLIとの出力一致を確認します。

## 問題への対処

- LFSポインタが残る場合：`git lfs pull`で資源実体を取得します。
- Python依存が不足する場合：準備手順の`uv sync`を実行します。
- ルールが見つからない場合：`.textlintrc.cjs`のあるリポジトリルートで実行します。
