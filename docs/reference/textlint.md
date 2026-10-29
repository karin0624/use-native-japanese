# textlintの設定と検査範囲

導入は[セットアップ](../guide/setup.md)、checkoutからの実行は[開発手順](../guide/development.md)を参照。

現在のプリセットは、開発途中の[述語と組み合わせの用法照合](../design/usage.md)を実行する。[製品の目的](../../README.md)に含まれる他の解析機と修正案の生成は未実装である。

## 設定

プリセット名は`preset-native-japanese`、ルール名は`native-japanese/usage`。

Markdownでは同梱processorを`--plugin textlint-rule-preset-native-japanese`で選ぶ。公開版textlint 15.8.0は設定ファイルのpluginより内蔵processorを先に選ぶため、CLIの`--plugin`指定を使う。`.txt`・`.text`は内蔵のテキストprocessorで検査できる。

| 項目 | 型・既定値 | 意味 |
| --- | --- | --- |
| `command` | 文字列配列・省略可能 | 実行ファイルと引数。省略時は同梱検査器を自動準備する |
| `requestTimeoutMs` | 整数・120000 | 起動後の要求期限。1〜2147483647ミリ秒 |
| `severity` | `"error"` | プリセットの既定値。設定上書き時にも明示する |

独自参照を使う例。パスは準備先へ置き換え、引数にshell用の引用符を含めない。

```js
module.exports = {
    rules: {
        "preset-native-japanese": {
            usage: {
                command: [
                    "/path/to/.venv/bin/python", "-E", "-X", "utf8",
                    "-m", "native_japanese", "serve",
                    "--reference", "/path/to/reference.sqlite3",
                    "--assets-dir", "/path/to/assets"
                ],
                severity: "error"
            }
        }
    }
};
```

WindowsではPythonのパスを`/path/to/.venv/Scripts/python.exe`へ置き換える。`command`省略時の同梱検査器とモデルの自動準備も、UTF-8を明示したPythonで起動する。

textlintではプリセット設定の上書き時にseverityが引き継がれない（[textlint #1606](https://github.com/textlint/textlint/issues/1606)）。相対パスの基準はtextlintの実行ディレクトリ。

## 指摘と失敗

| 結果 | 表示・終了値 |
| --- | --- |
| 用例による支持あり | 無出力・0 |
| 支持なし | 語の両端へ`usage`の`error`・1 |
| 解析・資源・通信などの処理異常 | `systemerror`・失敗 |

支持の[判定条件と限界](../design/usage.md)は照合方式を参照。外部検査器が`suggestions`を返した場合は説明文に候補を表示し、自動修正は行わない。同梱検査器は候補を生成しない。

## 外部検査器との通信

[JSONL通信](runtime.md#jsonl通信)を使う。`command`は`shell: false`で起動し、同一設定の文書間で共有する。未処理要求がなくなって30秒後に終了する。

要求単位の`error`はその要求だけを失敗させる。不正JSON・未知ID・結果件数の不一致・異常終了・送受信エラー・応答期限超過では、そのworkerの保留要求を全て失敗させる。自動再試行せず、次の要求で起動し直す。受信バッファ上限は16×1024×1024 UTF-16コード単位。

初回の自動準備時間は`requestTimeoutMs`に含めない。

## 本文と原文位置

| 入力 | 検査する範囲 |
| --- | --- |
| Markdown | 段落・見出し。文字参照・エスケープを解釈し、強調・取り消し線・リンク・インラインコードの本文を連結 |
| `.txt`・`.text` | 空行までの連続行を一段落として集約し、記号・文字参照・バックスラッシュも文字通り保持 |
| フェンスコード | 対象外 |
| 画像・HTML・未知ノード | 前後の本文を分離 |

Markdownの解釈は同梱processorが担当し、ルールは通常のテキストを解析機へ一括送信する。`.txt`・`.text`はMarkdownとして解析しない。

processorは`mdast-util-from-markdown`の公開APIで文書を一度だけ解析し、その解析中に生成された本文とtokenの原文範囲を保持する。文字参照やエスケープで長さが変わる部分も、この対応とtextlintの`locator.range`で元ファイルへ戻す。装飾を跨ぐ指摘には、その間のMarkdown記号を含む。

引用・リスト継続行の本文もtokenの範囲を使う。改行は保持し、Markdownの強制改行を表すバックスラッシュや末尾スペースは本文に含めない。インラインコード内の文字参照・エスケープは文字通り保持する。本文と位置の対応が不正な場合や、Markdownで同梱processorが選ばれていない場合は処理エラーにする。

複数文字に展開する文字参照の一部が指摘された場合は、対応する文字参照全体を原文の指摘範囲に含める。
