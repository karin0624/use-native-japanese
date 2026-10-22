# textlint接続

このルールは[Python検査器](runtime.md)の常駐JSONL出力をtextlintの位置付き指摘へ接続します。先に[セットアップ](setup.md)と[本体のprepare](runtime.md#索引準備と検査)を済ませてください。実解析からの指摘位置と常駐workerの再利用を確認済みです。[検証結果](runtime-validation.md)では、この実接続と人工workerによる通信・位置の試験を区別しています。

## 導入と設定

Node.js 20.18以上を使用する。リポジトリのルートで次を実行する。

```sh
npm ci
npm test
```

パッケージは非公開のworkspaceとして配置しています。リポジトリのルートに`.textlintrc.json`を作り、仮想環境内の実行ファイルと各引数を別々の要素で指定します。shell用の引用符は含めません。以下の絶対パスはローカルの準備先へ置き換えてください。相対パスはtextlintの実行ディレクトリを基準とします。

```json
{
  "rules": {
    "native-japanese": {
      "command": [
        "/path/to/use-native-japanese/.venv/bin/native-japanese", "serve",
        "--config", "/path/to/native-runtime/etc/native-runtime.json",
        "--cache", "/path/to/local/derived.sqlite3"
      ],
      "requestTimeoutMs": 120000
    }
  }
}
```

```sh
npx textlint --config .textlintrc.json input.md
```

追加用例を使う場合は`command`配列の末尾へ`"--examples", "/path/to/local/examples.sqlite3"`を加えます。取込方法は[出典付き用例の追加](runtime.md#出典付き用例を追加する)を参照してください。

`command`は必須。`requestTimeoutMs`は省略時120秒で、1〜2147483647ミリ秒の整数を指定できる。モデルや手法の指定は外部command側で行う。`shell: false`で起動し、同一設定の文書ではプロセスを共有する。要求がなくなって30秒経つと終了する。常駐中の保持モデルを使うための仕組みであり、120秒はリアルタイム性を保証する値ではない。

## 外部検査器のプロトコル

標準入力・標準出力にUTF-8のJSONLを使う。一行が一要求または一応答。stdoutへログや空行を出さず、ログはstderrへ出す。入力の一文書から抽出した複数の本文を`texts`へまとめて送る。

```json
{"id":"1","texts":["🍣 雲を食べる。","次の段落。"]}
```

textlintが送る`id`は文字列で、応答は入力と同じ値を返す。`results`は入力と同じ件数・順序で返す。要求同士の応答順は自由。

```json
{"id":"1","results":[{"offsetEncoding":"utf-16","diagnostics":[{"range":[3,8],"message":"語の組み合わせを確認してください。","suggestions":["ご飯を食べる"]}]},{"offsetEncoding":"utf-16","diagnostics":[]}]}
```

`range`は対応する`texts`要素内の0始まり・終端を含まないUTF-16コード単位の範囲。上例の絵文字は2単位を占める。Pythonの文字列添字はそのまま送らず、`len(text[:index].encode("utf-16-le")) // 2`等で変換する。範囲は空でなく、本文内に収まり、サロゲートペアの途中で切らない。`message`は空でない文字列。`suggestions`は省略可能な、空でない文字列の配列とする。候補の対象はその指摘範囲で、textlintのメッセージへ表示する。候補がなくても指摘できる。本体は現在`suggestions`による訂正案を生成しない。

上の応答例では省略していますが、各結果に任意の`unavailable`配列を付けられます。各項目の`kind`は`analysis`または`reference`、`reason`は空でない文字列、`ranges`は空でない範囲配列です。各範囲には指摘と同じUTF-16の条件を適用します。例えば、同じ要求への比較不能だけの応答は次の形式です。

```json
{"id":"1","results":[{"offsetEncoding":"utf-16","diagnostics":[],"unavailable":[{"kind":"reference","reason":"比較可能な用例群がありません。","ranges":[[3,4],[5,8]]}]},{"offsetEncoding":"utf-16","diagnostics":[]}]}
```

textlintは各端点に、`analysis`なら「判定保留（解析情報）: 理由」、`reference`なら「判定保留（比較不能）: 理由」を表示します。解析情報の不足は文単位のfallbackだけでなく、公開関係が得られない場合や名詞・述語候補に項がない場合も含みます。これらを誤用の`diagnostics`と区別し、参照不足を誤用や段落全体の処理失敗にしません。本体が返す`relations`の内部構造は読まず、保留表示には`unavailable`だけを使います。従来のworkerが配列を省略する場合も互換です。存在する場合は配列・項目・範囲の形式を検証し、不正なら処理エラーにします。

要求単位の処理失敗は次の形式で返す。この場合、同じworkerを次の要求で利用できる。

```json
{"id":"1","error":{"message":"検査を完了できませんでした。"}}
```

不正JSON、未知ID、結果件数の不一致、異常終了、送受信エラー、応答期限超過では、そのworkerの保留要求をすべて失敗させる。自動再試行はせず、次の要求時に起動し直す。受信バッファの上限は16×1024×1024 UTF-16コード単位。無応答や異常を「指摘なし」として隠さない。

## Markdownの位置と検査範囲

textlintの`Paragraph`と`Header`から本文を抽出する。強調・取り消し線・リンク・inline codeの本文は連結するため、`**雲**を[食べる](url)`を`雲を食べる`、`` `Pod`を削除する ``を`Podを削除する`として検査できる。fenceは対象外とし、画像・HTML・未知ノードの前後は連結しない。文書全体の集約後に一括要求するので、逐次生成中の呼び出し・debounce等は利用側の役割となる。

inline codeはmicromark 4.0.3でDocument原文を一度解析し、textlintのCodeと範囲・本文が一致するtokenを使う。引用・リストの継続行にある構文記号も既存parserが処理する。本文はCode.valueを保持し、LF・CRLFをHTML表示用の空白へ置き換えない。tokenの原文部分文字列やUTF-16幅が一致しない場合は処理エラーを返す。例えばcode中のNULはparserによる置換で原文と異なるためエラーとなる。

hard breakの`Break`葉は原文の`raw`を保持するため、改行直前のMarkdown用バックスラッシュや末尾スペースも検査器へ渡る。外部backend接続時は、それらが指摘結果へ与える影響を確認する必要がある。

指摘範囲は`StringSource`とtextlintの`locator.range`で原文位置へ戻す。装飾を跨ぐ指摘はその間のMarkdown記号を含む原文範囲になる。訂正候補は案内に留め、`fix`や自動置換は提供しない。

**制限:** `&amp;`などの文字参照や`\*`などのescapeで、通常本文のStr葉の`raw`と`value`が一致しない場合、その葉全体を未検査にする。`&amp; 雲を食べる。`は段落全体が一つの葉になるため未検査。`&amp; **雲を食べる**。`では後ろの独立した安全な葉を検査できる。この境界の前後は連結しない。原文からの完全な位置対応を検証できない葉を推測で処理しない。

## 選択理由と追加調査（2026-10-21）

- [textlint 15.8.0](https://github.com/textlint/textlint/releases/tag/v15.8.0)と、公式ルールでも使われる[textlint-util-to-string 3.3.4](https://github.com/textlint/textlint-util-to-string)を使い、抽出・位置変換を既存APIへ委ねる。StringSourceは最終リリースが2023-11-25で、[readonly対応PR #25](https://github.com/textlint/textlint-util-to-string/pull/25)は未merge。保守が活発とはみなさない。
- `new StringSource(段落)`だけでは`&amp; 雲を食べる。`の「雲」を原文offset 6ではなく2へ戻す。ASTの葉の範囲、rawとvalue、原文部分文字列の一致を確かめてからStringSourceへ渡す。
- 代替候補の[mdast-util-to-nlcst](https://github.com/syntax-tree/mdast-util-to-nlcst)も調査した。[公式例](https://github.com/syntax-tree/mdast-util-to-nlcst#use)は装飾を跨ぐ位置を保持するが、[実装のpatch](https://github.com/syntax-tree/mdast-util-to-nlcst/blob/main/lib/index.js)はdecoded valueの長さを元の開始位置へ加算する。7.0.1＋mdast-util-from-markdown 2.1.0＋parse-latin 7.0.0＋vfile 6.0.3の実行で、`&amp; 雲を食べる。`はoffset 2（正しくは6）、`\* 雲を食べる。`はoffset 2（正しくは3）を再現した。両プロジェクトの関連issue検索では解決を裏付ける報告が見つからなかったため、安全な位置対応の代替には採用しない。
- 操作が本文配列の検査一つであるため、JSON-RPCは導入せず、既存の小さいJSONL形式を維持する。日本語解析や採点はこのadapterへ追加しない。

## inline codeの接続（2026-10-22）

[micromark 4.0.3](https://www.npmjs.com/package/micromark/v/4.0.3)を直接依存として固定し、[公開rootのparse・preprocess・postprocess](https://unpkg.com/micromark@4.0.3/index.js)を使う。[codeTextのtoken](https://github.com/micromark/micromark/blob/main/packages/micromark-core-commonmark/dev/lib/code-text.js)が返す本文と元位置をStr形式へ接続するため、backtickやpaddingの規則をadapterで再実装しない。Code.raw単体の再解析では引用・リスト継続行の文脈を失うため、Document原文を使う。

[StringSourceの公式例](https://github.com/textlint/textlint-util-to-string/tree/v3.3.4#tostring-string)にもcodeを含む本文連結があるが、非Str葉の装飾位置を推定する実装をそのまま使わず、検証済みtokenの範囲を渡す。[mdastのissue #48](https://github.com/syntax-tree/mdast-util-from-markdown/issues/48)でも、valueと原文range内の文字列が異なることはASTの仕様と説明されている。textlintの拡張構文とmicromarkの全般的な同一性は保証せず、対応するCodeの範囲・本文が一致しない場合は明示的に失敗させる。

追加parseの固定2入力での観察は、Node.js 24.19.0で、11 UTF-16単位の短文が初回26.6ms・中央値1.0ms、1,868単位の技術文が初回47.1ms・中央値18.6msだった。各入力で3回の予備実行後に25回測定した。対象はpreprocess・parse・postprocessのみで、module読込、ASTとの照合、worker、日本語解析を含まず、一般的な速度を保証しない。

`npm test`はNode標準test runnerで実行する。人工workerは通信・位置・寿命の回帰試験用で、日本語の正しさ・訂正精度・モデル速度の証拠ではない。
