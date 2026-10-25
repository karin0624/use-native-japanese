# 日本語の語彙選択・連語誤用の検出

「重複を畳む」のような語の使い方を用例に照らし、確認が必要な名詞・述語を原文位置付きで返すtextlintルールです。GiNZAの公開文節API（NER除外）、Bloom集合索引、名詞群の平均を使います。訂正案は未実装です。

Node.js 20.18以上を使います。npm registryには未公開のため、`npm pack`で作成したtgzを利用するプロジェクトに置いて導入します。

```sh
npm install --save-dev textlint ./textlint-rule-native-japanese-0.1.0.tgz
npx textlint --rule native-japanese README.md
npx textlint --rule native-japanese "docs/**/*.md"
```

初回は実行環境の自動準備にネットワーク接続、時間、ディスク空き容量が必要です。モデルと参照DBは同梱し、準備した環境は再利用します。対象OSなどの前提は[セットアップ](docs/setup.md)を参照してください。

`indexed`は近似索引の一致で、低確率の誤通過があり、実用例の直接確認を意味しません。陰性だけでは警告せず、比較可能な群の支持が暫定境界0.35未満なら確認を促します。比較不能は理由付きで残します。支持は正誤確率ではありません。

全件正答や未収録語の解消を完成条件にせず、所定形式の情報追加で改善でき、費用を現実的に増やせることを目指します。正用同士の意味差、文章のもっともらしさ、AI生成判定は対象外です。

固定38本文では、別出典追加で「世界的な大ヒット」の比較不能を1件解消しましたが、指摘位置は不変でした。「重複を畳んでいる」は今回の小規模参照に該当群がなく保留です。短文6種の反復は18〜182ミリ秒、初期化と初回には別途秒単位の費用がかかります。一般の検出精度や全入力のリアルタイム性を保証する結果ではありません。

- [textlint接続と検査範囲](docs/textlint.md)／[配布資源の出自と利用条件](packages/textlint-rule-native-japanese/THIRD_PARTY_NOTICES.md)
- [開発・資源構築](docs/development.md)／[Python API・CLI・用例形式](docs/runtime.md)
- [現在の採用方針・検証範囲](docs/compact-runtime-plan.md)／[構成と容量](docs/architecture.md)
- [自然参照の容量・速度・用例追加・実接続の検証結果](docs/compact-runtime-validation.md)
- [旧KNP方式の検証記録](docs/runtime-validation.md)は履歴です。旧方式の成績・遅延を現方式の結果へ転用しません。
