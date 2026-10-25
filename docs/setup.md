# セットアップ

Node.js 20.18以上を用意します。npm registryには未公開のため、`npm pack`で作成した`textlint-rule-native-japanese-0.1.0.tgz`を利用するプロジェクトに置き、textlintと一緒に導入します。

```sh
npm install --save-dev textlint ./textlint-rule-native-japanese-0.1.0.tgz
```

検査するファイル、またはパスを指定します。

```sh
npx textlint --rule native-japanese README.md
npx textlint --rule native-japanese "docs/**/*.md"
```

初回の検査では実行環境を自動で準備します。ネットワーク接続とダウンロード・展開のための時間、ディスク空き容量が必要です。準備した環境は利用者のcacheに保存し、次回から再利用します。モデルと参照DBはパッケージに同梱しています。

対象環境はLinux（glibc 2.28以上、x64・arm64）、macOS 13以上（x64・arm64）、Windows 10以上（x64）です。CIではLinux x64・macOS arm64・Windows x64で検証しています。具体的なOS版と未検証の範囲は[配布方針と検証](npm-distribution-plan.md)を参照してください。

検査範囲・指摘位置の仕様は[textlint接続](textlint.md)、開発環境や独自参照の構築は[開発者向け手順](development.md)を参照してください。
