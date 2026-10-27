# セットアップ

## 前提

Node.js 20.18以上と、配布された`textlint-rule-preset-native-japanese-0.1.0.tgz`を用意します。npm registryには未公開です。

依存パッケージの配布対象は次の環境です。最低OS版・全CPUでの動作確認を意味しません。CIの実行対象は[workflow](../../.github/workflows/npm-package.yml)を参照してください。

| OS | 条件・CPU |
| --- | --- |
| Linux | glibc 2.28以上、x64・arm64 |
| macOS | 13以上、x64・arm64 |
| Windows | 10以上、x64 |

初回はネットワーク接続と、依存の取得・展開用のディスク空き容量が必要です。Pythonは自動準備されます。

## 手順

利用先プロジェクトのルートで実行します。

```sh
npm install --save-dev textlint ./textlint-rule-preset-native-japanese-0.1.0.tgz
npx textlint --rule preset-native-japanese README.md
npx textlint --rule preset-native-japanese "docs/**/*.md"
```

## 結果

実行環境は利用者のcacheに保存され、次回から再利用されます。モデルと参照DBはパッケージに同梱されています。

指摘と終了値は[textlint仕様](../reference/textlint.md)を参照してください。

## 問題への対処

`No rules found`の場合は、実行場所と`--rule preset-native-japanese`の指定を確認します。環境準備の失敗は、表示された取得先・空き容量・通信のエラーを確認してください。

このリポジトリのソースを検証する場合は、[開発手順](development.md)を使います。
