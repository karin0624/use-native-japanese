# 語の使い方の照合

## 目的と対象外

原文の語と関係を用例へ照合し、支持のない使い方を指摘します。リアルタイムの位置付き指摘を目標に、スクリプト・textlintから利用し、商用利用も想定します。

試作の完成条件は、所定形式の情報追加による改善と規模拡大、容量・構築・検索費用の増え方を示すことです。全件正答や全用例の収録は求めません。

正用同士の意味差、AI生成の判定、分野ごとの文体は扱いません。訂正候補は未実装です。

## 入出力

入力は日本語本文、出力は名詞側・述語側の原文範囲とメッセージです。

| 結果 | 出力 |
| --- | --- |
| 用例による支持あり | 指摘なし |
| 支持なし | 書き手への通常のerror |
| 解析・資源・通信などの処理異常 | systemerror |

支持なしは参照資料での判定であり、日本語全体での不存在を証明しません。

## 方式

### 語と関係

[Parser](../../src/native_japanese/parser.py)はGiNZAのC分割後、ASCIIの連続英数字・英字等から始まる識別子を元Token境界に合う範囲で結合し、同じDocを解析します。NERは使いません。

比較語彙は、関係端点が属する文節のlemma列です。位置分類`FUNC`・`SYN_HEAD`、依存関係`aux`・`case`・`cop`に該当するTokenとその文節内`fixed`直接子、空白・句読点を除きます。元端点が除外された関係は採りません。

| 元依存関係 | 述語側P | 名詞側N |
| --- | --- | --- |
| nsubj・obj・iobj・obl | head | dependent |
| acl・amod | dependent | head |

元UPOS・依存ラベル・方向を保持し、名詞側の詳細品詞が「助動詞語幹」の関係は除外します。表示は選択Tokenの先頭から末尾まで、照合は選択したlemmaだけを使います。

### 支持判定

照合キーはP/Nのlemma列・元UPOS、依存ラベル、Nの方向です。解析契約の識別子と固定版は[models.py](../../src/native_japanese/models.py)で管理します。

1. 同じキーがBloom陽性なら`indexed`。
2. 陰性なら、N以外の条件を固定した用例名詞群と比較します。取得可能な各名詞列のベクトルを等票で平均し、入力名詞とのcosineが境界以上なら`supported`。
3. どちらもなければ`review`として両端を指摘します。

既定境界は0.35です。Pを類義語へ一般化せず、ベクトル・比較群がない場合は追加支持なしとします。類似度0への置換は行いません。資源破損・非有限値・解析契約の不一致は処理異常です。

## 根拠と制約

文節・依存関係の取得は[GiNZA](https://megagonlabs.github.io/ginza/)、機能表現の`fixed`は[UD](https://universaldependencies.org/ja/dep/fixed.html)に基づきます。支持推定の研究上の位置付けは[先行研究](../research/prior-research.md)を参照してください。

文節の残り全体を比較するため、語の欠落や修飾語の混入が起こり得ます（[#19](https://github.com/karin0624/use-native-japanese/issues/19)、[#20](https://github.com/karin0624/use-native-japanese/issues/20)）。係り先や原形の取り違えも残ります（[#21](https://github.com/karin0624/use-native-japanese/issues/21)、[#22](https://github.com/karin0624/use-native-japanese/issues/22)）。

Bloom陽性には低確率の誤通過があります。群平均と境界は正誤確率ではなく、用例追加で判定が悪化する場合もあります。表記差とベクトルの取得範囲により正用を支持できない例があります（[#23](https://github.com/karin0624/use-native-japanese/issues/23)、[#24](https://github.com/karin0624/use-native-japanese/issues/24)）。
