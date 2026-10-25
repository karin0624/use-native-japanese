# GiNZAの元Token起点の句取得

公式GiNZA 5.3.0へ、`bunsetu_phrase_span(..., use_token_head=True)`という
keyword-onlyの選択肢を追加した。既定値はFalseで、既存APIと解析pipelineは変えない。
Trueでは既存の文節範囲と既存の句探索を使い、探索開始点だけを渡されたTokenにする。
文法規則、依存の推定、単語別の例外は追加していない。

固定モデルの自然文では、文節headと`bunsetu_span(token).root`が異なることがある。
例えば「『後漢書』（ごかんじょ）は、中国後漢朝について書かれた歴史書。」を含む
参照本文160967#0では、元head「朝」に対し既定APIは別の「後漢」を返す。
元headから同じ探索を行うと「中国後漢朝」を取得できる。

この変更は本プロジェクトによるもの。公式版との区別のため版を
`5.3.0+nativejapanese.1`とした。元のMITライセンスは[LICENSE](LICENSE)へ無改変で保持する。
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
利用時にpatch適用やwheelの再buildは行わない。元Tokenから句を読む抽出契約はv2とし、
参照DBも同じ契約で原本文から再生成する。

## 採用前の確認

公式wheelと改修wheelを別processで固定85本文へ適用した。Docの公開JSON、文節の
user_data、既定phrase範囲、`sub_phrases`列挙は85本文すべてで一致した。
明示Trueでは既存49本文の比較照会は不変、既知の範囲不整合がある36本文で36照会が
回復し、既存照会の削除はなかった。この限定確認から全入力の正答を保証しない。

既定APIを無条件の元Token起点へ変える1行修正は、文節内の助詞等を渡した場合の
既存契約も変えるため採らない。明示optionで既存pipelineの出力を維持する。

一次資料：

- [公式文節API](https://github.com/megagonlabs/ginza/blob/v5.3.0/docs/bunsetu_api.md)
- [既存の句探索](https://github.com/megagonlabs/ginza/blob/v5.3.0/ginza/bunsetu_recognizer.py#L120-L141)
- [spaCyのSpan.root](https://spacy.io/api/span#root)
