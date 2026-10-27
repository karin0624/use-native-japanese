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
| `analyze(text: str)` | 原文と解析関係を持つ`Analysis` |
| `lint(text: str)` | `analysis`・`relations`・`diagnostics`を持つ`LintResult` |
| `lint_many(texts: Iterable[str])` | 入力順の`tuple[LintResult, ...]` |

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

`input`はUTF-8ファイル。省略時と`-`は標準入力。`lint`・`serve`だけが`--threshold`を受け付ける。ファイル読込ではCRLFがLFへ変換されるため、改行を含む元位置が必要ならPython APIかJSONLへ原文を渡す。

用例の追加は、出典本文を抽出・確認してから取り込む。パスとURLは自分の資源へ置き換える。

```sh
native-japanese extract --source-id source-001 --source-url https://example.org/source source.txt > /path/to/examples.jsonl
native-japanese import --examples /path/to/archive.sqlite3 /path/to/examples.jsonl
native-japanese prepare --examples /path/to/archive.sqlite3 --reference /path/to/reference.sqlite3 --assets-dir /path/to/assets
```

`import`は不正行があれば全件を取り消し、同じ記録を重複登録しない。返す件数は`records`・`usages_added`・`observations_added`。取込後は`prepare`で参照を更新し、検査器を起動し直す。正本DBは検査時には不要。

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

本体のCLI出力は関係の端点もUTF-16へ変換する。Python文字添字と混用しない。

## エラー

CLIの終了値は正常処理0、処理失敗1、引数の誤り2。`lint`は指摘を含むJSONを正常に出力した場合も0となる。

`serve`は要求単位の失敗を次の形式で返し、後続要求を受け付ける。

```json
{"id":"1","error":{"message":"検査を完了できませんでした。"}}
```

解析・資源・位置・契約の不整合や不正な数値は、Python APIでは例外、単発CLIでは標準エラーへ理由を出す。

## 用例JSONL

一行は次の11キーだけを持つJSONオブジェクト。

| キー | 型・値 |
| --- | --- |
| `schema_version` | 整数`2` |
| `normalization_identity` | `"ginza-bunsetu-functional-projection-v10"` |
| `predicate`・`noun` | 空でないlemma文字列の配列。各配列も空にしない |
| `predicate_type`・`noun_type` | 元Tokenの標準UD品詞 |
| `dependency` | 空白だけではない元依存ラベル |
| `noun_role` | `"head"`または`"dependent"` |
| `source_id`・`observation_id`・`source_url` | 空白だけではない出典ID・出現ID・所在 |

`extract`は本文ハッシュと端点から出現IDを作る。JSONLと正本には本文全文を保存せず、用法と出典対応を保持する。入力と参照には同じ[解析契約](../design/usage.md)を適用し、異なる契約の資源は混用しない。
