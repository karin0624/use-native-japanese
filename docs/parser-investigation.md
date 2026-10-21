# 解析基盤の再調査

調査日: 2026-10-22（日本時間）。採用判断は継続中。

## 判断基準

形態素、係り受け、述語の単位と正規化、格の解釈を既存ツールへ委ねる。アプリケーションに日本語の構文規則を実装しない。述語IDは解析器が提供する安定した値でよく、「削除する」という文字列そのものを独自に組み立てる必要はない。ただし、異なる動作を同じ述語として救済しない。

公式APIだけで結論を出さず、公開ソース、issueと回答、PR、利用例、論文、配布条件、実際の出力を照合する。検索で見つからなかった機能は「不可能」ではなく「未確認」と記録する。少数例の動作確認と、未知の文書での性能評価を区別する。

## 候補と確認結果

| 候補 | 確認できたこと | 採用前の確認事項 |
| --- | --- | --- |
| GiNZAの文節API | [公式の述語・項抽出例](https://github.com/megagonlabs/ginza/blob/v5.3.0/docs/bunsetu_api.md)を変更せず実行できる。「削除する」のキーは「削除」、「情報を正しくする」では「正しい」と「する」が別に出る | 公式例の出力だけでは、今回固定したい述語単位と一致するとは限らない。独自の結合規則で補完しない |
| Juman++・KNP・rhoknp | KNPは用言代表表記や格の情報を出力する。[rhoknp](https://github.com/ku-nlp/rhoknp)に解析結果を読むAPIがある | 公式配布物での実出力、表層格と意味格の区別、CPU速度、辞書の条件を検証中 |
| KNP・pyknp-eventgraph | [述語API](https://github.com/ku-nlp/pyknp-eventgraph/blob/main/pyknp_eventgraph/predicate.py)に`normalized_reps`、`standard_reps`がある。KNPの代表表記があれば利用する | KNPの出力を直接読む方法より、この依存追加が必要かを確認する |
| KWJA・rhoknp | 既存APIで述語項構造を取得できる。現行設定はガ・ヲ・ニ・ガ２・デ・ト・時間を扱う | 述語全体の安定したIDへの接続、CPU性能、配布チェックポイントの条件が未確認 |
| KWJA・pyknp-eventgraph | [2022年の公式論文](https://murawaki.org/pubdb/NL253ueda.pdf)に利用例がある | 現行公開版ではKWJAの`rel`出力からEventGraphの項へ接続する経路を確認できていない。論文の実績を否定せず、接続方法の未確認として扱う |
| desuwa | [述語抽出API](https://github.com/megagonlabs/desuwa/blob/main/desuwa/predicate.py)がKNP由来の既存素性を利用する | [対応処理](https://github.com/megagonlabs/desuwa/blob/main/README.md)は形態素・基本句の素性付与で、依存構造解析は別途必要。リポジトリはアーカイブ済み、公開依存条件はPython 3.8以上3.11未満 |

KNPの`用言代表表記`と`標準用言代表表記`は同じ機能ではない。前者は係り受けモードにも生成処理があり、後者は格フレーム選択に関わる。モードを変えても同じ出力が得られると仮定しない。

## issueから分かったこと

- [GiNZA #167](https://github.com/megagonlabs/ginza/issues/167#issuecomment-906048577): 受動態の解析報告には、UD r2.8とGiNZA v5で改善したという開発者回答がある。古い報告を現行版の未解決問題として扱わない。
- [GiNZA #139](https://github.com/megagonlabs/ginza/issues/139#issuecomment-689934796): 固有表現と文節の不一致は上流で修正されている。アプリ側の修復規則を追加する根拠にはならない。
- [KNP #2](https://github.com/ku-nlp/knp/issues/2#issuecomment-978813492): 半角空白などの予約文字は新しいJuman++とKNPで対応されている。独自の文字置換を追加する前に現行版で確認する。
- [KNP #9](https://github.com/ku-nlp/knp/issues/9#issuecomment-872014083): 活用情報の不一致による異常終了は、標準の`--with-included-juman`構成で解決したと報告されている。
- [KNP #6](https://github.com/ku-nlp/knp/issues/6): 古いマニュアルとコマンドのオプションに差がある。実行する版のヘルプと出力を確認する。
- [KWJA #225](https://github.com/ku-nlp/kwja/issues/225): 複合動詞を分割したいという利用例であり、今回の「述語を保持する」要件に適合しない証拠ではない。

desuwaの公開issue、依存更新を除いたPR、外部利用例も検索した。係り受け・格解析を提供する経路は確認できず、KNPを直接使う構成より単純になる根拠は今のところない。

## 利用条件の扱い

コード、解析モデル、辞書、参照コーパスを区別する。別配布のGSK京都大学格フレームの条件を、KNPに同梱される辞書の条件だと推定しない。KWJAのコードはMITだが、元となるDeBERTaモデルの公式カードはCC BY-SA 4.0であり、配布チェックポイントまでMITだとは説明しない。

この調査中は独自規則を含む旧抽出器で索引や性能評価を更新しない。既存ツールへの置き換えを確認してから、旧規則を削除し、既存の評価資料を用いた検証へ進む。
