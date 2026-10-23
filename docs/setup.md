# 解析器と固定モデルのセットアップ

Python 3.12以上と`uv`を使い、リポジトリのルートで実行します。現行方式はGiNZA（NER除外）で、KNP/Juman++のnative buildや大容量格フレームを要求しません。方式と検証範囲は[最新方針](compact-runtime-plan.md)、参照の構築・検査は[使い方](runtime.md)を参照してください。

```sh
uv sync --locked --extra setup
. .venv/bin/activate
python tools/setup-resources.py --assets-dir /path/to/assets
```

GiNZA・ja_ginza・spaCy・Sudachi辞書とベクトル依存の版は`pyproject.toml`と`uv.lock`で固定します。解析モデル・辞書はPython依存として導入し、上のscriptは固定の小型fastTextモデルを準備します。資源・原文・生成DBはGit外へ置いてください。

## 既存モデルの再利用

```sh
python tools/setup-resources.py --assets-dir /path/to/assets \
  --reuse fasttext-ja-mini=/path/to/existing/fasttext-ja-mini --offline
```

`--reuse ID=PATH`は固定size/SHA-256を確認してsymlinkで参照し、実体を複製しません。参照元は利用中も保持してください。`--offline`はモデルのネットワーク取得を禁止し、不足があれば停止します。Python依存の導入をoffline化する指定ではありません。既存fileの不一致は上書きや別モデルへの置換で解消しません。

資源のID・相対path・取得URL・期待size/hashは[native-assets.json](../src/native_japanese/data/native-assets.json)が正本です。対象は`fasttext-ja-mini`、21,944,687 bytesです。取得は既知size/hashとEOFを確認してから完成先へ公開し、モデル利用時にも同じbytesと依存版を検査します。任意のpickleや隣接sidecarは読み込みません。

## 出自と確認範囲

モデルはLiebl Bernhard, Compressed fastText Embeddings, v1.0, 2021-06-07、[Zenodo 10.5281/zenodo.4905385](https://doi.org/10.5281/zenodo.4905385)の圧縮版です。説明本文のCC BY-SA 3.0と機械metadataのCC BY 4.0に不一致があり、圧縮前の厳密snapshotは未確認です。SHAは保存済み取得値を固定期待値として使い、公式公開SHAとの照合とは称しません。導入成功を再配布条件の確定とは扱いません。

モデル単体の容量は総導入容量ではありません。解析辞書・Python依存・配布参照・一時領域を含む実容量は別に確認します。旧KNPのbuild・資源検証記録はGit履歴と[旧検証結果](runtime-validation.md)に保持し、現行の導入手順や成績へ転用しません。
