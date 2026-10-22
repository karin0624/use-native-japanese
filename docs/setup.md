# 採用済みnative解析器のセットアップ

Juman++ 2.0.0-rc4から、固定KNPの標準確率CV経路へ形態素列を渡す構成を準備します。KNPの既存非CV見出しを追加feature `非CV用言見出候補`（H）として公開する二patchを使います。Hはlookup前の候補列で、Nや全最終lookup keyと同一とは扱いません。このページは解析器と資源のローカル導入を扱います。導入後の検査は[本体の使い方](runtime.md)へ進んでください。大容量資源・モデル・原文・生成DBはGit外へ置きます。

## 現在使える準備コマンド

`tools/setup-native.py`は資源の準備・build・設定・verifyを行う独立scriptです。ここでの`prepare`は資源取得であり、本体の`native-japanese prepare`による派生索引の準備とは異なります。configure／verify／既存資源のoffline再利用にはPython 3.12の標準libraryだけを使います。ネットワーク取得時だけ既存pin `fsspec==2026.7.0`・`aiohttp==3.14.5` が必要です。

```sh
# Python 3.12以上。確認済みのgcc/g++でpure-cdbの拡張をbuildします。
CC=gcc CXX=g++ uv sync --locked --extra setup
. .venv/bin/activate
# repo外の共有cacheと、新規の作業先／prefixを指定します。
CACHE=/path/to/shared/native-assets
PREFIX=/path/to/new/native-runtime
WORK=/path/to/new/native-build
python3 tools/setup-native.py prepare --assets-dir "$CACHE" --include-vector
python3 tools/setup-native.py check-python --vector --assets-dir "$CACHE"
python3 tools/setup-native.py build --assets-dir "$CACHE" --prefix "$PREFIX" --work-dir "$WORK"
python3 tools/setup-native.py configure --assets-dir "$CACHE" --prefix "$PREFIX"
python3 tools/setup-native.py verify --config "$PREFIX/etc/native-runtime.json"
```

prepareの資源ID・相対path・サイズ・期待SHA-256・公式URLは [native-assets.json](../src/native_japanese/data/native-assets.json) が正本です。基本例は公式配布から必要資源を取得し、公式KNP sourceの固定commitからbuildします。`--include-vector`は許可したcompactモデルも準備します。KNP ZIPは既存のfsspec readahead＋zipfile経路を再利用し、全ZIPを保存せず必要memberだけ読みます。固定配布identity、条件付きRange／206、member size／CRC／EOFに加え、**既知SHAへ一致してから**`.part`をrenameします。資源準備は15分、展開残量＋512MiBの空きを要求します。既存fileのhash不一致を自動更新や別モデルで置き換えません。

## 既存資源を再利用する場合

既存fileはcacheの対応pathに置くか、prepareへ`--reuse 'ID=/path/to/existing/file'`を繰り返します。固定size/hashを照合した後にsymlinkで参照登録し、大容量の実体をコピーしません。例えば`--reuse 'cf.dat=/path/to/cf.dat'`を指定できます。全件を準備済みなら`--offline`で取得を禁止でき、不足資源は列挙して停止します。

buildの`--knp-source /path/to/fixed/knp`は既存checkout、`--bootstrap-from /path/to/generated/knp`は既存autotools生成script、`--grammar-dir /path/to/existing/share/juman/dic`は元install全9点を再利用する任意の指定です。既存資源を持たない初回には不要です。今回の実build確認はこの再利用経路で行っています。remote source取得・autogen・資源のnetwork取得・grammar生成まで一式を新規実行した証拠ではありません。差分は後述します。

## clean native build

既存Debian bookworm builderの依存は `build-essential autoconf automake libtool cmake zlib1g-dev ca-certificates git python3` です。Dockerはbuild手段でありruntime必須ではありません。モデル・大容量DBは準備した外部cacheを参照します。cache・grammarの参照元はruntimeの利用中も保持してください。既存prefix／workは上書きせず、新規directoryを要求します。KNPのprefixに空白を含むpathは現在未対応です。

固定sourceは `bc4cef188669f88cdeb590fe7afb1021ce2ae481`。`--knp-source`で既存checkoutを指定すると別cloneへ展開し、原本を変更しません。未指定時はmetadataの公式repoを使います。`tools/patches/`の `minimal-dpnd.patch` と `non-cv-predicate-head.patch` の固定hashを確認して適用します。前者は別配布資源をbuild時に要求しない既存調整で、公式optionではありません。日本語規則・格採点を変更するものではありません。

buildは既存と同じ `--enable-minimal-dpnd --with-included-juman`・`CFLAGS=-O2`・`CXXFLAGS=-O2 -std=gnu++11` とJuman++のRelease／tests off／protobuf offを使います。必要なlibrary、binary、rule、小DBをclean buildし、原binary／objectは再利用しません。`--grammar-dir`は同梱JUMANの元のmake install対象9点（grammar/kankei/katuyou/connectとjumandic.mat/tab/dat/da/bin）を固定hashへ照合し、全件を参照配置します。これを省略すると本来の`make -C juman/dic install`で9点を生成します。今回の確認は既存の文法directoryを保持して大容量語彙辞書の複製・再生成を避けた経路です。Juman++モデルと外部DBもsymlink、rule・生成小DB・binaryは新prefixに配置します。

同じ既存builderでautotools生成済みsourceがある場合は`--bootstrap-from /path/to/generated/knp`を追加できます。再利用するのはconfigure／Makefile.in／m4等の生成scriptだけで、採用した各fileのhashを`bootstrap-inputs.json`へ保存します。C/C++ sourceやobject／libraryはコピーしません。

開始前に2GiB＋512MiBの空きを確認し、buildのnative工程を既定45分・増分2GiBに制限します。時間は`--timeout-seconds`で正の秒数を指定できます。今回確認した再利用経路のnative工程は同じ新規workの続行を含め約29分30秒でした。停止時もwork内の`build.log`と`build-status.json`を保持し、設定を変えた無制限再試行はしません。完成時だけprefixへ`build.json`を保存します。Docker利用時はこのscriptを既存builder内で実行し、共有cacheと元sourceはread-onlyでmountします。proxy／CAの正規設定を維持し、資格情報や環境変数全体をログへ出しません。

## 資源と起動前検証

KNPのruntime辞書は11fileです。auto 1、gcf 1、scode 2、ebcf 7（cf.db/cf.dat/mrph2id.db/case_soto.txt/cfp.db/cfcase.db/case.db）。公式ZIPのruntime用8memberとsourceから生成する小DB3件を区別します。空のwikipedia.dat.skipは既存build入力としてmetadataに保持しています。

これにKNPのJUMAN文法／活用、必須17名のrule（sourceの生成出力19名を配置）、Juman++ model／config、native binaryと共有libraryが必要です。Juman++ modelとKNP用の同梱JUMAN文法は別資源です。configureは固定外部資源と生成DBのhash、必須file・rule・設定pathを検査し、各fileのhashと実path／identity、版本、build来歴を`etc/native-runtime.json`へ保存します。

固定optionsは`-r <生成knprc> -case -tab -para-scode none -analyze-deverbative-noun`です。非確率・非CV・case2・anaphoraへ変更しません。通常verifyはprocess開始前の軽いidentity検査で、変更があれば停止します。同じprefixでの明示的な完全検査には`verify --full`を使い、全hashへ一致した後だけidentity記録を更新します。数GBのhashを各文で再計算しません。生成設定とnative既定pathはprefixに対応するため、prefixの単純移設は未対応です。別pathへ導入する場合は新prefixへbuildしてください。

## Python依存

既存の`pyproject.toml`＋`uv.lock`による一つのPython環境へ採用依存を統合しました。core3は`rhoknp==1.8.2,pure-cdb==4.0.0,cohesion-tools==0.7.7`、現行vector直接5pinは`compress-fasttext==0.1.5,gensim==4.4.0,numpy==2.5.4,scipy==1.18.1,scikit-learn==1.9.1`です。取得依存はsetup extra、devは既存dev groupです。compactの旧11pin lockを出典として残り6間接pinもconstraintへ固定し、旧prototype依存は持ち込みません。

上記`uv sync --locked --extra setup`はdevを含む一環境として導入・import・固定モデルloadまで確認済みです。pure-cdbの拡張buildではPython既定CCのclangがhostにないため既存gcc/g++を明示しました。別venvでの以前の検証や、先行scratch環境での26packages解決は別の証拠です。製品のvenv分割や独立vectorプロセスを必須にしません。

準備済みの環境に対して、`python tools/setup-native.py check-python`でcoreのpinとimport、`--vector --assets-dir "$CACHE"`でvectorも確認できます。vector loadは現行adapterと同様、許可済み21,944,687 bytes／SHAを同一bytesで照合し、private snapshotだけを公開loaderへ渡します。任意pickleや隣接sidecar、自動別モデル取得はありません。起動前のnative設定検証と固定vectorのloaderは`native_japanese.resources`へ共通化し、setupと本体から利用しています。

## 出自と確認範囲

Juman++公式モデル込みrelease、KNP公式辞書ZIP、compactのZenodo record `10.5281/zenodo.4905385` を固定します。SHAは既存取得値を固定期待値として再利用し、公式公開SHAとの照合とは称しません。KNP／同梱JUMANのBSDと同梱CRF++/cdbのnoticeをprefixへ保持します。Juman++コードのApache 2.0表記と第三者の条件は元sourceも参照してください。今回の公式releaseにはrootの独立LICENSE fileがなく、全第三者noticeのruntime再配布用収集は完了扱いにしていません。KNP別配布DB、BGH、Juman++配布モデルへの個別条件の適用範囲は未確認です。compactはLiebl Bernhard, v1.0, 2021-06-07の圧縮版で、説明本文CC BY-SA 3.0と機械metadata CC BY 4.0の不一致をmetadataへ残します。ローカルsetupの実装成功を再配布条件の確定とは扱いません。

今回未試行の初回経路は、公式URLからの取得（同じ既知SHAへ照合）、remote cloneとautogen（固定commitと同じ二patch）、文法9点の生成（元の`make -C juman/dic install`）です。独自の辞書削減や別の日本語規則は追加していません。確認済み経路では、同じ固定sourceから新しいobject/library/native binary・rule・小DBを生成し、外部辞書・モデル・元install文法9点とautotools生成scriptを再利用しました。この新prefixでconfigure／verify、版本・空stdin・lddは全て成功しました。

fixtureは`python3 -m unittest discover -s tests -p test_native_setup.py -v`で実行します。固定hash不一致の完成公開拒否、既存資源を上書きしない参照再利用、任意pickleのload前拒否、検証後のruntime改変拒否、compiler一時fileの消滅でbuild監視を止めないことを確認します。実build・版本・空stdinの結果は作業報告へ保存し、本文解析や検出性能の証拠にはしません。
