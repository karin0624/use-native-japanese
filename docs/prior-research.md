# 連語・語彙選択誤り検出の先行研究

調査日：2026年10月22日。論文本文、公開コード、モデルカード、データ配布元、issueを確認した。目的は自由な日本語入力中の誤り箇所を検出することであり、選択選好は候補手法の一つとして扱う。

## 日本語の検出・訂正研究

| 研究 | 実際の対象と結果 | 今回への適用範囲 |
| --- | --- | --- |
| [新井ほか2020](https://www.jstage.jst.go.jp/article/tjsai/35/5/35_35-5_A-K23/_pdf) | 各語の正誤を検出して用例を提示。Lang-8で学習し、検出P=.615/R=.304。別集計で語彙選択はTP22/FN77 | 局所指摘という出力が近い。語彙選択は弱点であり、総合値を連語誤用の精度と呼べない |
| [本間・小町2022](https://www.jstage.jst.go.jp/article/tjsai/37/1/37_37-1_B-L22/_pdf) | 非自己回帰訂正モデルと知識蒸留。GPUなし・batch1で平均.19〜.24秒、誤選択441箇所中79箇所を正訂正 | CPUでもニューラル方式を検討できる先例。日本語重みの公開は確認できていない |
| [岡本ほか2024](https://www.anlp.jp/proceedings/annual_meeting/2024/pdf_dir/B7-2.pdf) | BERTによる誤り箇所検出。追加事前学習でP=.639→.725、R=.226→.244 | 独自文法規則なしで局所指摘できる方式。語彙選択別性能、CPU速度、配布重みは未確認 |

[Pereiraほか2013](https://aclanthology.org/P13-3008/)は日本語連語の訂正候補推薦を扱うが、誤り位置は既知としている。評価も正用の共起スコアが誤用より高い例に絞っている。推薦候補の成績を、自由文の検出精度として使わない。

[深美ほか2026](https://www.jstage.jst.go.jp/article/pjsai/JSAI2026/0/JSAI2026_5E1GS6d03/_pdf/-char/ja)はBERTと人による連語の自然さ評定を比較し、相関r=.412を報告する。ただし類義動詞5対から作った415フレーズの評価であり、自由文からの誤り検出ではない。

## 公開モデルの実測と制約

以下は公開モデルを既存APIへ接続したローカル検証であり、製品への採用や汎用精度を確定した結果ではない。共通の人工対比例9文、教師作成の擬似誤用の誤正対、固定したKubernetes実文などを用いた。実文には専門家の正誤注釈がなく、処理した件数も方式ごとに異なるため、候補間の精度比較には使わない。

### 日本語GECToR

[gector-ja](https://github.com/jonnyli1125/gector-ja)は日本語BERTに編集分類と正誤分類のヘッドを加え、predict()で各tokenのdetect_probsを返す。Wikipedia由来の合成誤りとLang-8で学習している。コードはApache-2.0だが、外部配布の重みの商用利用条件は明示が十分でない。

コードの主要依存はTensorFlow 2.5など古い版であり、[依存修正PR](https://github.com/jonnyli1125/gector-ja/pull/1)も未マージである。一方、作者は2025年の[issue](https://github.com/jonnyli1125/gector-ja/issues/2)に回答しており、第三者によるONNX版が公開されている。

第三者ONNXは12個のErfcを含み、ONNX Runtime 1.31.0ではSession初期化に失敗した。さらにRandomUniformLikeが37個残るが、読込失敗のため反復出力の安定性は測れていない。graphは修理していない。元TensorFlow版は公開引数bert_trainable=Falseと元predict()で28文を処理できたが、Python 3.9／TensorFlow 2.5／Transformers 4.5などの旧依存が必要だった。初回以降27文のpredict中央値は3.53秒で、人工例「精度が大きい。」などを誤りとして検出しなかった。少数例での検出不足と実行負荷があり、そのまま高速lintへ採用する根拠は得られなかった。元TFと第三者ONNXの数値同等性、重みの独立した商用利用条件も未確認である。

### ELECTRAによる置換語検出

[Brooksほか2025](https://aclanthology.org/2025.findings-naacl.401/)は、誤りラベルで追加学習せずELECTRAの置換語検出出力を使い、ギリシャ語の実テキスト中の誤りを評価した。AUROC=.73だが、候補を事前に濃縮した評価であり、普通の日本語文書での誤警告率を表す値ではない。

[Megagon Labsの日本語学習済モデル](https://huggingface.co/megagonlabs/electra-base-japanese-discriminator)は約2億文の日本語mC4で学習し、重みもMITと明記している。配布checkpointに学習済の置換語検出ヘッドがあることを確認し、標準AutoModelForPreTrainingで人工9文と実文20文を処理した。同一processで3回反復したlogitsは一致し、起動済み短文の中央値は0.255秒だった。

ただし「精度が高い。」の「高い」より「精度が大きい。」の「大きい」の置換語スコアが低く、想定した疑わしさとは逆になった。技術用語にも人工例の対象語より高い値があり、日本語の語彙誤用検出としては未実証である。tokenizerのSudachiTraはTransformers 5系の削除済APIを参照するため、検証では4.57.6へ固定した。既存のslow tokenizerから原文offsetは取得できず、単語への集約や診断規則は追加していない。

### 日本語MLMとOddballness

[Oddballness 2025](https://aclanthology.org/2025.coling-main.183/)は、観測語の低確率そのものではなく、同じ文脈の語彙分布からの外れ度で誤りを検出する。日本語では未評価であり、閾値の選択にはラベル付き検証データを使用している。今回は公開研究コードをコピーせず、既存MLM APIへ公表式を接続した。追加学習はしていない。

[ModernBERT-ja-30m](https://huggingface.co/sbintuitions/modernbert-ja-30m)はMIT表記の公開モデルで、公式fast tokenizerのoffsetと全422位置のスコアを取得できた。一部の誤正対に文脈差が出た一方、「をまとめる」「が高い」などtokenが語や助詞をまたぎ、自然な対照にも高い値が出た。実文20文の処理中央値は1.805秒だった。モデルカードも形態素境界とtoken境界の不一致を説明しており、局所指摘の精度と速度を満たす根拠は得られなかった。

[Tohoku BERT v3](https://huggingface.co/cl-tohoku/bert-base-japanese-v3)はApache-2.0表記の公開モデルで、公式BertJapaneseTokenizerがMeCab／UniDic-liteとWordPieceを使う。原文offsetは返さないため、[spacy-alignments](https://github.com/explosion/spacy-alignments)の公開APIで文字とtokenを対応付けた。未知語・特殊token・不明な対応から範囲を推測しない。独自の日本語構文規則や文字列探索による位置復元は行わない。

公式Transformersによる13文の実行後、公式[Optimum ONNX](https://huggingface.co/docs/optimum-onnx/onnxruntime/usage_guides/models)／ONNX Runtime APIでも実行できた。同一checkpoint・83位置の保存出力ではrankとtop5が一致し、oddballnessの最大絶対差は約6.5e-6だった。これは出力契約の確認であり、全入力での数値同等性ではない。人工9文の処理中央値はTorchで2.59秒、ONNXで1.16秒だったが、共有CPUと少数例の実測である。

現在はTohoku＋Oddballnessを最後の限定比較として、固定したdev/test各300誤正対を評価している。閾値選択と最終評価を分け、検出位置・見逃し・指摘件数・速度を確認する。教師作成擬似誤用の全GED成績を、連語誤用だけの精度とは呼ばない。製品採用は未決定である。

## その他の比較から分かったこと

- [MultiGED](https://aclanthology.org/2023.nlp4call-1.1/)は文脈を保ったtoken単位の検出という点で近い。ただし日本語を含まず、別コーパスへ移すと精度が落ちる実例もある。
- [Li・Gaillat 2020](https://aclanthology.org/2020.mwe-1.13/)は用例集合との照合による連語誤用検出を公開しているが、最大F0.5は約.05。用例照合が既存手法であることと、今回のlintに十分有効であることは別である。
- [Erkほか2010](https://aclanthology.org/J10-4007/)の用例と類似度による選択選好は、初期案に近い。ただし、その評価を日本語連語誤用検出の精度として使うことはできない。

## 評価データ

今回の限定比較には、[日本語教師が作成した擬似誤用データ](https://doi.org/10.5281/zenodo.4276130)を使用する。配布元はCC BY 4.0を明記している。同一文・近似文をグループ化してdev/testを分けたが、近似テンプレートの完全な独立は保証できない。元の誤り範囲が広い例や、訂正後にも別の問題が残る例があるため、注釈範囲との一致と訂正文1000文字あたりの指摘件数を報告する。後者を真の誤警告率とは呼ばない。語彙選択だけのカテゴリは配布されていない。

[TEC-JL](https://github.com/koyama-aomi/TEC-JL)には実学習者文1702文、複数の訂正、語彙選択を含む誤用タグがある。[論文](https://www.jstage.jst.go.jp/article/jnlp/30/2/30_330/_pdf)は動詞・形容詞の選択誤りにコロケーション誤りも含むと説明している。今回に近い資源だが申請制であり、論文のライセンスをデータの商用許諾とはみなさない。[Lang-8配布元](https://sites.google.com/site/naistlang8corpora/)は研究・教育利用と商用利用を区別している。

Wikipediaの実訂正から作られたJWTDやその学習済モデルは再利用できるが、主対象は入力誤りである。JCoLAも主に統語的な容認性を扱う。どちらも、その全体成績だけで連語誤用検出の性能を示す評価にはしない。

評価では、既存データの利用条件に加え、実誤用か合成例か、学習者文か技術文か、訂正候補推薦か局所検出かを明記する。少数の例文への一致や、普通の文章に警告が出なかったことだけでは、汎用的に検出できると結論しない。

モデルカードの重みの許諾、実行ライブラリのコードライセンス、学習・評価データの利用条件はそれぞれ確認する。上記の動作確認やMIT／Apache表記だけで、製品全体の商用利用可能性を断定しない。
