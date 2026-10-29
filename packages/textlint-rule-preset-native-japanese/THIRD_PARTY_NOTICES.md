# 同梱資源と依存ソフトウェアの帰属

ライセンス原文は`licenses/`に無改変で収録します。この文書は製品コードの公開ライセンスを定めません。

## 圧縮fastTextモデル

`assets/fasttext-ja-mini`はLiebl Bernhard（Leipzig University）の[Compressed fastText Embeddings, v1.0, 2021-06-07](https://doi.org/10.5281/zenodo.4905385)を未変更で同梱しています。元系列はCommon Crawl・Wikipediaで学習した300次元fastTextで、提供者がcompress-fasttextの標準設定で圧縮したものです。

[配布ファイル](https://zenodo.org/api/records/4905385/files/fasttext-ja-mini/content)のサイズ・当方計算SHA-256・公式API公開MD5は`assets/manifest.json`にあります。

再配布条件はCC BY-SA 3.0です。[Zenodo説明](https://zenodo.org/records/4905385)と[上流fastText](https://fasttext.cc/docs/en/crawl-vectors.html)が同条件を示します。Zenodoの機械metadataのCC BY 4.0表示も保持しますが、上流条件の解除や自由なライセンス選択を意味しません。圧縮前の厳密なsnapshotと表示不一致の投稿者意図は未確認です。

上流の指定引用：Edouard Grave, Piotr Bojanowski, Prakhar Gupta, Armand Joulin, Tomas Mikolov, *Learning Word Vectors for 157 Languages*, LREC, 2018。

## 配布参照データベース

`assets/reference.sqlite3`はCC BY-SA 4.0の派生データベースです。Wikimedia projects editorsとMIRACL contributorsに帰属します。記事の題名・ID・著者履歴へ辿れるURL・使用区間は`assets/sources.json`に記載します。[Wikimedia利用規約§7](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content)は記事URLによる著者帰属を認めています。

原資料は[日本語Wikipedia dump 2019-02-01](https://archive.org/details/jawiki-20190201)です。[dump metadata](https://archive.org/metadata/jawiki-20190201)のCC BY-SA 3.0と、そこから本文抽出・区間分割した[MIRACLコーパス](https://huggingface.co/datasets/miracl/miracl-corpus)の[カード](https://huggingface.co/datasets/miracl/miracl-corpus/blob/d921ec7e349ce0d28daf30b2da9da5ee698bef0d/README.md)のApache 2.0表示を保持します。後者はWikipedia本文の再許諾ではありません。使用revisionは`4b58cd23ae34af0f77c424b98807901cf472c377`です。

当方による変更は、KWJAの基本句・述語項構造から作る述語構成・接続関係の完全一致表、用例集合のBloom filter、上記モデルで合成した異なる名詞特徴列のベクトルの等重み平均です。配布DBは派生キャッシュで、原文全文・全KNP解析・名詞側の元subtree graphを保持する構築用正本は配布しません。抽出契約と資源識別情報は`assets/manifest.json`に記載します。元記事・モデルの提供者が本DBを提供・公認したものではありません。

元資料・モデルのCC BY-SA 3.0を保持し、派生DBには同条件§4(b)が認める後継版CC BY-SA 4.0を適用します。

## 依存ソフトウェア

| ソフトウェア | 条件・帰属 | 原文 |
| --- | --- | --- |
| [KWJA 2.6.0](https://github.com/ku-nlp/kwja/tree/v2.6.0) | MIT、Copyright (c) 2022 Kyoto University。公式配布物を未変更で使用 | `licenses/kwja-MIT.txt` |
| [rhoknp 1.8.2](https://github.com/ku-nlp/rhoknp) | MIT、Copyright (c) 2022 Kyoto University。公式配布物を未変更で使用 | `licenses/rhoknp-MIT.txt` |
| [@dataiku/uv 0.12.0](https://github.com/575-lab/uv-npm) | MIT、Copyright (c) 2025 Trevor Manz | `licenses/uv-npm-MIT.txt` |
| [uv 0.12.0](https://github.com/astral-sh/uv/tree/0.12.0) | MIT、Copyright (c) 2025 Astral Software Inc. | `licenses/uv-MIT.txt` |
| [compress-fasttext](https://github.com/avidale/compress-fasttext) | MIT、Copyright (c) 2020 David Dale。モデル自体の条件とは別 | `licenses/compress-fasttext-MIT.txt` |

KWJAのbase解析モデルは、公式のモデル配布元からKWJAが取得する別配布物です。形態素・構文・述語項構造の解析には、公式の`CLIProcessor`からchar・wordの各moduleを使います。word moduleの入力上限は256または512 tokenに設定し、配布モデルのweightは変更しません。

Python本体とPython依存はuv経由で取得する別配布物で、各提供者の条件に従います。固定版・取得URL・ハッシュは`backend/uv.lock`にあります。

## ライセンス原文の取得元

| ファイル（licenses/配下） | 取得元 |
| --- | --- |
| CC-BY-SA-3.0.txt | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/legalcode.txt) |
| CC-BY-SA-4.0.txt | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/legalcode.txt) |
| CC-BY-4.0.txt | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.txt) |
| MIRACL-Apache-2.0.txt | [MIRACL](https://raw.githubusercontent.com/project-miracl/miracl/main/LICENSE) |
| uv-MIT.txt | [uv](https://raw.githubusercontent.com/astral-sh/uv/0.12.0/LICENSE-MIT) |
| uv-npm-MIT.txt | [uv-npm](https://raw.githubusercontent.com/575-lab/uv-npm/main/LICENSE) |
| compress-fasttext-MIT.txt | [compress-fasttext](https://raw.githubusercontent.com/avidale/compress-fasttext/master/LICENSE) |
| kwja-MIT.txt | [KWJA](https://raw.githubusercontent.com/ku-nlp/kwja/v2.6.0/LICENSE) |
| rhoknp-MIT.txt | [rhoknp](https://raw.githubusercontent.com/ku-nlp/rhoknp/main/LICENSE) |
