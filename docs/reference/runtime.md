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

`Inspector`は解析器と参照を保持する。`threshold`は既定値0.35、指定範囲は-1以上1以下の有限値。`with`を使わない場合は`close()`で解放する。

| メソッド | 戻り値 |
| --- | --- |
| `analyze(text: str)` | 原文・Docのsnapshot・語彙情報を持つ`Analysis` |
| `lint(text: str)` | `analysis`・`relations`・`diagnostics`を持つ`LintResult` |
| `lint_many(texts: Iterable[str])` | 入力順の`tuple[LintResult, ...]` |

`Inspector`は解析後の同じ`Analysis`を各`judge.assess(analysis)`へ渡し、`JudgeResult`の関係と指摘を集約する。`judges`は`UsageJudge`一つで、`assess()`内で用法照合用viewの作成・検証と参照照会を行う。

`Analysis`は`text`・`snapshot`・`vocabulary: VocabularySnapshot`を持つ。`vocabulary.resources`は固定Vocab全資源、`vocabulary.generation`は解析時点の`VocabularyGeneration`への参照である。世代は親と差分snapshotを持ち、`generation_id`・`resource_id`・`parent_id`で識別する。Parser所有の`VocabularyRecorder`が追加・変更を記録し、各世代を不変値として共有する。文書のLexeme・lookups状態は`vocabulary`に分けて保持する。`Analysis.restore_doc()`は語彙情報を含めて保存したDocの独立したコピーを返す。

`Analysis.usage_structure`は`Analysis`を参照する用法照合用の`UsageStructure`で、その`document`は用法照合に必要な語彙を持つ局所viewである。`comparison`・`predicates`・`relations`はこの用法viewを使う便利APIである。

`predicates`は元述語役割の証拠付き構成と`marker_units`（markとそのfixed機能単位）、`relations`は評価辺・端点・照会・比較viewを返す。viewのToken IDと依存辺は同じsnapshotを参照する。`predicate_root`は同じ文書内のP出現を指す。`OccurrenceView`はNの全subtreeと、`noun_feature_nodes`・`noun_case_nodes`・`noun_mark_nodes`・`noun_predicate_roots`を分けて持つ。P–P関係では`Query.related_predicate`と`related_attachment`で第二Pと接続点を保持し、`Query.noun`は空になる。`Analysis.comparison.roles`と`targets`には、認識した役割と、各元辺を関係・構成内部として参照する区分や、非語彙端点・名詞表現として働かない修飾先（`non_nominal_modifier`）などの対象外理由を保持する。

関係の評価には`status`（`indexed`・`supported`・`review`）、`score: float \| None`、照会ごとの支持情報を含む。指摘は`range`と`message`を持ち、全位置は0始まり・終端を含まないPython文字添字。

## CLI

`native-japanese`は`python -m native_japanese`でも呼べる。

| コマンド | 必須引数 | 出力・作用 |
| --- | --- | --- |
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

一行は次の13キーだけを持つJSONオブジェクトで、文書ごとに原解析・語彙情報・出典を交換する。

| キー | 型・値 |
| --- | --- |
| `schema_version` | 整数`4` |
| `normalization_identity` | `"ginza-predicate-role-occurrence-v12"` |
| `parser_versions` | [固定解析版](../../src/native_japanese/models.py)と一致するpackage名・版のオブジェクト |
| `text` | 空文字列を含む原文全文 |
| `doc_base64` | 原文と一致する公開`Doc.to_bytes()`全体のbase64文字列 |
| `vocabulary_resource_id` | 共有語彙資源のSHA-256 |
| `vocabulary_resource_base64` | 共有語彙資源のbase64文字列。同じ資源の初出行にだけ格納し、後続行は`null` |
| `vocabulary_generation_id` | 解析時点の語彙世代ID。基底のみの場合は`null` |
| `vocabulary_generation_base64` | 未出力の祖先から当該世代までの差分bundleのbase64文字列。同じ世代は一度だけ出力し、未出力分がなければ`null` |
| `vocabulary_state_base64` | 文書のLexeme属性・lookups・世代IDを保持する状態のbase64文字列 |
| `source_id`・`observation_id`・`source_url` | 空白だけではない出典ID・出現ID・所在 |

`extract`は原文のSHA-256を出現IDに使う。語彙資源と世代の保持範囲は[解析と比較範囲](../design/usage.md#解析と比較範囲)で定義する。基底資源と必要な祖先世代を当該行・同じストリームの先行行・正本DBから解決できなければ取り込めない。文書IDはDocのbytes・共有資源ID・世代IDを含む文書の語彙状態から計算し、語彙状態だけが異なる解析も区別する。同じ文書の全出典を保持し、NN tensorのbatch演算差でsnapshotが異なる場合でも、比較viewから同じ用法を重複排除する。

`import`は原文・Doc・語彙情報・出典を保存し、[静的view](../design/usage.md)から索引を作る。正本DBは基底資源を`vocabularies`、各差分世代を`vocabulary_generations`へ一度だけ保存し、`documents`に資源ID・世代ID・文書の語彙状態を保持する。`iter_records()`で全文・語彙情報・全出典をJSONLへ戻せ、基底と各世代のpayloadは初出だけに出力する。配布DBは厳密な述語・実共起群の存在索引、Bloom集合、名詞特徴の群平均を持つ、検査用の派生データである。

用例JSONLと正本DBはschema 4、配布参照は独立したschema 3で、いずれも定めた解析契約と一致する版だけを読み込む。
