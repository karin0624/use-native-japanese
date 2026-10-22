# 解析部品の調査

調査更新日: 2026年10月22日。製品方式は未採用である。以下は、構文関係を用いる方式を選ぶ場合の部品調査であり、コーパス方式や名詞・格・述語の三つ組を要件にするものではない。

## 判断基準

形態素、係り受け、述語の単位と正規化、格の解釈を既存ツールへ委ねる。アプリケーションに日本語の構文規則を実装しない。完全な複合述語正規化を必須要件にせず、必要な単位を既存出力で得られるかを方式ごとに判断する。異なる動作を同じ述語として救済するための独自規則は足さない。

公式APIだけで結論を出さず、公開ソース、issueと回答、PR、利用例、論文、配布条件、実際の出力を照合する。検索で見つからなかった機能は「不可能」ではなく「未確認」と記録する。少数例の動作確認と、未知の文書での性能評価を区別する。

## 候補と確認結果

| 候補 | 確認できたこと | 採用前の確認事項 |
| --- | --- | --- |
| GiNZAの文節API | [公式の述語・項抽出例](https://github.com/megagonlabs/ginza/blob/v5.3.0/docs/bunsetu_api.md)を変更せず実行できる。「削除する」のキーは「削除」、「情報を正しくする」では「正しい」と「する」が別に出る | 公式例の出力だけでは、各方式で必要になる述語単位と一致するとは限らない。独自の結合規則で補完しない |
| Juman++・KNP・rhoknp | KNPは用言代表表記や格の情報を出力する。[rhoknp](https://github.com/ku-nlp/rhoknp)に解析結果を読むAPIがある | 公式配布物を実測済み。用言代表表記・係・parentを既存APIで読める。表層格と意味格を区別する必要があり、辞書の商用利用条件は未確認 |
| KNP・pyknp-eventgraph | [述語API](https://github.com/ku-nlp/pyknp-eventgraph/blob/main/pyknp_eventgraph/predicate.py)に`normalized_reps`、`standard_reps`がある。KNPの代表表記があれば利用する | KNPの出力を直接読む方法より、この依存追加が必要かを確認する |
| KWJA・rhoknp | 既存APIで述語主辞の基本形・代表表記と述語項構造を取得できる。現行設定はガ・ヲ・ニ・ガ２・デ・ト・時間を扱う | 必要な解析taskだけを用いる経路、CPU性能、配布チェックポイントの条件を確認する。述語句全体の安定したIDを前提にしない |
| KWJA・pyknp-eventgraph | [2022年の公式論文](https://murawaki.org/pubdb/NL253ueda.pdf)に利用例がある | 現行公開版ではKWJAの`rel`出力からEventGraphの項へ接続する経路を確認できていない。論文の実績を否定せず、接続方法の未確認として扱う |
| desuwa | [述語抽出API](https://github.com/megagonlabs/desuwa/blob/main/desuwa/predicate.py)がKNP由来の既存素性を利用する | [対応処理](https://github.com/megagonlabs/desuwa/blob/main/README.md)は形態素・基本句の素性付与で、依存構造解析は別途必要。リポジトリはアーカイブ済み、公開依存条件はPython 3.8以上3.11未満 |

KNPの`用言代表表記`と`標準用言代表表記`は同じ機能ではない。前者は係り受けモードにも生成処理があり、後者は格フレーム選択に関わる。モードを変えても同じ出力が得られると仮定しない。

## KNP・rhoknpの実測

Juman++ 2.0.0-rc4（辞書20210915-2a1662a）、KNP 5.0 revision `bc4cef1`、rhoknp 1.8.2で確認した。rhoknpの既存APIから基本句の`features`、`parent`、格解析結果を読み、形式変換だけで接続できる。係り受けモードでも`用言代表表記`と表層の`係`が得られた。これを意味格と同一視しない。

例えば「情報を正しくする。」の用言代表表記は`正しい/ただしい+する/する`、「ファイルを削除する。」は`削除/さくじょ`だった。進行・受動・使役・可能に対応する別表記も得られるが、全ての複合述語の正規化を保証した結果ではない。

| 32文・1337字を同じprocessで処理 | 初回32文 | 起動済みで再処理した32文 |
| --- | ---: | ---: |
| 係り受け（`-tab -dpnd -para-scode none`） | 2.484秒 | 1.252秒 |
| 格解析（`-tab`） | 28.449秒 | 31.889秒 |

上表にprocess起動は含めない。Juman++／KNPの起動は係り受けで0.727／0.685秒、格解析で0.223／0.857秒だった。共有Linux環境、Intel Xeon Platinum 8573C、CPU割当上限4コア相当、メモリ上限16GiBでの少数入力の観測であり、一般的な速度保証ではない。格解析の初回と再処理では一部の出力差もあり、反復時の完全一致を保証しない。

再現資料はscratchの`knp-validation/output/{timing.json,host.json,dpnd-examples-api.json,case-examples-api.json}`に保持する。新規の製品依存やコーパス索引は追加していない。

## KWJAの公開値と比較単位

原述語の内容を保持できるかという問いに限り、KWJA main `821df300630b4d05c38406945536eaac848619da`、rhoknp main `665dcfb97c0d4c9b617af9f110e67799ac7f8c59`とrhoknp 1.8.2の公式ソース・test・利用例を追加確認した。新しいモデル取得や推論はしていない。

[Predicate](https://github.com/ku-nlp/rhoknp/blob/665dcfb97c0d4c9b617af9f110e67799ac7f8c59/src/rhoknp/cohesion/predicate.py)の`base_phrase`は述語の核となる基本句、`text`はその表層文字列を返す。`BasePhrase.canonical_form`という公開値は確認できず、実在する`Morpheme.canon`は形態素単位である。KWJAの標準writerにKNPの用言代表表記相当を合成する経路も確認できなかった。正規化された複合述語IDを取得できる前提で接続しない。

一方、公開APIで原文・全形態素列・基本句ID・関係の両端を保持できる。既存の基本句に「高くする」の全内容が入れば、その内容をそのまま保存できる。別基本句の場合は核の参照だけで全内容にならない。該当する公式fixtureはなく、実出力は未確認。完全な正規化IDの不在を不可能性の証明にせず、**必要な情報が既存単位に残るか、比較時に利用できるか**を次の確認事項にする。独自の形容詞・するの結合規則は足さない。

[Pas](https://github.com/ku-nlp/rhoknp/blob/665dcfb97c0d4c9b617af9f110e67799ac7f8c59/src/rhoknp/cohesion/pas.py)の`get_all_arguments()`は既定で共参照先も返す。元の解析が結んだ項を読む用途では`relax=False`とし、本文内の項と外界照応を区別する。`Argument.case`は表層助詞そのものではなく、主辞表示文字列だけを原対象の全体とも見なさない。

## 今回の限定照合で使う経路

語の基本形・代表表記と、同じ出現の格関係を別に読むため、既存Juman++ → KWJA 2.5.1のword tiny → rhoknp 1.8.2を選ぶ。KWJAには形態・格の解析を担当させ、使用実績は参照資料への照合で確認する。

[公式CHANGELOG](https://github.com/ku-nlp/kwja/blob/v2.5.1/CHANGELOG.md)と[CLI](https://github.com/ku-nlp/kwja/blob/v2.5.1/src/kwja/cli/cli.py)に、`--tasks word --input-format jumanpp --device cpu --model-size tiny` という公開経路がある。raw入力での文字正規化を通さず、既存Juman++の形態素列を渡せる。wordのwriterは表層を保ち、既存の辞書と活用処理でlemma/canonを再付与する。Juman++の基本形をそのまま固定する設定ではない。rhoknpの既存wrapperで常駐再利用とDocumentの受け渡しができ、独自のCLI通信実装は不要である。

releaseの依存はPython 3.9以上3.14未満、PyTorch 2.4以上2.8未満等。Python 3.12、CPU版PyTorch 2.7.1を使用する。新規checkpointは[公式配布のword tiny](https://huggingface.co/ku-nlp/kwja-checkpoints/blob/4c5db234089ca9e524fc5d32d383bc45e6fe27a2/word_deberta-v2-tiny.ckpt)一本、44,135,170 bytes、SHA-256 `eca1a9ae0c3a6acb4f73fea67170a3b628a4933dd87d4f489739cea7ecfcd500`。別にtokenizer・configと依存パッケージが必要である。KNPの格フレーム、char・seq2seq・typoのモデルは使わない。tinyが対象文を正しく解析することやリアルタイム性は、メタデータからは分からない。

固定33文書の実行では、word単独経路で再予測された読み・活用により対象語のcanon欠損を観測した。元のJuman++出力にはcanonがあり、返却32文書すべてで文境界・形態素列・spanの対応を保持していた。[writerの実装](https://github.com/ku-nlp/kwja/blob/v2.5.1/src/kwja/callbacks/word_module_writer.py)も入力形態素の順序と表層を引き継ぐ。このため、正規化は入力Juman++、主辞の選択と格関係はKWJAの公開出力から読む構成へ修正した。KWJA側の欠損を新しい活用処理で補う必要はない。

保守はrelease日だけで判断していない。stable 2.5.1は2025年8月、mainには2026年9月の実質変更があり、devには[word推論のメモリ改善 #260](https://github.com/ku-nlp/kwja/pull/260)と[長さ超過での未解析通知 #264](https://github.com/ku-nlp/kwja/pull/264)がmergeされている。これらdevの変更をstableに含まれるものとは扱わない。限定照合では入力ID・表層・位置の保存を確認し、欠落を未観測へ読み替えない。

## issueから分かったこと

- [GiNZA #167](https://github.com/megagonlabs/ginza/issues/167#issuecomment-906048577): 受動態の解析報告には、UD r2.8とGiNZA v5で改善したという開発者回答がある。古い報告を現行版の未解決問題として扱わない。
- [GiNZA #139](https://github.com/megagonlabs/ginza/issues/139#issuecomment-689934796): 固有表現と文節の不一致は上流で修正されている。アプリ側の修復規則を追加する根拠にはならない。
- [KNP #2](https://github.com/ku-nlp/knp/issues/2#issuecomment-978813492): 半角空白などの予約文字は新しいJuman++とKNPで対応されている。独自の文字置換を追加する前に現行版で確認する。
- [KNP #9](https://github.com/ku-nlp/knp/issues/9#issuecomment-872014083): 活用情報の不一致による異常終了は、標準の`--with-included-juman`構成で解決したと報告されている。
- [KNP #6](https://github.com/ku-nlp/knp/issues/6): 古いマニュアルとコマンドのオプションに差がある。実行する版のヘルプと出力を確認する。
- [KWJA #225](https://github.com/ku-nlp/kwja/issues/225): 複合動詞を分割したいという利用例であり、今回必要になる述語単位を得られない証拠ではない。

desuwaの公開issue、依存更新を除いたPR、外部利用例も検索した。係り受け・格解析を提供する経路は確認できず、KNPを直接使う構成より単純になる根拠は今のところない。

## 利用条件の扱い

コード、解析モデル、辞書、参照コーパスを区別する。別配布のGSK京都大学格フレームの条件を、KNPに同梱される辞書の条件だと推定しない。KWJAのコードはMITだが、元となるDeBERTaモデルの公式カードはCC BY-SA 4.0であり、配布チェックポイントまでMITだとは説明しない。

2026-10-21に配布物の取得manifest、残存ソースのCOPYING／README、公式の配布案内・FAQ・installation、公開issueを再点検した。KNP本体は京都大学の3条項BSD、Juman++本体はApache 2.0。KNPの別配布辞書・NEモデルと、実測したJuman++モデルへこれらの条件が適用されるかは未確認であり、商用利用禁止とは断定しない。KNP辞書ZIPの既存central-directory一覧27項目にはLICENSE／READMEがなく、実測コンテナも現環境には残っていないため、内部noticeは再確認できなかった。別売GSK格フレームの条件は転用していない。

KNPの最新の実質変更として確認したものは2023年11月の辞書index修正。公式辞書ZIPの取得経路はHTTP HEAD 200を返した。Juman++には2026年10月の実質修正がある。配布経路が存在することと、全同梱物の利用条件を確認できたことは別である。根拠は[KNPソース](https://github.com/ku-nlp/knp)、[公式配布案内](https://nlp.ist.i.kyoto-u.ac.jp/index.php?KNP)、[Juman++ソース](https://github.com/ku-nlp/jumanpp)と、scratchの取得済み配布物・調査メモに保存する。

独自規則を含む旧抽出器はscratchへ隔離済みで、製品の解析基盤として扱わない。KNPの接続確認は全文の誤用検出性能を示すものではなく、解析器の交換だけで検出器が完成したとはしない。
