# textlint接続

このプリセットはPython検査器の常駐JSONL出力をtextlintの位置付き指摘へ接続します。他のプロジェクトへの導入は[セットアップ](setup.md#他のプロジェクトで使う)を参照してください。配布パッケージはモデルと参照DBを同梱し、実行環境を初回に自動で準備します。cloneしたリポジトリの検証は[開発手順](development.md#checkoutのソースをtextlintで検証する)を参照してください。現行の採用契約と検証範囲は[現在の設計](design.md)を参照してください。[旧検証結果](runtime-validation.md)の日本語指摘結果は旧方式の履歴であり、人工workerによる通信・位置の試験と区別します。

## ファイル・パスを検査する

他のプロジェクトに配布パッケージを導入した場合は、そのプロジェクトでファイルまたはパスを指定します。

```sh
npx textlint --rule preset-native-japanese README.md
npx textlint --rule preset-native-japanese "docs/**/*.md"
```

配布パッケージの利用には設定ファイルや`command`の指定は不要です。初回の自動準備は、検査要求の応答期限を測り始める前に行います。

cloneしたリポジトリでは、開発環境を準備した後、そのルートで既存の`.textlintrc.cjs`を使います。

```sh
npx textlint README.md
npx textlint "docs/**/*.md"
```

この設定はcheckoutのソースとLFS資源を直接使います。開発用ルールを選択済みなので、このリポジトリでは`--rule`を追加せず実行します。変更後にtextlintを起動し直せば反映され、tgzの再導入は不要です。

## 指摘の読み方

`「A」と「B」の組み合わせに合う用例が見つかりません。表現を見直してください。`は、収録した用例との比較で確認が必要になった箇所です。`usage`ルールが`error`として返します。これは検査が正常に完了した結果であり、誤用を確定したわけではありません。

支持がある関係は表示しません。Bloom陽性、または同じ述語・関係条件の用例名詞群との類似度0.35以上を支持とします。どちらもなければ、通常の文章への指摘を返します。ベクトルがない場合も類似度0とはせず、名詞一般化による追加支持がないものとして扱います。参照に支持がないことは、日本語全体でその用法が存在しない証明ではありません。

ルール名は`native-japanese/usage`一つです。指摘なしならtextlintの終了値は0、文章への`error`を含むと1です。解析失敗、壊れた資源、不正な数値、通信異常などは`systemerror`となり、検査失敗として終了します。辞書不足のwarningや保留状態は返しません。

## 独自参照・既存接続用の外部検査器（任意）

既存の検査環境や独自参照を使う場合は、従来どおり`command`で外部検査器を指定できます。[開発環境](development.md)と[本体のprepare](runtime.md#索引準備と検査)を済ませ、`.textlintrc.cjs`に実行ファイルと各引数を別々の要素で指定します。shell用の引用符は含めません。以下の絶対パスはローカルの準備先へ置き換えてください。相対パスはtextlintの実行ディレクトリを基準とします。

```js
const command = [
    "/path/to/use-native-japanese/.venv/bin/native-japanese", "serve",
    "--reference", "/path/to/reference.sqlite3",
    "--assets-dir", "/path/to/assets"
];

module.exports = {
    rules: {
        "preset-native-japanese": {
            usage: { command, requestTimeoutMs: 120000, severity: "error" }
        }
    }
};
```

`command`などを上書きするときは、`usage`の`severity`も明示します。textlintではプリセット内の設定を上書きすると、プリセットで定めたseverityが引き継がれません（[textlint #1606](https://github.com/textlint/textlint/issues/1606)）。

用例追加後は正本から参照を再compileし、新しい参照でworkerを起動します。正本DBを`command`へ渡す必要はありません。[出典付き用例の追加](runtime.md#出典付き用例を追加する)を参照してください。

`command`は省略可能です。指定した場合だけ外部検査器を使います。`requestTimeoutMs`は省略時120秒で、1〜2147483647ミリ秒の整数を指定できます。外部検査器のモデルや手法はcommand側で指定します。`shell: false`で起動し、同一設定の文書ではプロセスを共有します。要求がなくなって30秒経つと終了します。常駐中の保持モデルを使うための仕組みであり、120秒はリアルタイム性を保証する値ではありません。

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

本体の応答には`relations`も含まれますが、textlintは位置付き表示に`diagnostics`を使います。比較不能を別表示する配列はありません。支持のない関係も通常の`diagnostics`へ返します。

要求単位の処理失敗は次の形式で返す。この場合、同じworkerを次の要求で利用できる。

```json
{"id":"1","error":{"message":"検査を完了できませんでした。"}}
```

不正JSON、未知ID、結果件数の不一致、異常終了、送受信エラー、応答期限超過では、そのworkerの保留要求をすべて失敗させる。自動再試行はせず、次の要求時に起動し直す。受信バッファの上限は16×1024×1024 UTF-16コード単位。無応答や異常を「指摘なし」として隠さない。

## 本文の範囲と原文位置

textlintの`Paragraph`と`Header`から本文を抽出する。強調・取り消し線・リンク・inline codeの本文は連結するため、`**雲**を[食べる](url)`を`雲を食べる`、`` `Pod`を削除する ``を`Podを削除する`として検査できる。fenceは対象外とし、画像・HTML・未知ノードの前後は連結しない。文書全体の集約後に一括要求するので、逐次生成中の呼び出し・debounce等は利用側の役割となる。

`.txt`・`.text`の標準parserは行ごとにParagraphを作るため、空行までの連続行を既存ASTの範囲からまとめる。同じ本文の単改行で、Markdownとテキストの解析文脈を分断しない。Markdownの段落・見出し境界はそのまま使う。リスト継続行の字下げでStr.rawとStr.valueが異なる場合も、micromarkのdata・lineEnding tokenが本文と原文位置に完全一致すれば、その位置情報で全文を保持する。

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
