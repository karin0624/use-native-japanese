# 文書案内

| 読む目的 | 文書 |
| --- | --- |
| 導入する | [セットアップ](guide/setup.md) |
| 開発環境を用意する | [開発手順](guide/development.md) |
| 設定・APIを調べる | [textlint](reference/textlint.md)、[Python・CLI](reference/runtime.md) |
| 仕組みを理解する | [構成](design/architecture.md)、[用法照合](design/usage.md) |
| 研究の根拠と限界を調べる | [先行研究](research/prior-research.md) |
| 日本語の比較単位を調べる | [名詞と述語の比較](research/japanese-predicate-comparison.md) |

## 保存先と書式

| 種別 | 保存先 | テンプレート |
| --- | --- | --- |
| プロジェクト紹介 | ルートのREADME.md | 下記 |
| 導入・開発・操作 | guide/ | [手順](guide/README.md) |
| API・設定・形式 | reference/ | [参照仕様](reference/README.md) |
| 構成・機能設計 | design/ | [設計](design/README.md) |
| 先行研究・技術比較 | research/ | [調査知見](research/README.md) |
| 配布資源の帰属 | packageのTHIRD_PARTY_NOTICES.md | [帰属表示](reference/README.md#帰属表示) |

各情報の本文は一か所に置き、他の文書からリンクする。不要な節は省略する。

## プロジェクト紹介

```markdown
# プロジェクト名

何を誰のために提供するか。利用判断に必要な制限。

## 利用開始
導入手順へのリンク。

## 文書
読者別の入口。
```
