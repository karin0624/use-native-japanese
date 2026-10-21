# textlint接続

この接続は外部の日本語検査器をtextlintへつなぐ薄いルールであり、日本語の誤りを判定する機能自体は持たない。Python側の検査runtimeは未実装で、現時点では人工workerによる通信・位置・プロセス管理の検証までを行っている。検出精度や実用時の応答速度は未評価。

## 導入と設定

Node.js 20.18以上を使用する。リポジトリのルートで次を実行する。

```sh
npm ci
npm test
```

パッケージは非公開のworkspaceとして配置している。利用側のtextlint設定に、JSONLプロトコルを実装した常駐commandを指定する。実行ファイルと引数を別々の要素にし、shell用の引用符は含めない。相対パスはtextlintの実行ディレクトリを基準とする。

```json
{
  "rules": {
    "native-japanese": {
      "command": ["/絶対パス/検査器", "--設定", "/絶対パス/設定ファイル"],
      "requestTimeoutMs": 120000
    }
  }
}
```

`command`は必須。`requestTimeoutMs`は省略時120秒で、1〜2147483647ミリ秒の整数を指定できる。モデルや手法の指定は外部command側で行う。`shell: false`で起動し、同一設定の文書ではプロセスを共有する。要求がなくなって30秒経つと終了する。常駐中の保持モデルを使うための仕組みであり、120秒はリアルタイム性を保証する値ではない。

## 外部検査器のプロトコル

標準入力・標準出力にUTF-8のJSONLを使う。一行が一要求または一応答。stdoutへログや空行を出さず、ログはstderrへ出す。入力の一文書から抽出した複数の本文を`texts`へまとめて送る。

```json
{"id":"1","texts":["🍣 雲を食べる。","次の段落。"]}
```

応答の`id`は入力と同じ文字列。`results`は入力と同じ件数・順序で返す。要求同士の応答順は自由。

```json
{"id":"1","results":[{"offsetEncoding":"utf-16","diagnostics":[{"range":[3,8],"message":"語の組み合わせを確認してください。","suggestions":["ご飯を食べる"]}]},{"offsetEncoding":"utf-16","diagnostics":[]}]}
```

`range`は対応する`texts`要素内の0始まり・終端を含まないUTF-16コード単位の範囲。上例の絵文字は2単位を占める。Pythonの文字列添字はそのまま送らず、`len(text[:index].encode("utf-16-le")) // 2`等で変換する。範囲は空でなく、本文内に収まり、サロゲートペアの途中で切らない。`message`は空でない文字列。`suggestions`は省略可能な、空でない文字列の配列とする。候補の対象はその指摘範囲で、textlintのメッセージへ表示する。候補がなくても指摘できる。

要求単位の処理失敗は次の形式で返す。この場合、同じworkerを次の要求で利用できる。

```json
{"id":"1","error":{"message":"検査を完了できませんでした。"}}
```

不正JSON、未知ID、結果件数の不一致、異常終了、送受信エラー、応答期限超過では、そのworkerの保留要求をすべて失敗させる。自動再試行はせず、次の要求時に起動し直す。受信バッファの上限は16×1024×1024 UTF-16コード単位。無応答や異常を「指摘なし」として隠さない。

## Markdownの位置と検査範囲

textlintの`Paragraph`と`Header`から本文を抽出する。強調・取り消し線・リンクの表示文字は連結するため、`**雲**を[食べる](url)`を`雲を食べる`として検査できる。inline code、fence、画像、HTML、未知ノードは対象から除外し、その前後を連結しない。文書全体の集約後に一括要求するので、逐次生成中の呼び出し・debounce等は利用側の役割となる。

hard breakの`Break`葉は原文の`raw`を保持するため、改行直前のMarkdown用バックスラッシュや末尾スペースも検査器へ渡る。外部backend接続時は、それらが指摘結果へ与える影響を確認する必要がある。

指摘範囲は`StringSource`とtextlintの`locator.range`で原文位置へ戻す。装飾を跨ぐ指摘はその間のMarkdown記号を含む原文範囲になる。訂正候補は案内に留め、`fix`や自動置換は提供しない。

**制限:** `&amp;`などの文字参照や`\*`などのescapeで、ASTの葉の`raw`と`value`が一致しない場合、その葉全体を未検査にする。`&amp; 雲を食べる。`は段落全体が一つの葉になるため未検査。`&amp; **雲を食べる**。`では後ろの独立した安全な葉を検査できる。この境界の前後は連結しない。原文からの完全な位置対応を検証できない葉を推測で処理しない。

## 選択理由と追加調査（2026-10-21）

- [textlint 15.8.0](https://github.com/textlint/textlint/releases/tag/v15.8.0)と、公式ルールでも使われる[textlint-util-to-string 3.3.4](https://github.com/textlint/textlint-util-to-string)を使い、抽出・位置変換を既存APIへ委ねる。StringSourceは最終リリースが2023-11-25で、[readonly対応PR #25](https://github.com/textlint/textlint-util-to-string/pull/25)は未merge。保守が活発とはみなさない。
- `new StringSource(段落)`だけでは`&amp; 雲を食べる。`の「雲」を原文offset 6ではなく2へ戻す。ASTの葉の範囲、rawとvalue、原文部分文字列の一致を確かめてからStringSourceへ渡す。
- 代替候補の[mdast-util-to-nlcst](https://github.com/syntax-tree/mdast-util-to-nlcst)も調査した。[公式例](https://github.com/syntax-tree/mdast-util-to-nlcst#use)は装飾を跨ぐ位置を保持するが、[実装のpatch](https://github.com/syntax-tree/mdast-util-to-nlcst/blob/main/lib/index.js)はdecoded valueの長さを元の開始位置へ加算する。7.0.1＋mdast-util-from-markdown 2.1.0＋parse-latin 7.0.0＋vfile 6.0.3の実行で、`&amp; 雲を食べる。`はoffset 2（正しくは6）、`\* 雲を食べる。`はoffset 2（正しくは3）を再現した。両プロジェクトの関連issue検索では解決を裏付ける報告が見つからなかったため、安全な位置対応の代替には採用しない。
- JSON-RPCの導入にはPython側の変更とプロセス寿命の接続が引き続き必要になる。操作が本文配列の検査一つであるため、既存の小さいJSONL形式を維持する。日本語解析や採点はこのadapterへ追加しない。

`npm test`はNode標準test runnerで実行する。人工workerは通信・位置・寿命の回帰試験用で、日本語の正しさ・訂正精度・モデル速度の証拠ではない。
