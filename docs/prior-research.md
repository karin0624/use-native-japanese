# 連語・語彙選択誤り検出の先行研究

調査更新日：2026年10月21日（UTC）。現在は既存研究・ライブラリを比較する段階で、製品方式は未採用である。目的は自由な日本語入力から不自然な語の組み合わせ・語彙選択を位置付きで検出すること。以下では全文検出、既知箇所の推薦、自然さ評定、検索部品を区別する。

## 全文から検出する研究

| 研究・方式 | 実際の出力と証拠 | 今回の判断に残る差 |
| --- | --- | --- |
| [新井ほか2020](https://www.jstage.jst.go.jp/article/tjsai/35/5/35_35-5_A-K23/_pdf)：日本語GED＋用例提示 | Lang-8で学習して各語の正誤を検出。P=.615/R=.304、語彙選択はTP22/FN77 | 局所指摘という出力は近いが、語彙選択が弱点。総合値を対象の精度と呼べない |
| [岡本ほか2024](https://www.anlp.jp/proceedings/annual_meeting/2024/pdf_dir/B7-2.pdf)：日本語BERT GED | 追加事前学習でP=.639→.725、R=.226→.244 | 語彙選択別性能、CPU速度、配布重みは未確認 |
| [Chantokun・IPA成果報告](https://www.ipa.go.jp/jinzai/mitou/it/2010/youth/jod03a0000005trp-att/seikashosai_gt-1.pdf)：日本語の統計的検出 | 入力全文の連語箇所へ下線。係り受け解析と相互情報量で共起を調べる。格助詞はn-gram条件付き確率を利用 | 日本語の全文検出の直接先例。ただし語彙別P/R、現行CPU速度、現在保守される公開検出器は未確認 |
| [Gaoほか2013](https://aclanthology.org/Y13-2006/)：英語の構文関係と用例照合 | 全文から依存関係を抽出して参照コーパスと比較。全体P=.75。recallは数値を示さず低いと報告 | 正用の未出現、構文・語義の曖昧さ、parserの誤りに失敗する。日本語の成績や現役の完成検出ライブラリを示すものではない |
| [AwkChecker 2008](https://cs.uwaterloo.ca/~ppoupart/publications/collocation-errors/collocation-errors.pdf)：英語のn-gram統計 | 自由文中の2〜5-gramを調べ、原句と編集候補の頻度差から疑わしさを判定。5人の利用者によるUI試験 | WordNet、冠詞・前置詞・活用など英語固有の誤りモデルを含む。体系的な日本語検出評価と現役公開検出コードは未確認 |
| [LinggleWrite 2020](https://aclanthology.org/2020.acl-demos.17/)：英語GED＋検索 | BiLSTM-CRF＋BERT/Flairで検出後にLinggleを使う。FCE学習30,953文・test2,720文でP72.3/R36.7/F0.5 60.6% | 検索自体による検出ではない。英語の値を日本語語彙選択へ転用できない。公式GED重みは未確認 |

Chantokunの[PM評価](https://www.ipa.go.jp/archive/files/000011075.pdf)や[2014年のUI拡張](https://www.anlp.jp/proceedings/annual_meeting/2014/pdf_dir/P4-8.pdf)を、対象別の検出精度の実証とは扱わない。AwkChecker本文が関連研究について引用するP100%/R28%も、AwkChecker自体の検出性能ではない。LinggleWriteと同名の[Flask3/LinggleWrite](https://github.com/Flask3/LinggleWrite)はdeprecatedのReact UIであり、公式の学習済GEDとは区別する。

日本語では[本間・小町2022](https://www.jstage.jst.go.jp/article/tjsai/37/1/37_37-1_B-L22/_pdf)の訂正モデルがGPUなし・batch1で平均.19〜.24秒、誤選択441箇所中79箇所の正訂正を報告した。これは訂正評価であり、局所検出の精度・速度へそのまま読み替えない。日本語重みの公開は未確認である。

[上垣外ほか2026](https://www.anlp.jp/proceedings/annual_meeting/2026/pdf_dir/Q4-23.pdf)はModernBERTと既存Hugging Face `run_ner`で広告文の誤りspanを検出し、参照広告あり／なしでweighted span F1=.719／.440を報告する。ただし両条件で広告キーワードとLP情報を入力し、助詞・キーワードの機械的誤りが対象である。一般文の連語誤用とは入力・誤り分布が異なり、専用重み・データの公開も未確認なので保留する。

専用GEDには明示的な誤り位置の教師があり、Codexによる生成やMLMとは学習目的が異なる。ただし対象語彙誤りの独立評価がなければ、「Codexでも誤るが専用BERTなら解決する」という根拠にはならない。日本語の直接先例はGEDにも統計的検出にもあり、先例の存在だけで一方を採用したり併用を決めたりしない。

## 検索・推薦・評定という別の課題

| 研究・成果物 | 確認できること | 全文検出との区別 |
| --- | --- | --- |
| [Linggle 2013](https://aclanthology.org/P13-4024/)・[Netspeak](https://github.com/netspeak/netspeak4-application-ngrams) | 指定phraseの用例・頻度を検索する | 任意の全文から疑わしい箇所を選んで判定する機能とは別。NetspeakはMITだが、default branchの最新変更は2022年、2024年PR #18とissue #19は未解決で、現役の誤用検出ライブラリとは呼ばない |
| [Erkほか2010](https://aclanthology.org/J10-4007/) | 用例と類似度による選択選好の一般化 | 構成部品として比較する。実誤用の全文検出評価ではなく、単独で不可能とも断定しない |
| [Pereiraほか2013](https://aclanthology.org/P13-3008/) | 日本語連語の訂正候補推薦。§3には全文からの抽出と原句・代替句の得点比較による警告案もある | 評価は誤り位置が既知で、正用の共起スコアが誤用を上回る例に絞っている。全文検出の精度実証とは区別する |
| [深美ほか2026](https://www.jstage.jst.go.jp/article/pjsai/JSAI2026/0/JSAI2026_5E1GS6d03/_pdf/-char/ja) | BERTと人の自然さ評定にr=.412 | 類義動詞5対から作った415フレーズの評定であり、自由文の局所検出ではない |

用例には外部原文を監査できる利点があるが、それは検出精度の証明ではない。[Li・Gaillat 2020](https://aclanthology.org/2020.mwe-1.13/)の用例照合による連語誤用検出は最大F0.5約.05であり、用例を使うこと自体を十分な性能の根拠にできない。[MultiGED](https://aclanthology.org/2023.nlp4call-1.1/)はtoken単位検出の参考になるが日本語を含まず、コーパス移行による性能低下も報告する。

### 拡張案へ戻って確認した研究

| 研究 | 設計へ反映することと限界 |
| --- | --- |
| [Herbelot・Kochmar 2016](https://aclanthology.org/C16-1093/) | より高頻度の代替があっても原句が正しい場合と、候補がない誤用を指摘する。原語を含む／除く文脈の分布的な整合を特徴にする先例。英語の既抽出形容詞–名詞824文脈の分類で、全文位置検出ではない |
| [Chersoniほか2018](https://aclanthology.org/W18-4603/) | 典型的な項のベクトル平均との類似度で、稀な正用と意味的違反を比較する。未観測正用を救う仮説の根拠になるが、英語の人工例の順位評価であり、日本語全文の検出精度ではない。文脈を増やした方式が劣るデータもある |
| [Vasselli・Watanabe 2023](https://aclanthology.org/2023.bea-1.19/) | 検索併用のF0.5改善が、誤警告減少と再現率低下による場合を示す。同じ警告負担で拾える誤りが増えるかを比較する。英語生成モデルのdecoder検索であり、軽量な独立検出器ではない |

2016年・2018年の専用公開実装は確認できなかった。2023年の参照元[EB-GEC](https://github.com/kanekomasahiro/eb-gec)はMITだがPython 3.7、Torch 1.8、spaCy 2.2等の環境を想定するため、そのまま採用せず研究上の失敗条件を利用する。既存の配列計算や分類器で公表手法を接続・拡張する開発は可能である。

Erk 2010の§3.1式(6)は任意の類似度の加重平均であり、cosineでは値域を[-1,1]と明記する。用例ごとの負値を0へ切ることは原式の必須要素ではない。正規化済みの各用例ベクトルについて、符号付きcosineの加重平均は、正規化し直さない加重平均ベクトルとの内積に等価である。その平均ベクトルへのcosineは、さらに平均ベクトルの長さで割るため別の計算になる。各用例の類似度を0で切る変更も、一回の内積へ等価変換できない。

今回の設計では、実在する対象への最大類似度と少数の根拠候補を、原語支持・候補ごとの記事支持と別に持つ案を検討する。多数派の対象群によって少数の正当な用法を平均で弱めないためであり、最大値だけで正用を認定するものではない。話題の類似、ハブになる語、解析誤り、別義による誤支持は残る。この候補取得をErkの加重平均式やChersoniのprototypeの再現とは呼ばない。

[Inoueほか2016](https://aclanthology.org/C16-1266/)は、同じ対象を含む別の述語項構造と主語／目的語の役割から、文脈に依存する選択選好を学ぶ。§6.2では名詞をX／ゼロベクトルにした評価も行う。対象の名前を知らないことと、その対象についての文脈情報がないことを分ける参考になる。一方、約482万の文脈付き正例と同数の通常SVO正例・人工置換負例で埋込みも更新しており、少量の日本語誤用教師で足りる根拠ではない。英語の出来事のもっともらしさや共参照評価を、日本語の慣用的な語の選択の正誤に読み替えない。

## 公開モデルで完了した限定評価

公開モデルを既存APIへ接続したscratchの検証であり、追加学習や製品採用はしていない。方式ごとに入力と処理件数が異なるため、以下を同一条件の精度ランキングにはしない。

| 実測候補 | 確認結果と現構成の制約 |
| --- | --- |
| [日本語GECToR](https://github.com/jonnyli1125/gector-ja) | 元TensorFlow版の`predict()`でtoken正誤出力を取得。旧依存が必要で、初回以降27文の中央値3.53秒、少数の語彙選択対で検出不足。第三者ONNXはORT 1.31.0でErfcにより読込失敗。重みの独立した商用条件も未確認 |
| [Megagon日本語ELECTRA](https://huggingface.co/megagonlabs/electra-base-japanese-discriminator) | 学習済置換語ヘッドを標準APIで実行。「精度が大きい」が自然な対照より低い置換語スコアになり、語彙誤用としての分離は未実証。SudachiTraはTransformers 5系の削除APIに依存 |
| [ModernBERT-ja-30m](https://huggingface.co/sbintuitions/modernbert-ja-30m)＋Oddballness | 原文offset付き採点は可能だが、tokenが助詞・述語をまたぎ、自然な対照・専門用語にも高値。実文20文の1文処理中央値1.805秒 |
| [Tohoku BERT v3](https://huggingface.co/cl-tohoku/bert-base-japanese-v3)＋[Oddballness](https://aclanthology.org/2025.coling-main.183/) | 固定dev/test評価を完了。指摘量と処理負荷を含め、この構成をそのまま高速lintへ採用する根拠は得られなかった。以下に条件と限界を記す |

置換語スコアやMLMの外れ度を誤用確率とは呼ばない。Oddballness原論文は日本語未評価で、閾値選択にはラベル付きデータを使う。今回もzero-shotは「追加学習なし」の意味であり、閾値調整まで教師不要という意味ではない。

### Tohoku＋Oddballnessの固定結果

モデルは`cl-tohoku/bert-base-japanese-v3` revision `65243d6e5629b969c77309f217bd7b1a79d43c7e`に固定した。追加学習せず、全語彙32,768 IDに対する公表式 `sum_j max(p_j-p_i, 0)`を既存MLM APIへ接続した。公式のMeCab／UniDic-lite＋WordPiece tokenizerとspacy-alignmentsの公開APIを使い、未知語・不明な対応を推測で埋めていない。13文83位置のTorch／ONNX比較ではrank・top5が一致、外れ度の最大絶対差は約6.5e-6だったが、全入力の同等性を保証するものではない。

教師作成擬似誤用のdev300対／test300対を先に固定し、重複を除く全1,154入力・11,260非特殊tokenの採点が完了した。エラーとnull offsetはともに0件。devでtoken overlap F0.5を最大にする閾値`0.7044925689697266`を選び、testへ一度適用した。

以下は誤文内の広い教師spanとのtoken overlapである。訂正文への指摘は別集計で、適合率・F0.5と閾値選択には含めない。

| 広い教師spanとのtoken overlap | dev | test |
| --- | ---: | ---: |
| 適合率 | 67.19% | 66.20% |
| 再現率 | 39.24% | 35.52% |
| F0.5 | 58.81% | 56.45% |

これは教師が囲んだ広い範囲との部分overlapであり、誤語そのものの境界や連語限定の精度ではない。testの訂正文293文のうち203文に364tokenを指摘した。計4,412文字に対して82.50指摘／1,000文字となるが、訂正文は完全な正用goldではないため真の偽陽性率とは呼ばない。同一文・近似文をgroup化してsplitを分けたが、近似テンプレートの完全独立も保証しない。

| 別に測定した固定Kubernetes実文20文・629token、各1回 | 結果 |
| --- | ---: |
| 1文の処理中央値／最大 | 3.658秒／25.652秒 |
| 20文の処理時間合計 | 109.432秒 |
| モデルロード | 11.915秒 |
| processのpeak RSS | 1,715.1MiB |

CPU2threads、mask batch16、Optimum ONNX／ONNX Runtime 1.31.0、CPUExecutionProvider、Transformers 4.57.6、Python 3.12.14の共有環境で測定した。ロードと文処理を分けた値で、20文全体を3.658秒で処理した意味ではない。専門家の正誤ラベルがない実文なので、この表から精度は分からない。

再現条件はscratchの`tohoku-mlm-validation/README.md`と`fixed-evaluation/validation-summary.json`、速度は`real20-batch16-summary.json`、集計は`ged-evaluation-data/aggregation-20261021/results/{dev-report.json,test-report.json,threshold.json,exploratory-category-and-warning-summary.json}`に保持する。原文・モデル・大きいlogは本リポジトリへコピーしない。この現構成の不採用を、BERT全般が不可能という結論にはしない。

## 既存評価資源と目的への適合

[Zenodo 4276130](https://doi.org/10.5281/zenodo.4276130)はCC BY 4.0の教師作成擬似誤用であり、実学習者の自由文ではない。元データに語彙選択カテゴリがなく、今回の総合GED評価だけでは対象に十分な誤りが含まれるかを判断できない。

既存dev/testで使った全groupを除外し、1,000groupから各1対を固定抽出して、モデル出力を見ずにLLMで参考分類した。lexical28、mixed21、other784、unclear167だった。これは人間goldではなく、母集団の真のカテゴリ率でもない。語彙・文法の境界例や判定困難例を含み、元の教師spanも改変していない。資料はscratchの`ged-evaluation-data/unused-group-audit-20261021/`に保持する。モデルを学習できることと、今回の語彙選択検出を妥当に検証できることは別である。

| 評価資源 | 使える情報 | 利用条件・適合の限界 |
| --- | --- | --- |
| [寺村誤用例集](https://www2.ninjal.ac.jp/teramuradb/about.html)・[TXTのライセンス](https://repository.ninjal.ac.jp/records/3550/file_details/teramura.goyoureishu.txt?filename=teramura.goyoureishu.txt&file_order=1) | 人手の誤用箇所、誤用分類、学習者・作文内文番号。7,415掲載行、語彙論2,377掲載／同一全文除外後1,655文 | 配布TXTはCC BY-SA 4.0で申請不要。同文の再掲載があり掲載数は独立文数ではない。欠落・文全体の注釈や不整形行を区別する。完全な訂正文はなく、学習者文からAI技術文への性能移行は未確認 |
| [JGECM](https://github.com/hideyoshikato/JGECM)・[2021年論文](https://www.anlp.jp/proceedings/annual_meeting/2021/pdf_dir/E1-4.pdf) | BCCWJ新聞500文をもとに10名が8種類の擬似誤りを作成。語彙選択カテゴリ、位置情報、原文IDがある | 注釈・構築scriptはCC BY 4.0。本文は含まず、BCCWJ-DVD v1.1と利用契約により復元し、生成データはBCCWJ規約に従う。指示例に「権利を尊重→尊敬」「常に→まいにち」があり連語限定ではない |
| [TEC-JL](https://github.com/koyama-aomi/TEC-JL)・[論文](https://www.jstage.jst.go.jp/article/jnlp/30/2/30_330/_pdf) | 実学習者文1,702文、複数訂正、語彙選択を含む誤用タグ。動詞・形容詞選択には連語誤りも含む | 申請制。論文のライセンスをデータの商用許諾とみなさない |
| [Lang-8](https://sites.google.com/site/naistlang8corpora/) | 日本語GEDの先例で使われる学習者文 | 配布元は研究・教育利用と商用利用を区別する |

JGECMの`leftpos`は左からの開始位置、`rightpos`は右からの終了位置を表すため、通常の開始・終了offsetと同じと仮定しない。[2023年の後続研究](https://www.jstage.jst.go.jp/article/tjsai/38/4/38_38-4_A-L41/_pdf)の表10では、語彙選択の訂正F0.5はA3RT／Bi-LSTMで7.53%、提案モデルで2.65%だった。全体の訂正改善を語彙選択の改善や検出精度に読み替えない。既存の対象カテゴリ資源は存在するが、今回必要な語彙誤り・位置評価・利用条件へ適合する範囲の確認が残る。

Wikipedia実訂正由来のJWTDは主に入力誤り、JCoLAは主に統語的な容認性を扱うため、全体成績だけを連語誤用検出の根拠にしない。新規の完全goldの手作業構築を必須条件にせず、まず既存カテゴリと注釈の再利用可能範囲を確認する。コード、重み、辞書、学習・評価データの許諾はそれぞれ分けて確認する。

### 固定した古い参照本文の経路

[MIRACL corpus](https://huggingface.co/datasets/miracl/miracl-corpus)の日本語は2019-02-01のWikipedia由来で、1,133,444記事・6,953,614passage。Parquet変換revision `4b58cd23ae34af0f77c424b98807901cf472c377`の6shardは計1,672,463,563bytesで、汎用readerから遠隔のデータセットコードを実行せず読める。本文の条件は元のCC BY-SA 3.0を保持し、カードのApache 2.0を本文の再許諾とは解釈しない。

少量の実読でも本文抽出による語の欠落があった。[作者の説明](https://github.com/project-miracl/miracl/issues/17#issuecomment-2014284662)でも厳密な再構築scriptは公開されていない。用例参照には使えるが、全て正しい文という教師にはできない。旧`legacy-datasets/wikipedia`の`20220301.ja`には配布済み日本語Parquetを確認できず、[datasets 4系以降のscript非対応](https://github.com/huggingface/datasets/issues/7693#issuecomment-3118509647)もあるため、古い呼出例をそのまま再利用しない。
