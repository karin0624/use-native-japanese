# npm導入だけで使える配布

2026-10-25。[課題4](https://github.com/karin0624/use-native-japanese/issues/4)の実装前の判断を記録する。利用者はGit LFS管理とnpm同梱を承認した。言語処理・採点・参照の内容を変えず、導入と起動を製品側で扱う。

## 完成時の利用方法

公開前は`npm pack`で作ったtgzを、textlintと一緒に別のprojectへ導入する。ファイルには`npx textlint --rule native-japanese README.md`、範囲には`npx textlint --rule native-japanese "docs/**/*.md"`を使い、commandや資源pathの設定を不要にする。npm registryには公開しない。README・setupにはこの利用手順と必要な前提だけを記載し、構築や内部APIは開発者文書へ分ける。

## 管理と同梱

参照DB71,204,864bytesと未変更fasttext-ja-mini21,944,687bytesを、npm package内のassetsへGit LFSで追跡する。通常Gitにはpointerとsize/hash/出典manifest/帰属表示を保持する。LFSはGit履歴を軽くする仕組みであり、各変更版の実体保存・開発者とCIの取得費用は残る。[GitHub仕様](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)、[利用量の扱い](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)。

配布時には実体を取得し、npmのfilesを明示して同梱する。Git LFSの取得はCIのcheckoutで明示する。npm利用者はtarballから実体を受け取り、Git LFSを必要としない。pointerや異なるhashの資源をpack・起動前に拒否する。[npmのfiles](https://docs.npmjs.com/cli/v11/configuring-npm/package-json#files)、[checkout設定](https://github.com/actions/checkout#usage)。

参照は既存の1,025記事・6,463区間からの成果をそのまま使う。原文と構築用正本は同梱しない。モデルの上流CC BY-SA3.0とZenodo側の表示を保持し、派生DBは後継条件CC BY-SA4.0とする。記事ID・題名・URL・区間IDを別の出典manifestに収録する。製品コードの新たな公開ライセンスをこの作業で選定しない。

## Python実行環境を既存uvへ委ねる

公式npm配布は確認できなかった。`uv`というnpm名は別製品。実績ある`@manzt/uv`は2025-08の0.8.13が最新で、0.12.0更新PRが未mergeだった。その更新者のforkである`@dataiku/uv@0.12.0`は2026-07に公開され、OS別binaryをoptionalDependenciesとして配布する。継続保守の実績はuv本体ほど厚くないが、自前の取得・展開処理を足さずに済む最小候補として固定する。[元実装と更新PR](https://github.com/manzt/uv-npm/pull/2)、[採用候補](https://github.com/575-lab/uv-npm)、[実利用例](https://github.com/anywhere-labs/dsh-desktop/blob/main/dsh-desktop-next/src/profiles.ts)。

別候補tiny-runtime-injectorは最近の継続更新とDeepChatでの実利用があるが、5依存に加えdownload先・cache等を呼出側で管理する必要がある。今回の第一案にはしない。Python/uvの自前download、native parserの再実装、Docker必須化は加えない。

npm側は独自postinstallを置かず、最初のtextlint検査前にuvでmanaged CPython3.12.13と同梱lockの依存を準備する。既存uvの環境・download・整合性・排他処理を使う。初回準備は検査要求の120秒timerより前に行い、以後は同じ専用環境とworkerを再利用する。環境は利用者の書込み可能なcacheへ置く。lockとPython版の違う環境を混用しない。[managed Python](https://docs.astral.sh/uv/concepts/python-versions/)、[project環境](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path)。

`uv sync --locked --no-dev --no-install-project`で依存を準備し、同梱srcを作業ディレクトリとしてPythonの公開module起動を使う案を優先する。これにより自分のpackageのbuildと古いsource wheelのcacheを避ける。Pythonの初回取得とcompress-fasttext等の依存buildはuvに任せる。利用者へprepare手順を出さない。既存の外部command指定は開発・既存接続用として維持する。

候補wrapperとlockにwheelがある共通範囲はLinux glibc x64/arm64、macOS x64/arm64、Windows x64。Linuxは主要wheelに合わせglibc2.28以上、uvの条件はmacOS13以上・Windows10/Server2016以上。wrapperはGNU Linux用のためAlpine/musl、現lockに主要wheelがないWindows arm64/32bitを対応済みとはしない。実際の起動はCIとローカルで確認する。

## 残る仮説だけを検証する

1. 固定uv0.12.0が現lockを`--locked`で読み、managed Python3.12.13へ同期できる。失敗したら具体的な版・lock形式の原因を確認し、この方針を更新してから変更する。
2. repo外へのtgz導入から、PATHのPythonや既存project環境に頼らず実textlintをファイル・globへ適用できる。指摘・保留・位置は同じPython検査結果を保持する。
3. 同時要求で初期化やworkerを競合させず、初回準備失敗・資源改変・pointer混入を成功として隠さない。取得済み環境ではnetworkなしでも実行できる。言語精度の再測定は行わない。
4. npm同梱物に資源実体・検査実装・帰属が入り、原文・秘密・構築正本・一時環境が入らない。LFSから再取得した実体でも同じhashである。

実装はGPT-6.1 sol、完了前にfresh contextで単純さ・既存機能への委譲・必要なテスト・利用手順を確認する。検証済みの単位で日本語commitを作り、安全確認後にpush、PR作成、mergeまで進める。
