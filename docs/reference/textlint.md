# textlintの設定と検査範囲

導入は[セットアップ](../guide/setup.md)、checkoutからの実行は[開発手順](../guide/development.md)を参照。

## 設定

プリセット名は`preset-native-japanese`、ルール名は`native-japanese/usage`。

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
                    "/path/to/.venv/bin/native-japanese", "serve",
                    "--reference", "/path/to/reference.sqlite3",
                    "--assets-dir", "/path/to/assets"
                ],
                severity: "error"
            }
        }
    }
};
```

textlintではプリセット設定の上書き時にseverityが引き継がれない（[textlint #1606](https://github.com/textlint/textlint/issues/1606)）。相対パスの基準はtextlintの実行ディレクトリ。

## 指摘と失敗

| 結果 | 表示・終了値 |
| --- | --- |
| 用例による支持あり | 無出力・0 |
| 支持なし | 語の両端へ`usage`の`error`・1 |
| 解析・資源・通信などの処理異常 | `systemerror`・失敗 |

支持の[判定条件と限界](../design/usage.md)は照合方式を参照。候補は説明文に表示し、自動修正は行わない。

## 外部検査器との通信

[JSONL通信](runtime.md#jsonl通信)を使う。`command`は`shell: false`で起動し、同一設定の文書間で共有する。未処理要求がなくなって30秒後に終了する。

要求単位の`error`はその要求だけを失敗させる。不正JSON・未知ID・結果件数の不一致・異常終了・送受信エラー・応答期限超過では、そのworkerの保留要求を全て失敗させる。自動再試行せず、次の要求で起動し直す。受信バッファ上限は16×1024×1024 UTF-16コード単位。

初回の自動準備時間は`requestTimeoutMs`に含めない。

## 本文と原文位置

| 入力 | 検査する範囲 |
| --- | --- |
| Markdown | 段落・見出し。強調・取り消し線・リンク・インラインコードの本文を連結 |
| `.txt`・`.text` | 空行までの連続行を一段落として集約 |
| フェンスコード | 対象外 |
| 画像・HTML・未知ノード | 前後の本文を分離 |

文書の本文を一括送信し、返った範囲を`StringSource`とtextlintの`locator.range`で原文へ戻す。装飾を跨ぐ指摘には、その間のMarkdown記号を含む。

インラインコードはDocument全体をmicromarkで解析し、textlintの範囲・本文と一致するtokenを使う。改行は保持し、原文との不一致は処理エラーにする。通常本文のリスト継続行も、tokenで本文と位置が一致する場合は検査する。hard breakは原文を渡すため、改行直前のバックスラッシュや末尾スペースを含む。

## 制限

文字参照・エスケープで通常本文の葉の`raw`と`value`を対応付けられない場合、その葉全体が未検査になる（[#25](https://github.com/karin0624/use-native-japanese/issues/25)）。`&amp; 雲を食べる。`は段落全体、`&amp; **雲を食べる**。`は文字参照の葉だけが落ち、後ろの独立した本文は検査される。
