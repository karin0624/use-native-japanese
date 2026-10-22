#!/bin/bash
# setup-native.py buildから呼び、固定sourceを新規work/prefixだけで構築する。
set -euo pipefail
prefix=$1
assets=$2
work=$3
source_location=$4
bootstrap=$5
grammar=${6:-}
tools_dir=$(cd -- "$(dirname -- "$0")" && pwd)
revision=bc4cef188669f88cdeb590fe7afb1021ce2ae481
stage() { printf '工程: %s\n' "$1"; }
stage '固定sourceを新しい作業先へ展開'
git clone --shared --no-checkout "$source_location" "$work/knp"
git -C "$work/knp" checkout --detach "$revision"
[ "$(git -C "$work/knp" rev-parse HEAD)" = "$revision" ]
for patch_file in minimal-dpnd.patch non-cv-predicate-head.patch; do
  git -C "$work/knp" apply "$tools_dir/patches/$patch_file"
done
# 大容量モデルは展開せず、検証済みcacheの正本を参照する。
tar -xJf "$assets/jumanpp-2.0.0-rc4.tar.xz" -C "$work" --exclude='*/model/jumandic.jppmdl'
ln -s "$assets/jumandic.jppmdl" "$work/jumanpp-2.0.0-rc4/model/jumandic.jppmdl"
if [ -n "$bootstrap" ]; then
  stage '既存builderのautotools生成scriptだけを再利用（objectは再利用しない）'
  python3 - "$bootstrap" "$work/knp" <<'PY'
from pathlib import Path
import hashlib, json, os, shutil, sys
source, target = map(Path, sys.argv[1:])
names = {'configure', 'Makefile.in', 'aclocal.m4', 'config.h.in', 'ltmain.sh',
         'install-sh', 'missing', 'depcomp', 'compile', 'config.guess', 'config.sub',
         'mkinstalldirs', 'ylwrap'}
records = []
for parent, dirs, files in os.walk(source):
    dirs[:] = [x for x in dirs if x not in {'.git', '.deps', '.libs'}]
    for name in files:
        if name not in names and not name.endswith('.m4'):
            continue
        original = Path(parent) / name
        relative = original.relative_to(source)
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.is_symlink():
            output.unlink()
        shutil.copy(original, output)
        records.append({'path': str(relative), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()})
# m4より生成物が古く見えてmakeが再生成を繰り返すことを防ぐ。
for name in ['aclocal.m4', 'configure', 'config.h.in', 'Makefile.in']:
    for path in target.rglob(name):
        path.touch()
(target.parent / 'bootstrap-inputs.json').write_text(json.dumps(records, indent=2)+'\n')
PY
else
  stage 'autotoolsの生成'
  (cd "$work/knp" && ./autogen.sh)
fi
stage 'KNP configure'
cd "$work/knp"
./configure --enable-minimal-dpnd --with-included-juman --prefix="$prefix" CFLAGS='-O2' CXXFLAGS='-O2 -std=gnu++11'
stage 'KNPと必要な補助libraryをclean build'
for directory in cdb CRF++-0.58 crf distsim juman/darts juman/lib; do
  make -C "$directory" -j2
 done
make -C system -j2 knp make_db
make knprc
make -C rule -j2
make -C dict/gcf -j2
make -C dict/scode/bgh -j2
stage 'Juman++をclean build'
cmake -S "$work/jumanpp-2.0.0-rc4" -B "$work/jumanpp-build" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
  -DJPP_ENABLE_TESTS=OFF -DJPP_USE_PROTOBUF=OFF
cmake --build "$work/jumanpp-build" --target jumanpp_v2 -j2
stage '必要なruntimeを新しいprefixへ配置'
mkdir -p "$prefix/bin" "$prefix/etc" "$prefix/libexec/jumanpp" \
  "$prefix/share/juman/dic" "$prefix/share/knp/rule" \
  "$prefix/share/knp/dict/gcf" "$prefix/share/knp/dict/scode/bgh" \
  "$prefix/share/knp/dict/auto" "$prefix/share/knp/dict/ebcf" "$prefix/share/notices"
install -m 0755 system/knp "$prefix/bin/knp"
install -m 0755 "$work/jumanpp-build/src/jumandic/jumanpp_v2" "$prefix/bin/jumanpp"
install -m 0644 knprc "$prefix/etc/knprc"
if [ -n "$grammar" ]; then
  # 元のmake install対象9点を省略せず、検証済みの外部正本へ参照する。
  for name in JUMAN.grammar JUMAN.kankei JUMAN.katuyou JUMAN.connect \
      jumandic.mat jumandic.tab jumandic.dat jumandic.da jumandic.bin; do
    ln -s "$grammar/$name" "$prefix/share/juman/dic/$name"
  done
else
  for directory in juman/makemat juman/makeint juman/makepat; do
    make -C "$directory" -j2
  done
  make -C juman/dic -j2
  make -C juman/dic install
fi
install -m 0644 rule/*.data "$prefix/share/knp/rule/"
install -m 0644 dict/gcf/scase.db "$prefix/share/knp/dict/gcf/"
install -m 0644 dict/scode/bgh/bgh.db dict/scode/bgh/sm2code.db "$prefix/share/knp/dict/scode/bgh/"
ln -s "$assets/knp/auto/auto.db" "$prefix/share/knp/dict/auto/auto.db"
for name in cf.db cf.dat mrph2id.db case_soto.txt cfp.db cfcase.db case.db; do
  ln -s "$assets/knp/ebcf/$name" "$prefix/share/knp/dict/ebcf/$name"
done
ln -s "$assets/jumandic.jppmdl" "$prefix/libexec/jumanpp/jumandic.jppmdl"
install -m 0644 "$work/jumanpp-build/model/jumandic.config" "$prefix/libexec/jumanpp/"
# 同梱noticeは保持し、個別許諾の未確認はsetup文書へ残す。
install -m 0644 COPYING "$prefix/share/notices/KNP-COPYING"
install -m 0644 juman/COPYING "$prefix/share/notices/JUMAN-COPYING"
install -m 0644 CRF++-0.58/COPYING "$prefix/share/notices/CRF++-COPYING"
install -m 0644 cdb/debian/copyright "$prefix/share/notices/cdb-copyright"
if [ -f "$work/jumanpp-2.0.0-rc4/LICENSE" ]; then
  install -m 0644 "$work/jumanpp-2.0.0-rc4/LICENSE" "$prefix/share/notices/Juman++-LICENSE"
fi
stage '配置完了'
