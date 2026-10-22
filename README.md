# 日本語の語彙選択・連語誤用の検出

AIが出した誤った日本語をリアルタイムで位置付き指摘し、可能なら訂正案を出すツールを開発しています。技術文書・一般文の不自然な語の組み合わせや語彙選択を重点に、スクリプトからの自動呼び出しとtextlintへの接続を想定しています。

現在は、既存解析から原語・格関係・原文位置を取得し、既成の用例資料へ照合する経路を確認しています。[単一の解析経路](docs/parser-connection-check.md)と[用法群内の平均による名詞側の支持](docs/frame-support-check.md)で[人手訂正9対の確認](docs/independent-pairs-check.md#完了結果)を終え、[一般文・技術文への指摘負担](docs/full-text-support-check.md)を確認する段階です。目的に合う検出器の成立はまだ実証していません。正用同士の意味差や名詞単体の未収録を、対象の連語誤用と混同しません。独自の構文規則を含む初期試作はscratchへ隔離済みです。

- [目的と現在の判断](docs/design.md)
- [検証した構成と未決事項](docs/architecture.md)
- [実装と検証の順序](docs/implementation-plan.md)
- [先行研究と完了した評価](docs/prior-research.md)
- [解析部品の調査](docs/parser-investigation.md)
