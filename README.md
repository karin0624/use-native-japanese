# 不適切な日本語を位置と修正案で指摘するツール

AIなどが書いた不適切な日本語について、問題の位置と修正案を示すことを目的としています。

現在は、本文中の述語や名詞との組み合わせを用例と突き合わせる第1の解析機を開発しています。Python API・CLI・textlintから位置付きの指摘を返せますが、解析機は開発途中で、誤指摘・見逃しがあります。修正案の生成は未実装です。

述語解析機の改良に加え、名詞の一般的な使用、重複・冗長な表現、不要な否定を調べる解析機の追加を計画しています。各解析機の結果を、問題の位置と修正案を示す指摘へまとめます。開発計画・受入条件・未確定事項は[ロードマップ #38](https://github.com/karin0624/use-native-japanese/issues/38)で管理します。

## 利用開始

導入は[セットアップ](docs/guide/setup.md)を参照してください。

## 文書

- [開発環境と検査](docs/guide/development.md)
- [設定と検査範囲](docs/reference/textlint.md)
- [Python・CLI・用例形式](docs/reference/runtime.md)
- [仕組みと先行研究](docs/README.md)
- [配布資源の出自・利用条件](packages/textlint-rule-preset-native-japanese/THIRD_PARTY_NOTICES.md)
