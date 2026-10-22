# Python APIとCLI

[セットアップ](setup.md)で生成した`etc/native-runtime.json`と、Git外の派生SQLite DBを使います。以下はリポジトリで仮想環境を有効にした後の操作です。`native-japanese`は`python -m native_japanese`でも呼び出せます。

## 索引準備と検査

```sh
PREFIX=/path/to/native-runtime
DERIVED=/path/to/local/derived.sqlite3
native-japanese prepare --config "$PREFIX/etc/native-runtime.json" --cache "$DERIVED"
native-japanese lint --config "$PREFIX/etc/native-runtime.json" --cache "$DERIVED" input.txt
```

`prepare`は既成CDBの名詞ID逆引きを派生DBへ準備します。[setupのprepare](setup.md)による資源取得とは別の操作です。用例群の平均は必要になった群ごとに保存するため、prepareだけで全ての初回計算が終わるわけではありません。元のCDBは変更しません。資源・モデル・方式の異なる派生DBの混用は拒否します。

`lint`はUTF-8の本文全体を読みます。入力ファイルの省略または`-`で標準入力を使います。成功時はJSONを1件、失敗時はstderrへ理由を出し終了値1を返します。`--timeout`は解析器の応答期限（秒、既定30）です。`--threshold`で境界を指定できますが、今回の評価は既定の0.35で固定します。

## Pythonで常駐させる

```python
from native_japanese.service import Inspector

with Inspector(
    config_path="/path/to/native-runtime/etc/native-runtime.json",
    cache_path="/path/to/local/derived.sqlite3",
    # examples_path="/path/to/local/examples.sqlite3",  # 追加用例がある場合
) as inspector:
    analysis = inspector.analyze("本文をここに渡します。")
    result = inspector.lint("重複を畳む。")
    results = inspector.lint_many(["一つ目の本文。", "二つ目の本文。"])
```

同じ`Inspector`が解析器と参照を保持します。`analyze`は解析関係・端点・不能理由を返し、`lint`はそれに照会結果と指摘を加えます。Python内の範囲は0始まり・終端を含まない文字添字です。`with`を使わない場合は`close()`で解放します。解析器の異常終了・timeout・原文との位置不一致は処理エラーです。

`result.relations`の状態は次のとおりです。

| status | 意味 |
| --- | --- |
| `observed` | 既成辞書または追加用例に直接の観測がある |
| `supported` | 直接観測はないが、比較可能な支持が境界以上 |
| `review` | 直接観測がなく、支持が境界未満で確認が必要 |
| `unavailable` | 比較可能な数値がなく、理由を保持する |

`review`では名詞・述語の端点へ指摘を返します。`score`は通常frame・公開照会候補間の最大のDです。Dは名詞群の方向への類似度であり、正誤確率ではありません。直接観測の有無とscoreは別で、不能を数値0へ置き換えません。文単位の解析不能は`analysis.unavailable`にも残ります。解析情報の不足には公開関係が得られない場合や、名詞・述語候補に項がない場合も含みます。指摘が空でも全てを判定できたとは限らないため、状態と不能理由を確認してください。

## JSONLで常駐させる

```sh
native-japanese serve --config "$PREFIX/etc/native-runtime.json" --cache "$DERIVED"
```

stdinに`{"id":"1","texts":["一つ目の本文。","二つ目の本文。"]}`のような要求を1行ずつ送ります。stdoutには同じ`id`と同じ順序の`results`を返します。`lint`と`serve`の外部結果は`offsetEncoding: "utf-16"`を明示し、指摘・関係の端点・不能範囲をUTF-16へ変換します。結果には`diagnostics`、`relations`、`unavailable`が含まれます。要求単位の失敗は`error.message`で返し、後続要求の受付を続けます。

`unavailable`には解析側の不能と、関係の`status`が`unavailable`になった結果を集約します。各項目は`kind`（`analysis`または`reference`）、空でない`reason`、空でないUTF-16端点範囲の配列`ranges`を持ち、関係の名詞・述語の端点を保持します。`analysis`は解析情報の不足、`reference`は参照との比較不能です。textlintはこの配列だけを使い、それぞれ「判定保留（解析情報）: 理由」「判定保留（比較不能）: 理由」と表示します。誤用の指摘とは区別し、参照不足を段落全体の処理失敗にはしません。プロトコルの詳細と互換性は[textlint接続](textlint.md)を参照してください。

## 出典付き用例を追加する

信頼できる別出典の本文をUTF-8で用意し、その出典IDと所在を指定します。以下のURLは置換用です。抽出結果を確認してから取り込みます。

```sh
native-japanese extract --config "$PREFIX/etc/native-runtime.json" \
  --source-id source-001 --source-url 'https://example.org/source' \
  source.txt > examples.jsonl
native-japanese import --examples /path/to/local/examples.sqlite3 examples.jsonl
native-japanese lint --config "$PREFIX/etc/native-runtime.json" --cache "$DERIVED" \
  --examples /path/to/local/examples.sqlite3 input.txt
```

`extract`と`import`も入力省略または`-`で標準入力を使います。`extract`は`--timeout`を指定できますが、派生索引やvectorを必要としません。`serve`も同じ`--examples`を受け取ります。Pythonでは`examples_path`で同じDBを指定します。

1行は次の9キーだけを持つJSON objectです。

| キー | 内容 |
| --- | --- |
| `schema_version` | 整数`1` |
| `normalization_identity` | `knp-standard-cv-head-v1` |
| `predicate` | 正規化した述語 |
| `predicate_type` | 述語型 |
| `case` | 格 |
| `noun` | 名詞全体 |
| `source_id` | 出典ID |
| `observation_id` | 同じ出現を識別するID |
| `source_url` | 出典の所在 |

版・正規化契約以外は空でない文字列です。`extract`は既存解析から同じ出現の照会候補を出力し、本文のhashと端点から出現IDを作ります。原文はJSONLやDBへ保存しません。抽出はその語の使い方の正しさを保証しないため、出典の選定・原文確認・利用条件の確認が必要です。評価入力そのものや手作業の正解用例の登録を、別出典追加による改善の証拠にしません。

`import`は形式を検証してSQLiteへ保存し、再解析やaliasの組合せ生成はしません。直接支持の一致キーは`(predicate, predicate_type, case, noun)`です。異なる出典との対応は保持しますが頻度で加点しません。同じ記録の再取込は重複登録せず、不正行があればその取込全体を取り消します。返却JSONの`records`は処理した行数、`usages_added`と`observations_added`は新規追加数です。

追加用例は直接観測へだけ反映します。群平均の更新、追加語から未追加語への一般化、訂正案は未実装です。保存・取込・照会費用の構造と残るCDB組合せ展開の制約は[実装方針](incremental-runtime.md#保存量と実行費用)にあります。少量の測定だけで大規模性能を保証しません。
