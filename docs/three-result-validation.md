# 三結果契約と参照再生成の検証

2026-10-25。[課題8](https://github.com/karin0624/use-native-japanese/issues/8)の現行契約と、[課題11](https://github.com/karin0624/use-native-japanese/issues/11)のclone後の実行経路を確認した。旧契約の[自然参照検証](compact-runtime-validation.md)と[配布物検証](npm-distribution-plan.md#配布物の検証結果)は当時の履歴として保持する。

## 契約と固定条件

用例の支持があれば正常無出力、支持がなければ書き手への通常の`error`、解析や資源破損などの処理異常は`systemerror`とする。Bloom陽性、または同じ述語・関係条件の用例名詞群との類似度0.35以上を支持とする。ベクトルがない場合は類似度0を捏造せず、追加支持なしとして扱う。辞書不足warningや第四の保留状態は返さない。

GiNZAは`5.3.0+nativejapanese.1`、正規化契約は`ginza-bunsetu-lemma-no-ner-v2`、参照schemaは`2`。既存の句探索を元Tokenから開始できる最小修正であり、既定pipelineの85本文での比較と再作成手順は[vendorの記録](../vendor/ginza/README.md)を参照する。Sudachiの形態素辞書と、名詞一般化用の既存圧縮fastTextを使い、未知語辞書や語例ルールは追加していない。

原文入力は従来と同じ1,025記事・6,463区間で、記事ID・区間ID・題名・URLと出典manifestは不変。圧縮モデルのbytesも不変である。結果に合わせた本文の再選別はしていない。

## 全量再抽出と照合

全6,463本文を新契約で解析し、出典付き抽出・import・compileで参照を再生成した。正規化識別子を除いて比較すると、従来の76,069関係とその照会・端点は保存され、既知のphrase欠落36件が復元された。変更はその36区間だけで、残る6,427区間の照会・端点は不変だった。

| 項目 | 再生成結果 |
| --- | ---: |
| 関係の出現数 | 76,105 |
| 異なる照会条件 | 68,976（従来68,942から34増） |
| 名詞一般化の群数 | 19,019 |
| 配布参照DB | 70,889,472 bytes |

参照DBのSHA-256は`410f79df11d8663f924fc92f9ac9a8ecb9958b40395c1b68c50e41557d22176a`。資源manifestと出自資料の生成結果が一致し、SQLiteの`integrity_check`は`ok`だった。原文と構築用正本は配布物へ追加していない。

保存済みの新解析結果を製品の照合経路へ全件入力すると、76,105照会すべてが`indexed`、診断0、処理異常0だった。参照の構築に使った本文を同じ参照へ照合する接続検査であり、日本語の検出精度を測ったものではない。

## 固定38本文と用例追加

固定38本文・356照会は照会条件と端点が不変だった。従来の`indexed`30件、`supported`80件はstatusとscoreを維持し、従来の`review`79件も維持した。旧`unavailable`167件は新契約に従い`review`へ移り、合計246件となった。これは支持のない関係を文章へのerrorとする契約変更であり、精度向上を示す数値ではない。比較できない値をscore 0へ置き換えてはいない。

従来と同じ別出典の8区間を追加すると、「世界的／大ヒット」が`review`から`indexed`となり、名詞・述語の2端点の指摘が消えた。追加分は140記録・126種類の新しい照会条件・14群で、同じ記録の再importでは用法・出典対応とも増加0だった。所定形式の情報追加が照合結果へ反映されることを確認した。

保存量の増え方は従来の`O(N log(1/p) + Gd + L)`から変わらない。今回の全量解析は`nlp.pipe`のbatch 1000で156.45秒、peak RSS 7,189,635,072 bytesだった。旧測定のbatch 64と条件が異なるため、時間差を性能改善とは扱わない。これは参照構築時の測定で、利用者の短文検査の実行時間ではない。

## 機械検証とclone後の実行

[開発手順](development.md#開発環境)で環境を準備し、次を実行した。

```sh
npm test
npm exec --no -- uv run --no-sync pytest -q
npm exec --no -- uv run --no-sync ruff check src tests tools
node --test tools/test-source-checkout.mjs
npx textlint README.md
```

Pythonは105 testsと19 subtests、Nodeは22件、checkout接続は2件が成功し、Ruffも成功した。2段階のfresh contextレビューで指摘はなかった。READMEへの実textlintは通常の文章へのerrorを返し、warning、`No rules found`、systemerrorはなかった。本文の編集で指摘数が変わるため件数は固定しない。

課題11の失敗時の実行ログ・cwdは残っておらず、その原因は断定できない。改修前のremote HEADからの新規cloneと、現在の作業repo rootでは、標準の依存準備後に`npx textlint README.md`が実行できた。一方、設定のないcwdやプリセット未導入の状態では同じ`No rules found`表示を再現した。READMEとsetupは`git clone`と`cd use-native-japanese`から始め、実行するrepo rootを明示した。

新配布物のpackとLinuxでの導入検査も成功した。リポジトリ外の別プロジェクトへ導入し、空の専用cacheからの初回検査、準備済み環境での`UV_OFFLINE=1`による再検査、公開Python CLIとの全メッセージ・UTF-16範囲の一致を確認した。資源実体・GiNZA wheelのhashと帰属資料を確認し、原コーパス・構築用正本・cacheの同梱がないことも検査した。

この記録の時点で新しいremote HEADからのcloneと3 OS CIの最終確認は進行中であり、完了結果には含めない。独立した正誤ラベルによる精度・誤指摘率の改善や、日本語全体での用法不存在を示す検証ではない。
