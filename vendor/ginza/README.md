# GiNZAの元Token起点の句と選択Token取得

公式GiNZA 5.3.0へ、`bunsetu_phrase_span(..., use_token_head=True)`という
keyword-onlyの選択肢を追加した。既定値はFalseで、既存APIと解析pipelineは変えない。
Trueでは既存の文節範囲と既存の句探索を使い、探索開始点だけを渡されたTokenにする。
文法規則、依存の推定、単語別の例外は追加していない。

`.2`では`bunsetu_phrase_tokens(..., use_token_head=True)`を追加した。
同じ一つの既存探索を使い、選択されたTokenだけを原文順のtupleで返す。
`phrase_relations`と`use_token_head`はSpan APIと同じ意味・既定値を持つ。
既存Span APIは従来どおり選択集合の最小・最大indexから連続範囲を返す。
Token APIは途中の非選択語を含めず、元のTokenとその依存・lemmaを保持する。
両APIを`ginza`と`ginza.bunsetu_recognizer`から公開する。

adapterではSpanを原文表示と位置へ、選択Tokenを比較lemmaへ使う。
既存の識別子投影と両端重複時の復帰を保ち、識別子に属する選択Tokenは
その原文表記一項へ投影する。表示範囲に含まれるだけの非選択Tokenはlemmaへ入れない。
この集合は既存の探索結果であり、正しい語彙境界を保証しない。
F5の誤compoundとadvclは残り、助動詞等の包絡による混入だけを解消する。

固定モデルの自然文では、文節headと`bunsetu_span(token).root`が異なることがある。
例えば「『後漢書』（ごかんじょ）は、中国後漢朝について書かれた歴史書。」を含む
参照本文160967#0では、元head「朝」に対し既定APIは別の「後漢」を返す。
元headから同じ探索を行うと「中国後漢朝」を取得できる。

この変更は本プロジェクトによるもの。公式版との区別のため版を
`5.3.0+nativejapanese.2`とした。元のMITライセンスは[LICENSE](LICENSE)へ無改変で保持する。
公式配布URL、公式wheelのSHA-256、source commit、改修wheelのSHA-256とサイズは
[upstream.json](upstream.json)、source差分は[token-head.patch](token-head.patch)にある。

## 再作成

repoのrootで、開発用のuvから次を実行する。

```sh
uv run --no-project --with wheel==0.48.0 --with patch-ng==1.19.1 python tools/rebuild-ginza-wheel.py
```

再作成用scriptは一時directoryへ公式wheelを取得し、固定hashを検査する。
標準`wheel unpack`、既存`patch-ng`による差分適用、版の更新、標準`wheel pack`を行い、
生成物のhashが記録と一致した場合だけvendorのwheelを書き込む。
`SOURCE_DATE_EPOCH`を固定するため、同じwheelを再作成できる。
これらの再作成用ツールは製品の実行依存に含めない。

このwheelはpure Pythonの`py3-none-any`で、cloneのuvとnpm内のuvで同じものを使う。
利用時にpatch適用やwheelの再buildは行わない。現adapterの抽出契約は
`ginza-bunsetu-lemma-no-ner-identifier-tokens-v6`とし、旧参照DBとは混用しない。

## 採用前の確認

旧`.1`の採用時には、公式wheelと改修wheelを別processで固定85本文へ適用した。Docの公開JSON、文節の
user_data、既定phrase範囲、`sub_phrases`列挙は85本文すべてで一致した。
明示Trueでは既存49本文の比較照会は不変、既知の範囲不整合がある36本文で36照会が
回復し、既存照会の削除はなかった。この限定確認から全入力の正答を保証しない。

既定APIを無条件の元Token起点へ変える1行修正は、文節内の助詞等を渡した場合の
既存契約も変えるため採らない。明示optionで既存pipelineの出力を維持する。

`.2`では`tests/test_parser.py`で、選択集合に穴のある公開Docから非選択の助動詞・記号を
lemmaへ混入させず、表示範囲と元依存を保つことを確認する。既定起点と指定Token起点、
空の`phrase_relations`、既存複合語・識別子投影・重複時の復帰も検証対象とする。
旧85本文の比較を`.2`で再実行済みとは扱わない。

一次資料：

- [公式文節API](https://github.com/megagonlabs/ginza/blob/v5.3.0/docs/bunsetu_api.md)
- [既存の句探索](https://github.com/megagonlabs/ginza/blob/v5.3.0/ginza/bunsetu_recognizer.py#L120-L141)
- [spaCyのSpan.root](https://spacy.io/api/span#root)
