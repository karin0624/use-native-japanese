# 日本語の語彙選択・連語誤用の検出

語の組合せを用例に照らし、確認が必要な名詞・述語を原文位置付きで返すツールです。Juman++・KNP、既成の格フレーム、固定の名詞ベクトルを使い、Python API・CLI・textlintから利用できます。訂正案は未実装です。

まず[セットアップ](docs/setup.md)でPython依存・native解析器・外部資源をローカルに準備します。生成した設定を使い、派生索引を準備して本文を検査します。

```sh
. .venv/bin/activate
PREFIX=/path/to/native-runtime
DERIVED=/path/to/local/derived.sqlite3
native-japanese prepare --config "$PREFIX/etc/native-runtime.json" --cache "$DERIVED"
native-japanese lint --config "$PREFIX/etc/native-runtime.json" --cache "$DERIVED" input.txt
```

`lint`の入力はUTF-8の本文です。省略時は標準入力を読み、結果をJSONで返します。初期化と初回の用例群準備には別途費用がかかるため、繰返し検査には常駐APIか`serve`を使います。

- [Python常駐API・CLI・結果の読み方](docs/runtime.md)
- [出典付き用例のextract → import → --examples](docs/runtime.md#出典付き用例を追加する)
- [常駐JSONLとtextlint設定・Markdownの制約](docs/textlint.md)
- [現在の完成条件・実装と検証の状況](docs/implementation-plan.md)
- [実経路・用例追加による改善・容量と遅延の検証結果](docs/runtime-validation.md)

直接の用例があれば通過し、比較可能な用例群からの支持が暫定境界 `D < 0.35` なら確認を促します。比較不能は別状態です。Dは正誤確率ではなく、未観測だけで誤用とはしません。正用同士の意味差、文章のもっともらしさ、AI生成の判定は対象外です。

今回の完成条件に沿い、実際の検査経路と、所定形式の実用例追加による改善・費用の増え方を検証しました。別出典の222件を追加すると「世界的な大ヒット」への不要な指摘が消え、「重複を畳んでいる」への指摘は維持されました。追加保存は用法と出典の情報量に比例する構造で、段階的な取込・照会も測定しています。

全ての日本語で正答するものではありません。短文でも8〜9秒かかる例、解析が元の語を失う例、上流の出力形式を読めず処理に失敗する例が残っています。追加用例による群の更新や未追加語への一般化は未実装です。[検証結果](docs/runtime-validation.md)に改善しなかった事例も記載しています。

資源・原文・モデル・生成DBはGit外へ置きます。利用条件の未確認事項は[セットアップ](docs/setup.md#出自と確認範囲)を参照してください。

設計の根拠は[固定した実装・検証方針](docs/incremental-runtime.md)、[目的と設計](docs/design.md)、[先行研究](docs/prior-research.md)、[解析と正規化の接続](docs/parser-connection-check.md)、[全文照合と支持計算](docs/full-text-support-check.md)に残しています。
