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

## 接続前の限定確認

Git外で`@dataiku/uv@0.12.0`をscripts無効で導入し、managed Python3.12.13の取得と、現lockに対する`sync --locked --no-dev --no-install-project --dry-run`が成功した。再解決は発生しなかった。実際の依存同期と検査起動は配布物で検証する。

wrapperの`bin.cjs`は内部でspawnSyncを使うため、外側Nodeの終了だけではuvを確実に回収できない。また初回のbinaryコピーはpackage領域に書込みを要求する。npm依存として配られたplatform packageの既存uv binaryを解決して直接起動する。独自のbinary取得や展開は行わず、薄い起動処理で有限の準備待ちと終了時回収を持つ。

実配布の確認で、`npm install --ignore-scripts`ではoptional packageのuvに実行権限がないことが分かった。既存wrapperの`runInstall()`は配置先を変更できず、node_modulesへのhardlink/copyとchmodを行う。この場合だけ、同梱済みbinaryを専用cacheへcopyし、実行権限を付けてからrenameで配置する。取得・展開・Python環境の構築は引き続き既存機能へ委ねる。

独立レビューでは、親NodeをSIGTERMで終了すると`exit` listenerが呼ばれず、子が残ることを再現した。[signal-exit 4.1.0](https://github.com/tapjs/signal-exit)の`onExit`へ委ね、通常終了に加えてsignal終了でも回収し、signal本来の終了状態を維持する。4.1.0の公開は2023年だが、上流には2025-10の更新があり、foreground-child 4.0.3にも利用されている。子が持つsocketの閉鎖と親のSIGTERM終了を確かめる回帰試験は、修正前に失敗し修正後に成功した。

## 配布物の検証結果

Linux x64、Node.js 24.19.0で、repo外へ実tgzを`npm install --ignore-scripts`で導入した。空cacheからのファイル検査は11.21秒、取得後に`UV_OFFLINE=1`で行ったglob検査は5.04秒だった。これは今回の2入力と環境での導入確認であり、一般的な初回所要時間・検出性能の測定ではない。managed CPython 3.12.13が専用cacheに入り、同梱Python APIと指摘・保留・UTF-16位置が一致した。

tgzは52,201,787bytes、展開した当該packageは94,103,785bytes・30ファイル。npmの依存、Python、初回取得cacheはこの容量に含まない。原コーパス・構築正本・一時環境を含まないこと、固定モデルと参照のhash、1,025記事・6,463区間の出典、ライセンス原文を検査した。Nodeの22テスト、Pythonの87テストと19subtest、Ruffが成功した。fresh contextで実装・テスト・利用手順を再レビューし、上記signal回収の修正後に追加の具体的不備は認められなかった。

macOSとWindowsは同じ配布物検証をGitHub Actionsで実行する。CI結果とLFS再取得の確認は、この節へ追記する。

## CIの目的と境界（2026-10-26）

実tgzを開発リポジトリ外へ導入する試験には、[Playwrightのinstallation tests](https://github.com/microsoft/playwright/blob/8c1b148f3d73120aa90f10119920358062fda177/tests/installation/globalSetup.ts)と[Puppeteerのinstallation tests](https://github.com/puppeteer/puppeteer/blob/08e7b1aebe60462b6f7834e9dfe0f6c70464247c/test/installation/src/puppeteer.test.ts)の先例がある。実依存の取得と利用者の入口を試す点は共通する。これを、日本語の精度評価や現スクリプトの全検査が一般的だという根拠にはしない。

通常の回帰試験と配布の確認を区別する。Nodeの試験に加え、既存のPythonテストとRuffもCIの明示したstepで実行する。導入試験では、実体と帰属資料の同梱、空cacheからの自動準備、textlintのファイル・glob処理、取得済み環境の再利用を確かめる。`UV_OFFLINE=1`はuvの取得禁止であり、OS全体の通信遮断ではない。

記事数・区間数・ライセンスファイル数・資源hashを試験へ重複記載せず、配布対象の正本と照合する。自然文の判定結果と範囲の固定値も外し、同じ参照を使った公開Python CLIとの出力一致を確認する。用例追加で判定が変わることを配布の不具合とみなさない。managed Pythonの版と専用領域は、利用者の既存Pythonに依存していないことを確かめる対象として残す。

Windowsの実起動では、深いcache内のspaCy拡張を読み込む際に長いパスのエラーを観測した。固定wheelのファイル名・manifestと[CPythonの既知issue #126929](https://github.com/python/cpython/issues/126929)を照合した。環境keyのSHA-256をhexからbase64urlへ表記変更し、全256bitと識別条件を保って21文字短縮する案を検証する。受け入れ試験の隔離先や期待値を変更して回避しない。

短縮後のWindows CIはDLL読込を通過し、次にJSONL入力で文字コードエラーを検出した。同じUTF-8入力をcp1252とsurrogateescapeで読むと、同じ文字位置・エラーを再現した。npmの標準起動に[Pythonの`-X utf8`](https://docs.python.org/3.12/using/cmdline.html#cmdoption-X)を明示し、JSONLのUTF-8契約をOSの既定値から独立させる。`-E`は維持し、環境変数による試験だけの回避や独自の文字コード変換を加えない。
