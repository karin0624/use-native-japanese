# セットアップ

Node.js 20.18以上を用意します。他のプロジェクトでの利用と、このリポジトリをcloneした開発・検証では、準備と実行ディレクトリが異なります。

## 他のプロジェクトで使う

npm registryには未公開です。配布された`textlint-rule-preset-native-japanese-0.1.0.tgz`を利用先のプロジェクトに置き、そのプロジェクトのルートでtextlintと一緒に導入します。

```sh
npm install --save-dev textlint ./textlint-rule-preset-native-japanese-0.1.0.tgz
```

検査するファイル、またはパスを指定します。

```sh
npx textlint --rule preset-native-japanese README.md
npx textlint --rule preset-native-japanese "docs/**/*.md"
```

語の使い方への指摘は`error`、辞書・解析の対応不足は`warning`として表示されます。`warning`だけなら終了値は0、`error`があれば1です。指摘の意味と、検査自体の失敗との違いは[指摘の読み方](textlint.md#指摘の読み方)を参照してください。

初回の検査では実行環境を自動で準備します。ネットワーク接続とダウンロード・展開のための時間、ディスク空き容量が必要です。準備した環境は利用者のcacheに保存し、次回から再利用します。モデルと参照DBはパッケージに同梱しています。利用者がPythonを準備する必要はありません。

対象環境はLinux（glibc 2.28以上、x64・arm64）、macOS 13以上（x64・arm64）、Windows 10以上（x64）です。CIではLinux x64・macOS arm64・Windows x64で検証しています。具体的なOS版と未検証の範囲は[配布方針と検証](npm-distribution-plan.md)を参照してください。

## cloneして開発・検証する

GitとGit LFSを用意し、cloneしたリポジトリのルートで実行します。

```sh
git lfs install
git lfs pull
npm ci
npm exec --no -- uv sync --locked --extra setup --managed-python --python 3.12.13
npx textlint README.md
npx textlint "docs/**/*.md"
```

`npm ci`はworkspaceの開発依存を導入します。同梱依存のuvで固定Pythonと`.venv`を準備し、Pythonパッケージをeditableで導入します。Git LFSで取得した固定モデルと参照DBを使うため、通常の開発で参照生成やモデルの別途取得は不要です。初回の依存・資源取得にはネットワーク接続が必要です。

リポジトリの`.textlintrc.cjs`はcheckoutのJavaScriptと`.venv`からPythonソースを直接使います。変更後にtextlintを起動し直すと反映されます。ルール更新のために`npm install`をし直したり、tgzを作成・再導入したり、配布用backendをコピーしたりする必要はありません。テストは[開発手順](development.md#開発環境)を参照してください。

検査範囲・指摘位置の仕様は[textlint接続](textlint.md)、開発環境や独自参照の構築は[開発者向け手順](development.md)を参照してください。
