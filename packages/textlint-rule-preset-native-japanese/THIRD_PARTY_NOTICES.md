# 同梱資源と依存ソフトウェアの帰属

資源ごとの条件を以下に記す。製品コードの公開ライセンスを定める文書ではない。ライセンス原文は`licenses/`に無改変で収録し、その免責条項を保持する。作者や提供者による本製品の公認・推薦を意味しない。

## 圧縮fastTextモデル

`assets/fasttext-ja-mini`はLiebl Bernhard（Leipzig University）, **Compressed fastText Embeddings**, v1.0, 2021-06-07, [DOI 10.5281/zenodo.4905385](https://doi.org/10.5281/zenodo.4905385)の公開ファイルを未変更で同梱したもの。取得元は[当該ファイル](https://zenodo.org/api/records/4905385/files/fasttext-ja-mini/content)。サイズとSHA-256は`assets/manifest.json`に記録する。実ファイルのMD5は、2026-10-25に[公式API](https://zenodo.org/api/records/4905385)の公開値`6f50bff91970a95743040320f76872ef`と一致した。SHA-256は当方が計算した固定値であり、公式公開SHA-256との照合とは称しない。

モデルは[CC BY-SA 3.0 Unported](https://creativecommons.org/licenses/by-sa/3.0/)の条件で再配布する。Zenodo説明本文はword vectorsをこの条件と明記し、[上流fastText crawl-vectors](https://fasttext.cc/docs/en/crawl-vectors.html)も同じ条件を示す。Zenodoの機械metadataには[CC BY 4.0 International](https://creativecommons.org/licenses/by/4.0/)とも記載されており、その表示と帰属も保持する。metadata表示によって上流のBY-SA条件がなくなる、または利用者がどちらかを自由に選べる、とは解釈しない。圧縮前の厳密snapshotと、この不一致についての投稿者の意図は未確認。

元系列はCommon CrawlとWikipediaを用いて学習したfastTextの300次元ベクトル。Zenodo作者が`compress-fasttext`の標準設定で圧縮したものを、そのまま使用する。当方によるモデルの再学習・再圧縮・語彙の編集はない。

上流が指定する引用：Edouard Grave, Piotr Bojanowski, Prakhar Gupta, Armand Joulin, Tomas Mikolov, **Learning Word Vectors for 157 Languages**, Proceedings of the International Conference on Language Resources and Evaluation (LREC), 2018。

ライセンス原文：`licenses/CC-BY-SA-3.0.txt`、`licenses/CC-BY-4.0.txt`。

## 配布参照データベース

`assets/reference.sqlite3`は**CC BY-SA 4.0 International**で配布する派生データベース。日本語Wikipediaの著者である**Wikimedia projects editors**に帰属する。記事ごとの題名・記事ID・著者履歴へ辿れる記事URL・使用区間IDは`assets/sources.json`に記す。[Wikimedia利用規約§7](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content)は、記事URLを通じた著者帰属を認めている。

元資料は[2019-02-01の日本語Wikipedia dump](https://archive.org/details/jawiki-20190201)で、[dump metadata](https://archive.org/metadata/jawiki-20190201)はCC BY-SA 3.0を示す。この資料から作られた[MIRACL日本語コーパス](https://huggingface.co/datasets/miracl/miracl-corpus)のParquet revision `4b58cd23ae34af0f77c424b98807901cf472c377`を使用した。MIRACL contributorsによるplain text抽出・区間分割にも帰属する。[カード](https://huggingface.co/datasets/miracl/miracl-corpus/blob/d921ec7e349ce0d28daf30b2da9da5ee698bef0d/README.md)のApache 2.0表示を保持するが、Wikipedia本文をApache 2.0へ再許諾したとは扱わない。MIRACLの公開ライセンス原文も`licenses/MIRACL-Apache-2.0.txt`に収録する。

既取得subsetから記事IDの固定hash順で選んだ1,024記事と、追加記事「ヴァニティ・フェア (バンド)」の合計1,025記事・6,463区間を用いる。現行の抽出契約は`ginza-bunsetu-functional-projection-v10`であり、同じ本文での再構築を完了した。配布資源の確定したsize・hash・契約は`assets/manifest.json`に記す。再構築した参照は89,964,544 bytes、SHA-256 `94d2a3230d686a4f890d4d0c8935fdc897f2fda62dd110aaeea1fa41b84cf2ce`。最終配布物への反映と接続は別途確認する。

v10の変換では、ASCIIの連続英数字wordrunと従来の英字/_開始の記号を含む識別子profileを分け、元境界にstrictに一致する複数Tokenだけを解析前に結合する。一Tokenの公開lemmaは保ち、数字開始の記号結合は増やさない。同じDocをNERなしの公式GiNZAで解析・照合する。用例の役割・向きは公開UD関係`nsubj/obj/iobj/obl`、`acl/amod`だけから読み取り、元依存辺と両端の元UPOSを保持する。比較語彙は各元anchorの所属文節から、位置分類`FUNC`・`SYN_HEAD`または公開dep`aux`・`case`・`cop`のTokenと、その文節内`fixed`直接子、公開`is_punct`・`is_space`を除いた全Tokenの公開lemmaである。`CONT`は語彙分類とみなさず、`NO_HEAD`を独自に機能語とせず、`mark`・`cc`等を依存ラベルだけで追加除外しない。同文節の修飾も条件に残る。表示範囲内の非選択Tokenを比較lemmaに加えず、原述語の一般化や解析後の識別子投影は行わない。用例集合をBloom filterと、異なる名詞列を等重みとした上記モデルの300次元ベクトル群平均へ変換する。本文全文、構築用正本、個々の出典本文は含めない。これらの抽出・変換・平均化が当方による変更であり、元記事や元モデルが本DBを提供したものではない。

元資料とモデルのCC BY-SA 3.0を保持し、派生DBにはその§4(b)が認める同じ条件の後継版を適用する。ライセンス原文：`licenses/CC-BY-SA-3.0.txt`、`licenses/CC-BY-SA-4.0.txt`。

## 起動に用いる依存ソフトウェア

- GiNZA `5.3.0`：MIT。Megagon Labsの公式配布物を変更せず使用する。版・取得URL・hashは`backend/uv.lock`へ固定し、元の著作権・許諾・免責原文は`licenses/ginza-MIT.txt`にも保持する。[公式source](https://github.com/megagonlabs/ginza/tree/v5.3.0)。
- `@dataiku/uv` 0.12.0：MIT。Copyright (c) 2025 Trevor Manz。[uv-npm](https://github.com/575-lab/uv-npm)。原文は`licenses/uv-npm-MIT.txt`。
- uv 0.12.0：MITの許諾を使用。Copyright (c) 2025 Astral Software Inc.。[uv](https://github.com/astral-sh/uv/tree/0.12.0)。原文は`licenses/uv-MIT.txt`。
- `compress-fasttext`：MIT。Copyright (c) 2020 David Dale。[compress-fasttext](https://github.com/avidale/compress-fasttext)。圧縮ソフトウェアのMITとモデル自体のCC条件は別である。原文は`licenses/compress-fasttext-MIT.txt`。

Python本体とGiNZAを含むPython依存packageは初回実行時にuv経由で取得する別配布物であり、それぞれの提供者の条件を保持する。

## ライセンス原文の取得元

- `CC-BY-SA-3.0.txt`：https://creativecommons.org/licenses/by-sa/3.0/legalcode.txt
- `CC-BY-SA-4.0.txt`：https://creativecommons.org/licenses/by-sa/4.0/legalcode.txt
- `CC-BY-4.0.txt`：https://creativecommons.org/licenses/by/4.0/legalcode.txt
- `MIRACL-Apache-2.0.txt`：https://raw.githubusercontent.com/project-miracl/miracl/main/LICENSE
- `uv-MIT.txt`：https://raw.githubusercontent.com/astral-sh/uv/0.12.0/LICENSE-MIT
- `uv-npm-MIT.txt`：https://raw.githubusercontent.com/575-lab/uv-npm/main/LICENSE
- `compress-fasttext-MIT.txt`：https://raw.githubusercontent.com/avidale/compress-fasttext/master/LICENSE
- `ginza-MIT.txt`：GiNZA公式5.3.0 wheelの`ginza-5.3.0.dist-info/licenses/LICENSE`。公式wheelのURLとhashは`backend/uv.lock`。
