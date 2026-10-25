# Python APIとCLI

通常のtextlint利用は[セットアップ](setup.md)を参照してください。以下は[開発環境](development.md)を準備し、独自の出典付き正本から配布参照を作る手順と内部APIです。`native-japanese`は`python -m native_japanese`でも呼べます。原コーパス本文と構築用正本はGit外に置き、公開しません。配布用の固定モデルと参照DBはGit LFSで管理し、npmへ実体を同梱します。現行の採用契約と検証範囲は[最新方針](compact-runtime-plan.md)を参照してください。

## 索引準備と検査

```sh
. .venv/bin/activate
ASSETS=/path/to/assets
ARCHIVE=/path/to/examples.sqlite3
REFERENCE=/path/to/reference.sqlite3
native-japanese prepare --examples "$ARCHIVE" --reference "$REFERENCE" --assets-dir "$ASSETS"
native-japanese lint --reference "$REFERENCE" --assets-dir "$ASSETS" input.txt
```

`prepare`は正本からBloom集合と群平均をcompileし、完成した配布参照を置き換えます。初回は下の手順で正本を用意します。追加取込後も再compileしてください。正本は更新・監査・再compile用で、`lint`・`serve`に不要です。検査端末は配布参照と固定モデル・解析依存だけを使います。

`lint`はUTF-8本文を読み、入力省略または`-`で標準入力を使います。成功時はJSONを1件、失敗時はstderrへ理由を出して終了値1を返します。`--threshold`の既定値は0.35で、現行の検証では固定します。

ファイル引数の読込みには既存の改行変換が残り、CRLFはLFになります。元ファイルのCRLFを含む位置が必要な場合は、改行を保持した文字列をPython APIまたはJSONLへ渡してください。textlintはJSONLの経路を使います。

## Pythonで常駐させる

```python
from native_japanese.service import Inspector

with Inspector(
    reference_path="/path/to/reference.sqlite3",
    assets_dir="/path/to/assets",
    threshold=0.35,
) as inspector:
    analysis = inspector.analyze("本文をここに渡します。")
    result = inspector.lint("重複を畳む。")
    results = inspector.lint_many(["一つ目の本文。", "二つ目の本文。"])
```

同じ`Inspector`が解析器と参照を再利用します。`analyze`は解析関係・端点・不能理由、`lint`と`lint_many`は照会結果と指摘も返します。Python内の位置は0始まり・終端を含まない文字添字です。`with`を使わない場合は`close()`で解放します。原文との位置不一致や契約不一致は処理エラーです。

| `result.relations`のstatus | 意味 |
| --- | --- |
| `indexed` | Bloom近似索引で陽性。低確率の誤通過があり、実用例の直接確認ではない |
| `supported` | 索引は陰性で、比較可能な支持が境界以上 |
| `review` | 索引は陰性で、比較可能な支持が境界未満 |
| `unavailable` | 比較可能な数値がなく、理由を保持する |

陰性だけでは警告しません。`review`では名詞・述語それぞれの端点へ指摘を返します。`score`は同じ条件の名詞群平均の方向への類似度で、正誤確率ではありません。不能を0へ置き換えず、未収録群・ベクトル不能・解析情報不足の理由を残します。指摘が空でも全文を判定できたとは限りません。訂正案は未実装です。

## JSONLで常駐させる

```sh
native-japanese serve --reference "$REFERENCE" --assets-dir "$ASSETS"
```

stdinへ`{"id":"1","texts":["一つ目の本文。","二つ目の本文。"]}`を1行ずつ送ると、同じ`id`と同じ順序の`results`を返します。`lint`・`serve`の結果には`offsetEncoding: "utf-16"`、`diagnostics`、`relations`、`unavailable`が含まれ、全範囲をUTF-16へ変換します。要求単位の失敗は`error.message`で返し、後続要求を受け付けます。

`unavailable`は`kind`（`analysis`または`reference`）、理由`reason`、端点範囲配列`ranges`を持ちます。textlintはこれを「判定保留（解析情報）」「判定保留（比較不能）」として誤用指摘と区別します。通信とMarkdown位置対応の詳細は[textlint接続](textlint.md)を参照してください。

## 出典付き用例を追加する

信頼できる別出典のUTF-8本文から、入力と同じGiNZA契約で抽出します。以下の出典URLは置換用です。抽出結果と原文を確認して取り込みます。

```sh
native-japanese extract --source-id source-001 --source-url 'https://example.org/source' \
  source.txt > /path/to/examples.jsonl
native-japanese import --examples "$ARCHIVE" /path/to/examples.jsonl
native-japanese prepare --examples "$ARCHIVE" --reference "$REFERENCE" --assets-dir "$ASSETS"
```

`extract`・`import`も入力省略または`-`で標準入力を使います。抽出に参照・ベクトルは不要です。取込後の再compileで集合と群平均へ反映し、検査器は新しい参照で起動します。

1行は次の11キーだけを持つJSON objectです。

| キー | 内容 |
| --- | --- |
| `schema_version` | 整数`2` |
| `normalization_identity` | `ginza-bunsetu-lemma-no-ner-v1` |
| `predicate`・`noun` | 公開句全体の空でないlemma文字列配列 |
| `predicate_type` | `VERB`または`ADJ` |
| `dependency` | 元依存ラベル（空でない文字列） |
| `noun_type` | `NOUN`または`PROPN` |
| `noun_role` | 元方向`head`または`dependent` |
| `source_id`・`observation_id`・`source_url` | 出典ID・出現ID・所在（空でない文字列） |

`extract`は本文hashと端点から出現IDを作り、原文全文はJSONL・正本DBへ保存しません。旧KNPのschema1とは互換ではありません。抽出は正用の保証ではなく、出典選定・原文・利用条件の確認が必要です。評価入力や手作業の正解登録を、別出典追加の改善実証へ数えません。

`import`は再解析せず形式を検証して正本SQLiteへ保存します。不正行があれば取込全体を取り消し、同じ記録の再取込は重複登録しません。返却JSONは`records`（処理行数）、`usages_added`（新しい組合せ）、`observations_added`（新しい出典対応）です。同じ組合せの頻度・出典だけを増やしても、配布集合や等票の群平均は増えません。容量の増え方は[構成](architecture.md#用例の構築と配布を分ける)を参照してください。旧方式の[検証結果](runtime-validation.md)は履歴であり、現方式の精度・遅延を示しません。
