# システムの構成

## 構成と責務

| 部品 | 責務 |
| --- | --- |
| [textlintルール](../../packages/textlint-rule-preset-native-japanese/src/index.js) | 本文抽出、原文への位置変換、指摘表示 |
| [worker](../../packages/textlint-rule-preset-native-japanese/src/worker.js) | JSONL通信、要求と応答の対応、プロセス共有 |
| [runtime](../../packages/textlint-rule-preset-native-japanese/src/runtime.js) | uvによるPython環境の準備と起動 |
| [Parser](../../src/native_japanese/parser.py) | 原文・GiNZAのDoc・復元に必要な語彙情報を`Analysis`へ保持 |
| [Inspector](../../src/native_japanese/service.py) | 同じ`Analysis`を各判定機へ渡し、結果を集約 |
| [UsageJudge](../../src/native_japanese/service.py) | 用法照合用の局所viewを検証し、参照照会から支持を判定 |
| [比較view](../../src/native_japanese/comparison.py) | 用法照合で参照する元Token・辺・述語構成・関係・名詞特徴を同定 |
| [語彙保存](../../src/native_japanese/vocabulary.py) | 固定Vocab全資源と親付きの不変な差分世代を共有し、各解析時点のVocabへ独立復元 |
| [Examples](../../src/native_japanese/examples.py) | 原文・解析結果・全出典を保存し、再構築可能な索引を作成 |
| [Reference](../../src/native_japanese/reference.py) | 厳密な述語・関係索引、Bloom集合、名詞群平均の構築・読込 |

## データの流れ

### 本文の検査

```mermaid
---
config:
    flowchart:
        wrappingWidth: 320
---
flowchart TB
    document[/検査する文書/] --> extract["textlintルールで文書から<br/>検査する本文を取り出す"]
    extract -->|本文をJSONLで送る| parser["GiNZAで本文を解析し<br/>単語・品詞・係り受けを得る<br/>（解析器 Parser）"]
    subgraph judges_group["判定機"]
        judges["用例を根拠に<br/>語の使い方を判定する<br/>（用法判定機 UsageJudge）"]
    end
    parser -->|同じ原文と解析結果を<br/>各判定機へ渡す| judges
    reference[("用例から作った照合データ<br/>検査端末用のSQLiteデータベース")] --> judges
    judges --> aggregate["各判定機の照合結果と<br/>指摘をまとめる（Inspector）"]
    aggregate -->|照合結果と指摘文を<br/>本文内の位置とともに<br/>JSONLで返す| report["textlintルールで指摘位置を<br/>文書の原文に対応付け<br/>メッセージを表示する"]
```

解析器は受け取った本文を原文として保持し、単語の位置・品詞・活用・係り受けなど、GiNZAが返す全解析情報を保存します。解析結果を読み戻せるよう、解析時点の語の表記・属性・単語ベクトルなどの辞書データも保持します。これらを保持する同じ`Analysis`を各判定機へ渡します。用法照合では、入力と用例に共通の[比較条件](usage.md)を適用します。

### 参照の構築

```mermaid
---
config:
    flowchart:
        wrappingWidth: 320
---
flowchart TB
    subgraph build["構築環境"]
        source[/出典本文と出典ID・URL/] --> extract["GiNZAで出典本文を解析し<br/>出典情報とともに書き出す"]
        extract --> records[/原文・解析結果・出典情報<br/>JSONL/]
        records --> import["原文と解析結果を取り込み<br/>各出典に対応付ける"]
        import --> master[("正本SQLite<br/>原文・解析結果・全出典との対応")]
        master --> prepare["正本の用例から<br/>検査端末用の照合データを作る"]
    end
    prepare --> reference[("用例から作った照合データ<br/>検査端末用のSQLiteデータベース")]
    reference --> runtime["照合データを読み込み<br/>本文の検査に使う"]
```

書き出し・取り込み・照合データの作成には、CLIの`extract`・`import`・`prepare`を使います。配布する照合データには、述語の構成や語の組み合わせを検索する索引と、名詞の意味の近さを比べるための数値データを収めます。索引と類似度の使い分けは[照合手順](usage.md#支持判定)、保存形式は[API仕様](../reference/runtime.md#用例jsonl)を参照してください。

## 実行・配布

`Inspector`は解析器と参照を呼出し間で再利用します。位置単位と通信は[API仕様](../reference/runtime.md)、本文範囲は[textlint仕様](../reference/textlint.md)で定義します。

モデル・配布参照はGit LFSで管理し、npm配布物へ実体を同梱します。資源のID・ハッシュ・出典は[manifest](../../packages/textlint-rule-preset-native-japanese/assets/manifest.json)と[帰属資料](../../packages/textlint-rule-preset-native-japanese/THIRD_PARTY_NOTICES.md)にあります。原コーパス本文と正本は構築環境で管理します。

Pythonの取得と依存同期は既存uvへ委ね、初回だけ利用者cacheへ準備します。用例追加後は正本から配布参照を全量再構築し、検査器を起動し直します。

保存量は異なる用法数N、群数G、次元d、文字列量L、Bloom誤認率pに対し`O(N log(1/p) + Gd + L)`です。厳密な述語索引の文字列量もLに含めます。配布集合と群平均は重複を除いた用法から構築します。正本の原解析・出典対応と、全量再構築の費用は別に増えます。

## 採用理由

- GiNZAは必須依存のspaCy・Sudachiを通じて語・文節・依存関係を提供します。文節の取得には[公開文節API](https://github.com/megagonlabs/ginza/blob/v5.3.0/ginza/bunsetu_recognizer.py)を使います。
- [spaCy retokenizer](https://spacy.io/api/doc#retokenize)で識別子の境界を解析前に揃え、その境界で解析します。
- N–P関係のBloom集合は低確率の誤通過を許容して所属判定を圧縮し、名詞群平均は検査端末への全用例ベクトルの配布を省きます。P–P関係は厳密な実共起群の存在だけで判定します。
- [uv](https://docs.astral.sh/uv/concepts/python-versions/)へPythonの取得・依存同期を委ねます。npm側の起動処理は配布済みbinaryを解決します。
