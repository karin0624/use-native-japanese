# 目的と設計

技術文書などの日本語から、用例による支持が弱い語の組み合わせを検出する。AIが生成した文書をスクリプトで検査し、根拠付きの指摘を生成元へ返す用途を想定する。作者判定、文体の分野分類、自動置換は行わない。

## 採点の不変条件

1. 名詞・格・述語の関係を既存のGiNZAで抽出し、活用を揃える。サ変の「削除する」は述語全体として固定する。
2. 同じ述語・格で観測した名詞だけを比較する。別の動詞や形容詞の類似性では加点しない。
3. 直接の用例、名詞ベクトルの類似度、根拠の十分さを区別する。スコアと確信度は校正済み確率ではない。
4. 未見名詞も一般化できる一方、材料不足や曖昧な構文は未評価にする。用例なしを誤りと断定しない。
5. 修正候補は同じ名詞・格の実用例から別途取得する。候補の多さで原文の採点を変えない。

## 構成と根拠

Python 3.12、GiNZA 5.3.0、標準のja_ginza 5.3.0を使う。既存の構文解析を再利用し、Transformersや実行時LLMを必要としない。索引は標準ライブラリのSQLite、単語ベクトルは既存のfastText/Gensim形式を使う。CLIとJSON Linesの常駐ワーカーを提供する。Node側はtextlintの非同期ルールとMarkdownの位置写像を担う薄い接続層とする。

- [GiNZA 5.3.0](https://github.com/megagonlabs/ginza/tree/v5.3.0)、[文節API](https://github.com/megagonlabs/ginza/blob/v5.3.0/docs/bunsetu_api.md)
- [spaCyのバッチ処理](https://spacy.io/usage/processing-pipelines)
- [textlintルールAPI](https://github.com/textlint/textlint/blob/master/docs/rule.md)、[StringSource](https://github.com/textlint/textlint-util-to-string)
- [fastTextの学習済みベクトル](https://fasttext.cc/docs/en/crawl-vectors.html)
- [CSLS原論文](https://arxiv.org/abs/1710.04087)

コサイン類似度を基準方式とし、CSLSも切り替えて比較する。CSLSの背景集合は索引の名詞から作り、全述語で共有し、自己近傍を除く。評価データを背景集合へ追加しない。両方式の閾値は別々に設定する。CSLSの単一言語lintへの効果は実測前に保証しない。依存関係から学習したベクトルも同じword2vec形式で差し替えられるようにし、新たな学習方式は発明しない。

## データ

標準の締切は2023-01-01T00:00:00Z未満。Wikipediaは版の日時で選び、記事の作成日で現在の本文を古いと見なさない。小中規模はMediaWiki API、規模を増やす場合は履歴XMLから締切直前の版を選ぶ。本文、版ID、URL、日時、ライセンスを保持し、同じ文の重複で件数を水増ししない。

実動作の検証には2022年末のWikipediaとKubernetes日本語文書を利用する。固定したKubernetesコミットには日本語Markdown353件、約3MBがある。

- [Kubernetes固定コミット](https://github.com/kubernetes/website/commit/0140099063d84e56f5284a07f7d7057a4b2ec539)
- [同コミットのライセンス](https://github.com/kubernetes/website/blob/0140099063d84e56f5284a07f7d7057a4b2ec539/LICENSE)
- [Wikimedia 2014–2023年利用規約](https://foundation.wikimedia.org/w/index.php?oldid=98524&title=Policy:Terms_of_Use)

用例本文、加工索引、モデル、ソースコードの条件を区別する。取得物はローカルで保持し、リポジトリには取得方法と出典メタデータを残す。fastText公式モデルのCC BY-SA 3.0、Wikipedia旧版のCC BY-SA 3.0、Kubernetes文書のCC BY 4.0などを混同しない。

## 検証

対象の述語・格を維持した採点、活用とサ変、形容詞、受身の棄権、材料不足、Markdown強調、コード除外、UTF-16位置、常駐プロセス再利用を検証する。既存UD Japanese GSDで抽出を確認し、JCoLAは統語容認性の補助評価として扱う。他ツールとの不一致をそのまま誤りラベルにしない。人工的な例は回帰検証であり、性能の裏付けとは呼ばない。

初回ロード時間、起動後の処理時間、再検査時間を分ける。実装後に独立したcontextでリファクタ・テストの妥当性をレビューし、指摘を修正してから公開する。
