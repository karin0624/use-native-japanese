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

同じ`Inspector`が解析器と参照を再利用します。`analyze`は解析関係と端点、`lint`と`lint_many`は照会結果と指摘も返します。Python内の位置は0始まり・終端を含まない文字添字です。`with`を使わない場合は`close()`で解放します。原文との位置不一致や契約不一致は処理エラーです。

| `result.relations`のstatus | 意味 |
| --- | --- |
| `indexed` | Bloom近似索引で陽性。低確率の誤通過があり、実用例の直接確認ではない |
| `supported` | 索引は陰性で、比較可能な支持が境界以上 |
| `review` | 索引は陰性で、名詞一般化の追加支持もない |

`indexed`と`supported`は指摘なし、`review`は名詞・述語それぞれの端点へ通常の指摘を返します。`score`は同じ述語・関係条件の名詞群平均の方向への類似度で、正誤確率ではありません。ベクトルや比較群がない場合は数値0を捏造せず、追加支持なしとして`review`になります。参照に支持がないことは、日本語全体での不存在の証明ではありません。解析の失敗、資源破損、不正な数値や契約不一致は処理異常であり、正常な指摘へ変換しません。訂正案は未実装です。

## JSONLで常駐させる

```sh
native-japanese serve --reference "$REFERENCE" --assets-dir "$ASSETS"
```

stdinへ`{"id":"1","texts":["一つ目の本文。","二つ目の本文。"]}`を1行ずつ送ると、同じ`id`と同じ順序の`results`を返します。`lint`・`serve`の結果には`offsetEncoding: "utf-16"`、`diagnostics`、`relations`が含まれ、全範囲をUTF-16へ変換します。要求単位の処理異常は`error.message`で返し、後続要求を受け付けます。

`diagnostics`は語の使い方について確認を促す正常な検査結果で、textlintでは`usage`ルールの`error`になります。支持ありなら空配列です。解析や資源破損などの処理異常は、これらと分けてtextlintの`systemerror`となります。Pythonの`lint`は正常なJSON出力なら指摘の有無によらず終了値0です。textlintは指摘なしなら0、文章への`error`を含むと1を返し、処理異常も失敗として終了します。通信とMarkdown位置対応の詳細は[textlint接続](textlint.md)を参照してください。

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
| `normalization_identity` | `ginza-bunsetu-lemma-no-ner-identifier-roles-v8` |
| `predicate`・`noun` | GiNZAの句探索で選択された非空白Tokenから作る文字列配列。範囲拡張後の両端が重ならない関係では、選択Tokenが属する識別子を原文表記一項へ投影する。重なる場合は両端とも識別子へ投影せず、選択Tokenのlemmaを使う |
| `predicate_type` | 述語役割の元Tokenの標準UD品詞（NOUN等も保持） |
| `dependency` | 元依存ラベル（空でない文字列） |
| `noun_type` | 名詞役割の元Tokenの標準UD品詞（ADJ・PRON等も保持） |
| `noun_role` | 元方向`head`または`dependent` |
| `source_id`・`observation_id`・`source_url` | 出典ID・出現ID・所在（空でない文字列） |

`extract`は本文hashと端点から出現IDを作り、原文全文はJSONL・正本DBへ保存しません。表示端点は公開句内の非空白tokenの先頭から末尾までとし、句の内部にある原文の空白は保持します。名詞側が既存辞書の「助動詞語幹」に分類される関係は抽出しません。識別子の一部を含む句は全名を含む範囲に広げ、外側の語を保持します。広げた述語句と名詞句が重なる場合は、その関係の両端とも範囲拡張・識別子の一項化を行わず、元の公開句を表示し、識別子へ投影しない選択Tokenのlemmaで検査します。表示範囲の途中に含まれるだけの非選択Tokenは比較lemmaに加えません。識別子は構造文字を含むASCII profileと元token境界が一致する範囲で扱い、元Docの品詞・依存と、元の端点tokenのPOS/tagは変更しません。nsubj/obj/iobj/oblとacl/amodの公開役割だけを用法照合へ変換します。advcl等の節間修飾やnmod/compound等から、品詞だけで独立の用法を作りません。各節の明示された主語・目的語・oblは保持し、nmod/compoundは句構成の取得に引き続き使います（[解析契約](architecture.md#原文から位置付き指摘まで)）。両端の位置と全Queryが完全に同じ関係は、元の順序を保って一度だけ返します。旧契約のJSONL・参照DBとは混用せず、同じ元本文から再抽出します。旧KNPのschema1とも互換ではありません。抽出は正用の保証ではなく、出典選定・原文・利用条件の確認が必要です。評価入力や手作業の正解登録を、別出典追加の改善実証へ数えません。

`import`は再解析せず形式を検証して正本SQLiteへ保存します。不正行があれば取込全体を取り消し、同じ記録の再取込は重複登録しません。返却JSONは`records`（処理行数）、`usages_added`（新しい組合せ）、`observations_added`（新しい出典対応）です。同じ組合せの頻度・出典だけを増やしても、配布集合や等票の群平均は増えません。容量の増え方は[構成](architecture.md#用例の構築と配布を分ける)を参照してください。旧方式の[検証結果](runtime-validation.md)は履歴であり、現方式の精度・遅延を示しません。
