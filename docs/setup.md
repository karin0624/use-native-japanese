# データ準備と開発環境

現在は[固定二入力の限定照合](lexical-usage-validation.md)だけを進めています。本書は既存部品の再現手順であり、掲載した全処理の実行や、文脈モデル等の採用を指示するものではありません。`src/native_japanese/`には既存解析の保存、構造の列挙、用例索引、位置付き文脈表現がありますが、実利用できる検出器・訂正器は未完成です。`scripts/`は取得・形式変換・固定分割・共通評価に使います。Python 3.12で検証しています。参照コーパスの取得と解析計測はPOSIXの`resource`を使用します。

リポジトリ直下で実行します。既存環境と分けるため、以下は`.venv-check`を使います。

```sh
UV_PROJECT_ENVIRONMENT=.venv-check uv sync --locked --extra data
.venv-check/bin/python -m pytest -q
.venv-check/bin/ruff check src scripts tests
.venv-check/bin/ruff format --check src scripts tests
```

通常の依存はGiNZAとその固定モデル・辞書、igraph、marisa-trie、NumPyです。開発グループはpytestとRuff、`data` extraはデータ準備用のSciPy、scikit-learn、PyArrow等です。直接依存と間接依存は`pyproject.toml`と`uv.lock`で固定しています。取得コーパス、原本、生成索引、重みは`data/`や`artifacts/`等のGit対象外へ保存してください。

文脈表現も検証する場合は`uv sync --locked --extra data --extra detector`を同じ隔離環境に実行します。`detector` extraにはPyTorch、Transformers、日本語tokenizer、位置整列の依存が入り、LinuxとWindowsではCPU版PyTorchを選びます。encoderのテストは小型モデルをその場で作り、公開重みを取得しません。実行時もモデルの自動downloadは行わず、呼出側が準備したローカル重みだけを読みます。依存がない場合はencoderのテストをskipするため、基本環境だけのテスト成功を文脈表現の検証済みとは扱わないでください。

`GinzaParser`は公開token・head・dep・文境界をそのまま保存し、`enumerate_fragments`が汎用graph処理で2〜4 nodeの連結構造を列挙します。`UsageIndexBuilder`は固定した入力件数と出典を受け取り、全入力の処理後だけ完成索引を公開します。解析や索引の成功自体は、日本語の正しさの判定ではありません。

## 教師コーパスの固定実験を再生する

[Zenodoの原配布](https://doi.org/10.5281/zenodo.4276130)から`teacher_data.tsv`だけを取得します。Alex Kimn、Yiqun Hu、Takako AikawaによるCC BY 4.0の資料です。日本語教師が学習者の典型誤りを模して作った文で、実学習者の原文ではありません。配布説明の件数とは区別し、この固定原本は6340対です。原本SHA256は`22d154be9f5eebecf0fe3bbaba31f541037a9b1bfe91a6428e62c5768f37dd4e`です。

```sh
mkdir -p data/raw
curl --fail --location \
  https://zenodo.org/api/records/4276130/files/teacher_data.tsv/content \
  --output data/raw/teacher_data.tsv
.venv-check/bin/python scripts/prepare_teacher.py \
  --source data/raw/teacher_data.tsv --output data/teacher-prepared
.venv-check/bin/python scripts/evaluate_detection.py prepare-corrected \
  --teacher-dev data/teacher-prepared/split-20261021/dev.jsonl \
  --output data/teacher-prepared/teacher-corrected-dev.jsonl
```

`prepare_teacher.py`は2026-10-21の固定実験を再生する入口です。別の原本を分割する道具ではありません。原文の`<>`と訂正文の`()`が一組で、前後の文字列が一致する等の既存検査を保ち、通過した6297対を使います。日本語の解析・修復は行いません。

元実験はPython 3.12.14、NumPy 2.3.5、SciPy 1.17.0、scikit-learn 1.8.0を使用しました。誤文・訂正文を区別せず全文完全一致を連結し、文字TF-IDF 2〜4gram・cosine 0.85以上の近傍を追加して、SciPyの無向連結成分3532groupを作成しました。`GroupShuffleSplit(test_size=0.5, random_state=20261021)`でpoolを分け、同seedのNumPy乱数器でdev/testを各300対選びました。その541group全体を保留し、残る昇順group IDsを`random.Random(20261021)`でshuffleした先頭1000groupから、続く同じ乱数器で各一対を選んだものが固定監査対象です。

今回の再生では近傍探索や乱数選択をやり直さず、本文を含まない`scripts/teacher-selection-20261021.json`のgroup所属・選択原行番号を使います。原手法、原処理3本のSHA256、版本、seed、出典、ライセンス、期待出力hashもそのmetadataに保持しています。原本hash・検査結果hash・7出力のhashが全て一致した場合だけ新規ディレクトリへ書き、再生manifestへ記録します。LLM監査ラベルは含めず、固定1000groupの保留を再現します。

校正用devは元dev300対の訂正文を全文一致でまとめた288文です。元の誤りspanは流用しません。`prepare-corrected`は[共通評価器](evaluation.md)の既存機能です。後述の比較分割へこの`data/teacher-prepared`を渡すと、補助train訂正文まで原本から再現できます。

## 寺村誤用例集

[公式原本](https://doi.org/10.15084/00003533)のTXTを利用者が取得し、原本のファイルを指定します。SHA256 `b63dc40c63a6559e389f6f1af5e59ed18607a1513e9112dfa60c74cba0c53181`と異なる入力は拒否します。CC BY-SA 4.0の出典・ライセンスと変換内容は出力manifestに記録します。

```sh
.venv-check/bin/python scripts/convert_teramura.py \
  --source data/raw/teramura.goyoureishu.txt --output artifacts/teramura-converted
.venv-check/bin/python scripts/prepare_comparison.py \
  --source-dir artifacts/teramura-converted \
  --teacher-dir data/teacher-prepared --output artifacts/comparison
```

出力先は存在しないディレクトリを指定します。原記号の除去と文字offsetへの変換だけを行い、日本語の正規化・訂正は行いません。不明記法は未解決として保持し、訂正文を推測しません。

比較分割はseed `20261021`、候補group・学習者IDの連結成分で固定します。全既知入力を連結してから未解決等を除き、groupの20%をtest、残りの25%をdevにします。group数の割合であり、文章数が必ず60/20/20になる意味ではありません（[groupサイズの均衡化要望](https://github.com/scikit-learn/scikit-learn/issues/9297)も確認）。同一offsetの原ラベルを統合し、gapは幅0のまま保持します。出力manifestに依存版本・入力hash・出力hashと制約を残します。

補助訂正文の入力は、上の教師コーパス再生で作る次のファイル群です。

- `teacher-inspected.jsonl`
- `split-20261021/assignment.jsonl`、`dev.jsonl`、`test.jsonl`
- `unused-group-audit-20261021/audit-part-1.jsonl`、`audit-part-2.jsonl`

既存dev/testと固定1000監査groupを補助trainから保留し、寺村dev/testと全文一致する訂正文も除外します。文字TF-IDFによる近似監査は記録のみで、近似文の混入がない保証ではありません。補助訂正文は完全な正用goldではなく、寺村の語彙章ラベルも連語専用・AI生成文専用のgoldではありません。

## 2019年の参照passage

```sh
.venv-check/bin/python scripts/reference_corpus.py acquire --output artifacts/reference-2019
.venv-check/bin/python scripts/reference_corpus.py verify --output artifacts/reference-2019
```

既に固定した`scripts/reference-2019-manifest.json`を使います。revision `4b58cd23ae34af0f77c424b98807901cf472c377`、6 shardのサイズ・SHA256、抽出seed、予算、事前閲覧の記録を含む原manifestは、既存成果とのhash照合を維持するため文章も変更していません。任意の既存manifestを再現する場合だけ`acquire --manifest PATH`を指定します。取得時に同じbytesを出力先へコピーするため、検証時には外部manifestを必要としません。

MIRACL日本語コーパスの2019-02-01 Wikipedia由来passageを、記事IDだけのSHA256で100 bucketへ分け、0〜4を抽出します。bucket 0は入れ子の初期集合です。タイトル・本文・評価例は選択に使いません。`docid/title/text`をそのまま保存し、URL、snapshot、revision、ライセンスを付けます。記事URLは出典への参照であり、元記事の個別revisionは提供されていません。

約1.67 GBの固定Parquetを一つずつ取得し、サイズ・hash・行数を検証します。転送上限2 GiB、出力合計512 MiB、監視RSS目標2 GiBです。RSSはbatchごとの監視でありOSの強制上限ではありません。再試行・追記・resumeは行いません。既存出力ディレクトリを拒否し、失敗時は部分出力と`failed_partial`の`result.json`を残します。`result.json`の`complete`と検証成功を確認してから利用してください。

Wikipedia本文のCC BY-SA 3.0と出典を保持します。配布cardのApache 2.0を本文の再ライセンスとは解釈しません。画像・表・markupを取り除いた抽出済み資料で、誤りのない日本語とは限りません。元の抽出プログラムが非公開という[保守者の回答](https://github.com/project-miracl/miracl/issues/17#issuecomment-2014284662)もあります。未観測の表現を誤用とはみなしません。

## 参照passageの解析正本を保存する

取得済み原本を、固定版`GinzaParser`で一度だけ解析してSQLiteへ保存します。初期bucket 0全体は69,195文書、SHA256は`2a64cb7535ba62281cff602f94627370faaf374976bbabdab70c4ed2a37a3897`です。

```sh
.venv-check/bin/python scripts/parse_reference.py \
  --source artifacts/reference-2019/initial-bucket-00.jsonl \
  --output artifacts/parsed-reference.sqlite \
  --source-sha256 2a64cb7535ba62281cff602f94627370faaf374976bbabdab70c4ed2a37a3897 \
  --expected-documents 69195
```

同じ引数で再実行すると、入力全体のSHA256と解析metadata・予定件数を照合し、保存済み文書の原ID・記事ID・原文を確認して残りだけを解析します。入力は最初に一度hashし、同じファイル記述子を巻き戻して読み、処理中のサイズ・時刻・inode変更を拒否します。全文、全tokenのoffset・lemma・UPOS・tag・morph・head・dep、文境界を圧縮保存し、日本語の正規化・修復をしません。文書・token数・lemma/UPOS種類数と保存境界を文書単位の同じtransactionへ記録します。中断時は未完了のままで、完成した正本だけを`ParsedCorpus`で開けます。

限定計測は別の出力へ`--limit 1000 --expected-documents 1000`を指定します。原本全体のhashは保持し、先頭1,000件の限定snapshotと明記します。全量の完成とは扱わず、後から同じDBを全量へ切り替えることも拒否します。

```python
from native_japanese.corpus import ParsedCorpus

corpus = ParsedCorpus("artifacts/parsed-reference.sqlite")
for article_id, document in corpus.documents(order="input"):
    pass  # 同じ原文・公開注釈を索引や文脈計算へ渡す
for article_id, document in corpus.documents(order="seed", seed=20261021):
    pass  # 2回目も再解析せず逐次読み出す
```

順序は入力順`input`、記事ID・source IDの辞書順`article`、`SHA256(UTF-8(str(seed) + ':' + source_id))`順`seed`を選べます。hash同順位は入力順です。全文書・全語彙をRAMへ保持せずSQLiteで処理します。metadataの`parse_seconds`は再開時の入力照合・モデル読込を除く、解析と文書保存の累計wall timeです。CLIはbatchごとにcommit済みの進捗を標準エラーへJSONで出力します。`content_sha256`は成功後、保存済み文書を入力順で再読込し、`[article_id, Documentのdataclass辞書]`をUTF-8・`ensure_ascii=False`・`sort_keys=True`・空白なしJSONと改行へ変換した列のSHA256です。中断したhash状態を復元したとは扱いません。

## 依存の選択根拠

2026-10-21にPyPIの版本情報、公式資料の利用例、各リポジトリの直近のissue/PR対応を確認しました。連結成分は[SciPy](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.csgraph.connected_components.html)、分割は[GroupShuffleSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupShuffleSplit.html)、Parquetの逐次読取は[PyArrow iter_batches](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetFile.html)へ委ねています。独自のgroup分割やParquet readerは実装しません。sklearnのgroup分割は各回のtest集合間の排他性を一般には保証しませんが、ここでは一度testを取り除いた残りだけを再分割します。

保守状況の確認例は[SciPyの不具合修正](https://github.com/scipy/scipy/pull/26468)、[scikit-learnのmetadata修正](https://github.com/scikit-learn/scikit-learn/pull/35191)、[Arrowの検証修正](https://github.com/apache/arrow/pull/54911)です。これらは直近の保守活動の証拠であり、このデータ処理の品質保証ではありません。pytestとRuffは[公式のテスト導入例](https://docs.pytest.org/en/stable/getting-started.html)と[lint/format利用例](https://docs.astral.sh/ruff/tutorial/)に従い、空のruntime packageや配布用build設定はまだ設けません。
