# 開発と資源の構築

通常の導入・実行は[セットアップ](setup.md)を参照してください。この文書は、リポジトリの開発、独自の参照構築、既存のPython接続を扱います。方式と検証範囲は[最新方針](compact-runtime-plan.md)、内部APIと用例形式は[Python APIとCLI](runtime.md)に記録しています。

## 開発環境

Python 3.12以上と`uv`を使い、リポジトリのルートで実行します。現行方式はGiNZA（NER除外）で、KNP/Juman++のnative buildや大容量格フレームを要求しません。

```sh
uv sync --locked --extra setup
. .venv/bin/activate
python tools/setup-resources.py --assets-dir /path/to/assets
```

GiNZA・ja_ginza・spaCy・Sudachi辞書とベクトル依存の版は`pyproject.toml`と`uv.lock`で固定します。解析モデル・辞書はPython依存として導入し、上のscriptは固定の小型fastTextモデルを準備します。原コーパス本文と構築用正本はGit外に置き、公開しません。配布用の固定モデルと参照DBはGit LFSで管理し、npm配布物には実体を同梱します。

Node.js 20.18以上を使い、textlint接続の開発・回帰試験はリポジトリのルートで実行します。

```sh
npm ci
npm test
```

人工workerによる試験は通信・位置・寿命の回帰確認で、日本語の正しさ・訂正精度・モデル速度の証拠ではありません。配布時にはGit LFSの実体を取得し、`npm pack`で作ったtgzをリポジトリ外へ導入して確認します。npm registryへの公開は行いません。[配布方針](npm-distribution-plan.md)も参照してください。

## 既存モデルの再利用

```sh
python tools/setup-resources.py --assets-dir /path/to/assets \
  --reuse fasttext-ja-mini=/path/to/existing/fasttext-ja-mini --offline
```

`--reuse ID=PATH`は固定size/SHA-256を確認してsymlinkで参照し、実体を複製しません。参照元は利用中も保持してください。`--offline`はモデルのネットワーク取得を禁止し、不足があれば停止します。Python依存の導入をoffline化する指定ではありません。既存fileの不一致は上書きや別モデルへの置換で解消しません。

資源のID・相対path・取得URL・期待size/hashは[native-assets.json](../src/native_japanese/data/native-assets.json)が正本です。対象は`fasttext-ja-mini`、21,944,687 bytesです。取得は既知size/hashとEOFを確認してから完成先へ公開し、モデル利用時にも同じbytesと依存版を検査します。任意のpickleや隣接sidecarは読み込みません。

モデルはLiebl Bernhard, Compressed fastText Embeddings, v1.0, 2021-06-07、[Zenodo 10.5281/zenodo.4905385](https://doi.org/10.5281/zenodo.4905385)の圧縮版です。従来の取得調査では説明本文のCC BY-SA 3.0と機械metadataのCC BY 4.0に不一致があり、圧縮前の厳密snapshotは未確認でした。SHAは保存済み取得値を固定期待値として使い、公式公開SHAとの照合とは称しません。導入成功を再配布条件の確認の代わりにはしません。公式Zenodoの公開MD5とは照合済みです。現在の配布資源の出自・帰属・利用条件は、同梱の[帰属表示](../packages/textlint-rule-native-japanese/THIRD_PARTY_NOTICES.md)を参照してください。

## 用例から参照を構築する

```mermaid
flowchart LR
  A[検査する原文] --> B[GiNZAで語・関係・位置を取得]
  B --> C[配布参照へ照合]
  C --> D[低支持の端点を位置付き指摘]
  E[別出典の用例本文] --> F[同じ解析で抽出・取込]
  F --> G[出典付き正本 archive]
  G --> H[compile]
  H --> I[Bloom集合＋群平均の配布参照]
  I --> C
```

```sh
. .venv/bin/activate
ASSETS=/path/to/assets
ARCHIVE=/path/to/examples.sqlite3
REFERENCE=/path/to/reference.sqlite3
native-japanese extract --source-id source-001 --source-url 'https://example.org/source' source.txt > /path/to/examples.jsonl
native-japanese import --examples "$ARCHIVE" /path/to/examples.jsonl
native-japanese prepare --examples "$ARCHIVE" --reference "$REFERENCE" --assets-dir "$ASSETS"
native-japanese lint --reference "$REFERENCE" --assets-dir "$ASSETS" input.txt
```

検査端末に用例全文DBや全出典を配る必要はありません。正本は更新・再compile時に使い、検査には配布参照とモデルを使います。用例形式・再構築・Pythonでの利用は[Python APIとCLI](runtime.md)を参照してください。

異なる組合せN、群G、次元d=300、保存する文字列量Lに対し、参照保存は `O(N log(1/p) + Gd + L)`、誤認率p固定では線形です。同じ組合せの頻度・出典だけを増やしても、配布集合と群平均は増えません。

自然参照1,024記事＋追加1記事の従来の測定では、約6.9万組合せ・1.9万群の配布参照が約71.2MB、Python本体・モデル・依存を含む検査環境が約913MBでした。後者はnpm配布で加わるuvやcacheを含む新たな総導入容量の測定ではありません。この規模では群平均の保存によって配布参照は正本より大きくなります。100万件のBloom単体2.89MBという部品値を総容量とは扱いません。モデル単体の容量も総導入容量ではなく、解析辞書・Python依存・配布参照・一時領域を含む実容量を別に確認します。

旧KNPのbuild・資源検証記録はGit履歴と[旧検証結果](runtime-validation.md)に保持し、現行の導入手順や成績へ転用しません。
