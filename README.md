# 日本語の語彙選択・連語誤用の検出

「重複を畳む」のような語の使い方を用例に照らし、確認が必要な名詞・述語を原文位置付きで返すtextlintプリセットです。[公式GiNZAの公開文節API](docs/architecture.md#原文から位置付き指摘まで)と軽量モデル（NER除外）、Bloom集合索引、名詞群の平均を使います。訂正案は未実装です。

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

以下を順に実行します。`cd use-native-japanese`で入るリポジトリのルート（`package.json`と`.textlintrc.cjs`のあるディレクトリ）で、Git LFSの資源実体と開発依存を準備します。

```sh
git clone https://github.com/karin0624/use-native-japanese.git
cd use-native-japanese
git lfs install
git lfs pull
npm ci
npm exec --no -- uv sync --locked --extra setup --managed-python --python 3.12.13
npx textlint README.md
npx textlint "docs/**/*.md"
```

リポジトリの設定により、checkoutのJavaScriptとPythonソースを直接使います。変更後にtextlintを起動し直すと反映されます。tgzの作成・再導入は不要です。詳細とテストは[開発手順](docs/development.md#開発環境)を参照してください。

## 検査の性質と検証範囲

用例の支持がある関係は指摘を返しません。Bloom索引が陽性、または同じ述語・関係条件の用例名詞群との類似度が0.35以上なら支持ありとします。どちらの根拠もない関係は、書き手に表現の確認を求める通常の`error`として返します。ベクトルを得られない場合は数値を0に置き換えず、名詞一般化による追加支持がないものとして扱います。これは日本語全体での不存在の証明ではありません。`indexed`は近似索引の一致で、低確率の誤通過があります。類似度は正誤確率ではありません。

結果は、支持ありで正常無出力、支持なしで文章への`error`、解析や資源破損などの処理異常で`systemerror`の三つです。textlintの終了値は指摘なしなら0、文章への`error`があれば1です。処理異常も失敗として終了します。

全件正答や未収録語の解消を完成条件にせず、所定形式の情報追加で改善でき、費用を現実的に増やせることを目指します。正用同士の意味差、文章のもっともらしさ、AI生成判定は対象外です。

[現在のv10設計](docs/design.md)では、連続英数字と英字等から始まる識別子について、元の解析で分かれた範囲だけを解析前にまとめます。元の述語・名詞が属する文節から、機能表現を示す位置分類または助動詞・格・繋辞の公開依存関係と、句読点・空白を除いた語彙を比較します。同文節の修飾も条件に残る保守的な比較です。直接集合のBloomと名詞群平均・0.35は再検証する仮説として維持します。

[v10の限定検証](docs/projection-validation.md)では、新規用例なしで同じ6,463本文から参照を再構築しました。既存8本文を戻す比較で、136用法・22群が増え、別記事の「世界的／大ヒット」への位置付き指摘が解消しました。一方、教師2対では訂正側も指摘され、原語取得や表記・ベクトルの限界が残ります。全体精度や全入力のリアルタイム性は未証明で、最終資源での製品接続試験は成功しました。対応3OSの導入・実行は[PRのCI](.github/workflows/npm-package.yml)で検査し、その実行結果を参照します。

- [textlint接続と検査範囲](docs/textlint.md)／[配布資源の出自と利用条件](packages/textlint-rule-preset-native-japanese/THIRD_PARTY_NOTICES.md)
- [開発・資源構築](docs/development.md)／[Python API・CLI・用例形式](docs/runtime.md)
- [現在の設計・検証条件](docs/design.md)／[構成と容量](docs/architecture.md)
- [v10の再構築・限定評価](docs/projection-validation.md)
- [旧三結果契約・全量再生成・実行経路の検証](docs/three-result-validation.md)
- [旧契約での自然参照の容量・速度・用例追加・実接続の検証結果](docs/compact-runtime-validation.md)
- [旧KNP方式の検証記録](docs/runtime-validation.md)は履歴です。旧方式の成績・遅延を現方式の結果へ転用しません。
