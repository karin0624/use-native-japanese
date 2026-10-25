# 日本語の語彙選択・連語誤用の検出

「重複を畳む」のような語の使い方を用例に照らし、確認が必要な名詞・述語を原文位置付きで返すtextlintプリセットです。GiNZAの公開文節API（NER除外）、Bloom集合索引、名詞群の平均を使います。訂正案は未実装です。

Node.js 20.18以上を使います。他のプロジェクトでルールを利用する場合と、このリポジトリをcloneして開発・検証する場合で、準備が異なります。

## 他のプロジェクトで使う

npm registryには未公開です。配布された`textlint-rule-preset-native-japanese-0.1.0.tgz`を利用先のプロジェクトに置き、そのルートで導入・実行します。

```sh
npm install --save-dev textlint ./textlint-rule-preset-native-japanese-0.1.0.tgz
npx textlint --rule preset-native-japanese README.md
npx textlint --rule preset-native-japanese "docs/**/*.md"
```

モデルと参照DBを同梱し、Python環境は初回に自動準備します。ネットワーク接続、時間、ディスク空き容量が必要です。準備した環境は再利用します。対象OSなどは[セットアップ](docs/setup.md#他のプロジェクトで使う)を参照してください。

## cloneして開発・検証する

cloneしたリポジトリのルートで、Git LFSの資源実体と開発依存を準備します。

```sh
git lfs install
git lfs pull
npm ci
npm exec --no -- uv sync --locked --extra setup --managed-python --python 3.12.13
npx textlint README.md
npx textlint "docs/**/*.md"
```

リポジトリの設定により、checkoutのJavaScriptとPythonソースを直接使います。変更後にtextlintを起動し直すと反映されます。tgzの作成・再導入は不要です。詳細とテストは[開発手順](docs/development.md#開発環境)を参照してください。

## 検査の性質と検証範囲

`indexed`は近似索引の一致で、低確率の誤通過があり、実用例の直接確認を意味しません。陰性だけでは語の使い方を指摘せず、比較可能な群の支持が暫定境界0.35未満なら確認を促します。比較不能は理由付きで残します。支持は正誤確率ではありません。

語の使い方への指摘はtextlintの`error`、辞書・解析の対応不足は`warning`として表示します。前者は検査が正常に完了した結果で、誤用を確定するものではありません。後者は文章の修正を求めるものではなく、未収録の用例が日本語全体に存在しないという意味でもありません。`warning`だけなら終了値は0、`error`を含むと1です。例外や壊れたデータによる処理失敗は、検査を完了できなかった異常として区別します。

全件正答や未収録語の解消を完成条件にせず、所定形式の情報追加で改善でき、費用を現実的に増やせることを目指します。正用同士の意味差、文章のもっともらしさ、AI生成判定は対象外です。

固定38本文では、別出典追加で「世界的な大ヒット」の比較不能を1件解消しましたが、指摘位置は不変でした。「重複を畳んでいる」は今回の小規模参照に該当群がなく保留です。短文6種の反復は18〜182ミリ秒、初期化と初回には別途秒単位の費用がかかります。一般の検出精度や全入力のリアルタイム性を保証する結果ではありません。

- [textlint接続と検査範囲](docs/textlint.md)／[配布資源の出自と利用条件](packages/textlint-rule-preset-native-japanese/THIRD_PARTY_NOTICES.md)
- [開発・資源構築](docs/development.md)／[Python API・CLI・用例形式](docs/runtime.md)
- [現在の採用方針・検証範囲](docs/compact-runtime-plan.md)／[構成と容量](docs/architecture.md)
- [自然参照の容量・速度・用例追加・実接続の検証結果](docs/compact-runtime-validation.md)
- [旧KNP方式の検証記録](docs/runtime-validation.md)は履歴です。旧方式の成績・遅延を現方式の結果へ転用しません。
