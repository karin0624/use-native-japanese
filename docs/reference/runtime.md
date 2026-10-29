# Python API・CLI・用例形式

[開発環境](../guide/development.md)で使う内部インターフェース。[照合方式](../design/usage.md)とは分けて、呼び出しとデータ形式を定義する。

## Python API

```python
from native_japanese.service import Inspector

with Inspector(reference_path="/path/to/reference.sqlite3", assets_dir="/path/to/assets") as inspector:
    analysis = inspector.analyze("本文。")
    result = inspector.lint("重複を畳む。")
    results = inspector.lint_many(["一つ目。", "二つ目。"])
```

`Parser`は同じPythonプロセス内で公式の`kwja.cli.cli.CLIProcessor`を使い、char・wordの各モデルを呼び出し間で保持する。公式のCLI用初期化を使い、`KWJA_CLI_MODE=1`で配布版の推論用実行経路を選ぶ。

word moduleの上限は、公式tokenizerで測った各文の符号化長と特殊token数の合計に従い、256または512から選ぶ。512を超える文や原文の文字を欠く結果は例外を返す。原文・文境界・モデルweightは変更しない。解析契約は[固定識別子](../../src/native_japanese/models.py)により参照DBと照合する。

`Inspector`は解析器と参照を保持する。`threshold`は既定値0.35、指定範囲は-1以上1以下の有限値。`with`を使わない場合は`close()`で解放する。

| メソッド | 戻り値 |
| --- | --- |
| `analyze(text: str)` | 原文・KNPのsnapshot・形態素の原文位置を持つ`Analysis` |
| `lint(text: str)` | `analysis`・`relations`・`diagnostics`を持つ`LintResult` |
| `lint_many(texts: Iterable[str])` | 入力順の`tuple[LintResult, ...]` |

`Inspector`は解析後の同じ`Analysis`を各`judge.assess(analysis)`へ渡し、`JudgeResult`の関係と指摘を集約する。現在の`judges`は開発途中の`PredicateJudge`一つで、`assess()`内で用法照合用viewの作成・検証と参照照会を行う。製品の目的と、追加を計画する解析機は[プロジェクト紹介](../../README.md)を参照する。

`Analysis`は原文の`text`、KWJAの全KNP出力をUTF-8で保持する`snapshot: bytes`、形態素の原文範囲`offsets: tuple[tuple[int, int], ...]`を持つ。位置は`rhoknp.Morpheme.global_index`順で、0始まり・終端を含まないPython文字添字である。空白を含む原文と解析形態素の対応を保持する。`Analysis.restore_doc()`は公開APIの`rhoknp.Document.from_knp()`を使い、独立した文書を返す。

`Analysis.usage_structure`は`Analysis`を参照する用法照合用の`UsageStructure`で、その`document`はsnapshotから復元したネイティブな`rhoknp.Document`である。`comparison`・`predicates`・`relations`はこの用法viewを使う便利APIである。

`predicates`は元解析の証拠付きの述語構成、`relations`は端点・照会・比較viewを返す。viewの形態素IDは同じsnapshotの`global_index`を参照する。`predicate_root`は同じ文書内のP出現を指す。`OccurrenceView`はNの参照範囲と語彙特徴の参照範囲を分けて持つ。

N–P関係の`Query`は`dependency="N-P"`・`noun_role="noun"`・`attachment=predicate.root`で、厳密なPとNの語彙的な組み合わせを照会する。元の格・項の種類・方向・接続点は`RelationTarget`・`OccurrenceView`・KNPに保持する。P–P関係では元関係を厳密照合し、`Query.related_predicate`と`related_attachment`で第二Pと接続点を保持する。`Query.noun`は空になる。

`GraphView.edges`は構成に含めた元基本句の主辞間の依存を保持する。厳密Pキーの`PredicateNode.head`・`dependency`は、その辺をP内の相対位置へ対応付ける。`Comparison.targets`の`scope="composition"`は、構成内部として保持する基本句依存を表す。述語・項・修飾関係の認定には[比較条件](../design/usage.md)を適用する。

`PredicateNode.lemma`と`Query.noun`の各要素は、元形態素の`canon`、欠ける場合は`lemma/reading`を使う語彙キーである。厳密照合には表記・読み全体を保持し、fastTextの取得時だけ`rsplit("/", 1)[0]`で末尾の読みを分ける。表記中の`/`は失わない。元の基本形・読み・代表表記候補は全KNP snapshotに保持する。

関係の評価には`status`（`indexed`・`supported`・`review`）、`score: float \| None`、照会ごとの支持情報を含む。現在の指摘は`range`と`message`を持ち、修正案は生成しない。全位置は0始まり・終端を含まないPython文字添字。

## CLI

`native-japanese`は`python -m native_japanese`でも呼べる。

| コマンド | 必須引数 | 出力・作用 |
| --- | --- | --- |
| `setup-parser` | なし | KWJAモデルを取得し、一文の解析で起動を確認する |
| `extract [input]` | `--source-id`・`--source-url` | 用例JSONLを標準出力へ書く |
| `import [input]` | `--examples` | 正本DBへ取り込み、件数をJSONで返す |
| `prepare` | `--examples`・`--reference`・`--assets-dir` | 配布参照を再構築し、件数・容量・時間をJSONで返す |
| `lint [input]` | `--reference`・`--assets-dir` | 検査結果をJSONで返す |
| `serve` | `--reference`・`--assets-dir` | 下記JSONL通信を続ける |

`input`はUTF-8ファイル。省略時と`-`は標準入力。`lint`・`serve`だけが`--threshold`を受け付ける。`lint`・`extract`はファイル・標準入力のCRLFも原文のまま保持する。

用例の追加は、出典本文を抽出・確認してから取り込む。パスとURLは自分の資源へ置き換える。

```sh
native-japanese extract --source-id source-001 --source-url https://example.org/source source.txt > /path/to/examples.jsonl
native-japanese import --examples /path/to/archive.sqlite3 /path/to/examples.jsonl
native-japanese prepare --examples /path/to/archive.sqlite3 --reference /path/to/reference.sqlite3 --assets-dir /path/to/assets
```

`import`は不正行があれば全件を取り消し、同じ記録は一件として登録する。返す件数は`records`・`documents_added`・`predicates_added`・`usages_added`・`observations_added`。取込後は`prepare`で参照を更新し、検査器を起動し直す。検査には配布参照DBを使う。

## JSONL通信

標準入出力はUTF-8で、一行が一要求または一応答。ログは標準エラーへ出す。

```json
{"id":"1","texts":["🍣 雲を食べる。"]}
```

`id`は応答を対応付けるJSON値で、textlintは文字列を送る。`texts`は文字列配列。応答は同じ`id`と、入力順・同件数の`results`を返す。

```json
{"id":"1","results":[{"offsetEncoding":"utf-16","diagnostics":[{"range":[3,8],"message":"語の組み合わせを確認してください。"}]}]}
```

| 項目 | 契約 |
| --- | --- |
| `offsetEncoding` | `"utf-16"` |
| `diagnostics` | 指摘の配列。支持があれば空配列 |
| `range` | 対応する本文内の0始まり・終端を含まないUTF-16範囲。空範囲やサロゲートペアの分断は不可 |
| `message` | 空白だけではない説明文 |
| `suggestions` | 外部検査器が任意に返す訂正候補の文字列配列。空白だけの要素は不可。本体は未生成 |
| `relations` | 本体が返す関係・支持情報。textlint接続では省略可能 |

本体のCLI出力は関係の端点もUTF-16へ変換する。Python APIの位置単位はPython文字添字である。

## エラー

CLIの終了値は正常処理0、処理失敗1、引数の誤り2。`lint`は指摘を含むJSONを正常に出力した場合も0となる。

`serve`は要求単位の失敗を次の形式で返し、後続要求を受け付ける。

```json
{"id":"1","error":{"message":"検査を完了できませんでした。"}}
```

解析・資源・位置・契約の不整合や不正な数値は、Python APIでは例外、単発CLIでは標準エラーへ理由を出す。

## 用例JSONL

一行は次の9キーだけを持つJSONオブジェクトで、文書ごとに原文・全解析・位置・出典を交換する。

| キー | 型・値 |
| --- | --- |
| `schema_version` | 整数`6` |
| `normalization_identity` | [固定解析契約](../../src/native_japanese/models.py)の`NORMALIZATION_IDENTITY`と一致する文字列 |
| `parser_versions` | [固定解析版](../../src/native_japanese/models.py)と一致するpackage名・版のオブジェクト |
| `text` | 空文字列を含む原文全文 |
| `knp` | KWJAが返す全解析を保持するKNP形式の文字列 |
| `offsets` | 形態素の`global_index`順の原文範囲`[[start, end], ...]` |
| `source_id`・`observation_id`・`source_url` | 空白だけではない出典ID・出現ID・所在 |

`extract`は原文のSHA-256を出現IDに使う。文書IDは原文・KNP snapshot・形態素の位置から計算し、解析または位置が異なる文書も区別する。各行は他の行やモデルの語彙状態に依存せず復元できる。

`import`は原文・KNP解析・形態素の位置・全出典を保存し、[比較view](../design/usage.md)から索引を作る。KNP内の基本句・文節・係り受け・述語項構造・照応・特徴を削らず保存する。`iter_records()`で保存した解析・位置・全出典をJSONLへ戻せる。同じ文書の全出典を保持し、比較viewから同じ用法を重複排除する。

用例JSONLと正本DBはschema 6を使う。正本DBは解析契約の`normalization_identity`・`parser_versions`をmetadataに保存し、読み込み時に現行の固定契約へ厳密照合する。schema 5や解析契約が異なる正本・JSONLは拒否し、原文からの再抽出を必要とする。書き出しで解析契約を付け替えることはしない。

配布DBは独立したschema 4の形式で、厳密な述語・P–P実共起群の存在索引、厳密PとNの語彙的な組み合わせのBloom集合、同じ厳密Pに属する名詞特徴の群平均を持つ検査用の派生データである。読み込みには解析契約の一致を必要とする。
