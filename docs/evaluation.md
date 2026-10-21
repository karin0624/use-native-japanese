# 共通GED文字評価器

寺村の位置注釈と、旧教師devの訂正文を使う方式比較用の小さい評価器。モデル推論・学習・日本語構文規則は含まない。現在は人工データのテストだけを実行し、testへのモデル推論もtestを使った閾値選択も行わない。原文・モデル・prediction成果物をGitへ含めない。

## 入力と投影

Gold JSONLは既存の `{id,text,split,group_id,spans:[{start,end,lexical,...}],gaps:[{start,end,...}]}` を使う。`spans` は非空、`gaps` は空の半開区間。完全一致するgold範囲の重複は入力不正とする。元注釈のカテゴリを推測して変更しない。

Prediction JSONLは次の形に統一する。

```json
{"id":"例1","text_sha256":"原文UTF-8のSHA-256","scores":[{"start":0,"end":2,"score":0.8}],"coverage":{"total_chars":5,"scored_chars":2,"unscored":[{"start":2,"end":5,"reason":"abstained"}],"null_offset_items":0}}
```

- offsetは正規化前原文のPython Unicode codepoint単位の半開区間。UTF-16、byte、書記素単位ではない。全文hash・ID・範囲・有限な0..1 score・重複・入力欠落・coverageと実score unionの一致を検証する。
- 重なるscoreは文字ごとのmaxとする。同じ `{start,end,score}` の完全重複は拒否する。scoreはモデルの判定値であり、校正済み誤用確率とは主張しない。
- 未生成・保留・null offsetに対応する範囲はscoreを出さない。無採点文字を0点で埋めない。null offsetは位置を推測せず、`coverage.null_offset_items` に個数を残せる。既知の保留範囲に有効scoreが重ならないようproducer側で除外する。
- `coverage.total_chars` と `scored_chars` は必須。任意の `unscored` は理由付き未採点範囲で、採点済み範囲との重複は拒否する。申告されていない未採点文字は `unspecified` として数える。
- arc方式はtarget tokenの公式offsetへscoreを置く。複数tokenの端点間を埋めない。score閾値以上の連続文字だけを1つの警告spanにする。

## 指標と閾値

主比較は、教師訂正文devの警告対象文字数が同じ予算以内のときの寺村dev語彙文字recallと語彙span到達率。予算1/5/10文字/1,000文字は比較点であり、製品の利用者要件ではない。警告spanを結合して件数を減らしても、この文字予算は減らない。

閾値は訂正文devのscoreだけから選び、`score >= threshold` で予算に入る最低のbinary64浮動小数値とする。同点score群を途中で分けず、予算を超える境界scoreの `math.nextafter(score,+inf)` を用いる。全て採用可能なら0、score=1を全て除外する場合も有限sentinel `nextafter(1,+inf)` を使える。分母は未採点を含む訂正文の全文字。予算を使い切れない同点群はそのまま除外し、実際の文字数も保存する。語彙goldや寺村scoreを主閾値選択には使わない。

全非空gold spanのunionに対する文字precision/recall/F0.5、語彙gold文字recall、全/語彙gold spanの1対1overlap到達率、exact-span一致件数/recall、警告数と指摘文字数の各1,000文字当たりを併記する。非語彙の真のerrorを語彙FPへ分類しないため、語彙precisionは定義しない。未採点goldも全gold分母とFNに残す。coverageは採点済み文字・gold文字、理由別未採点文字数、および全文採点/部分採点/全保留の入力群ごとに出す。gapは件数のみ別表示し、非空span指標へ混ぜない。

1対1matchingにはSciPy `maximum_bipartite_matching`を用いる。警告1つで到達できるgoldは最大1つ。語彙到達率は語彙goldだけで独立に最大matchingを解き、全gold側で同率解がどう選ばれたかに依存させない。文字の重複goldはunion、spanの異なる重なり注釈は別spanなので、文字recallとspan到達率は同じ指標ではない。

補助点として、寺村dev＋教師訂正文devの**全文字**を含めた文字F0.5最大閾値を出す。同率なら高閾値とする。訂正文の指摘はこの点の適合率・F0.5分母にも入れる。閾値と参照入力/predictionの内容hash、CLIでは入力ファイルのhashと評価器hashも保存する。

## 実行

Python 3.12、SciPy 1.17.0で人工テスト9件を確認した。依存は既存の `data` extraを利用する。リポジトリ直下から実行する。

```bash
OPENBLAS_NUM_THREADS=1 uv run --extra data pytest tests/test_evaluate_detection.py -q
uv run --extra data python scripts/evaluate_detection.py prepare-corrected --teacher-dev /絶対パス/旧教師/dev.jsonl --output /絶対パス/corrected-dev.jsonl
uv run --extra data python scripts/evaluate_detection.py select-dev --gold /絶対パス/寺村/dev.jsonl --predictions /絶対パス/寺村-dev-scores.jsonl --corrected-gold /絶対パス/corrected-dev.jsonl --corrected-predictions /絶対パス/corrected-dev-scores.jsonl --output /絶対パス/dev-report.json
```

入力・出力に固定パスは使わない。別ディレクトリからは、スクリプトと入出力の絶対パスを指定できる。

`prepare-corrected` は教師dev訂正文を全文完全一致で重複除去して空goldへ変換するだけ。旧教師の広い誤りspanは寺村goldへ混ぜない。`select-dev` は入力のsplitがdevでなければ拒否する。test実行用CLIは現在用意しない。

初期調査のscratchにある旧 `ged-evaluation-data/aggregation-20261021/aggregate.py` を確認したが、token分母・同じ警告から複数goldへの到達・訂正文分母外という旧定義へ依存するため、集計関数は再利用しない。数行のJSON I/Oまで旧moduleへ結合する利点がなく、集計実装全体のコピーも避けた。

## 限界

集計は比較条件を統制するための資料であり、単一scoreや足切りで方式の採否を決めない。同じ入力ID・原文位置でケースを対応付け、共通の見逃し、片方だけの見逃し、訂正文で片方だけが警告する例の挙動・原因・不足情報と、他方式で補えるかを分析する。特定のensembleをこの評価器の前提にはしない。

これは注釈基準の代理指標。未注釈位置を完全な正用とは呼ばず、`unannotated_flagged` も真の誤警告件数を保証しない。教師訂正文も完全な正用goldではないので、文字予算は真のFPRではない。学習者の誤用注釈からAIの一般文・技術文へ性能が移る保証はなく、語彙選択カテゴリも連語限定ではない。span到達は最小1文字のoverlapで、訂正案の正しさを測らない。保留が多い方式は同予算でもrecallが制約されるため、coverageを必ず比較する。
