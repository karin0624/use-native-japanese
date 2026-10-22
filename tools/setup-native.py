"""固定native資源の準備・別prefix build・起動前検査。解析や判定は行わない。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from urllib.request import urlopen

TOOLS = Path(__file__).resolve().parent
METADATA = TOOLS / "native-assets.json"
RESERVE = 512 << 20


def require(condition, message):
    if not condition:
        raise ValueError(message)


def metadata():
    return json.loads(METADATA.read_text())


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_asset(path, item):
    require(
        path.is_file() and path.stat().st_size == item["bytes"], f"資源size不一致: {item['id']}"
    )
    require(sha256(path) == item["sha256"], f"資源SHA-256不一致: {item['id']}")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def write_verified(stream, target, item):
    """既知size/hashとEOFを確認するまで完成pathへrenameしない。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    require(not partial.exists() and not partial.is_symlink(), "未完成fileを先に確認してください")
    count, digest = 0, hashlib.sha256()
    try:
        with partial.open("xb") as output:
            while chunk := stream.read(1 << 20):
                count += len(chunk)
                require(count <= item["bytes"], f"資源の展開量超過: {item['id']}")
                require(shutil.disk_usage(target.parent).free >= len(chunk) + RESERVE, "空容量不足")
                output.write(chunk)
                digest.update(chunk)
            require(
                count == item["bytes"] and digest.hexdigest() == item["sha256"],
                f"資源の固定size/SHA-256不一致: {item['id']}",
            )
            output.flush()
            os.fsync(output.fileno())
        require(not target.exists() and not target.is_symlink(), "完成先が既に存在します")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def fetch_zip(root, items, spec):
    """既存fsspec readahead＋zipfile取得器の条件付きRange経路を再利用する。"""
    import aiohttp
    import fsspec
    from fsspec.asyn import sync

    async def response_received(session, context, params):
        response = params.response
        if params.method == "HEAD":
            require(
                response.status == 200
                and int(response.headers.get("Content-Length", -1)) == spec["bytes"],
                "HEADのsize不一致",
            )
            require(
                response.headers.get("ETag") == spec["etag"]
                and response.headers.get("Last-Modified") == spec["last_modified"],
                "配布物identity不一致",
            )
            return
        require(response.status == 206, "Range応答がHTTP206ではありません")
        first, last = map(int, params.headers["Range"].removeprefix("bytes=").split("-"))
        require(
            response.headers.get("Content-Range") == f"bytes {first}-{last}/{spec['bytes']}",
            "Content-Range不一致",
        )
        require(int(response.headers.get("Content-Length", -1)) == last - first + 1, "応答長不一致")
        require(
            response.headers.get("Content-Encoding", "identity") == "identity", "予期しない転送圧縮"
        )
        require(
            response.headers.get("ETag") == spec["etag"]
            and response.headers.get("Last-Modified") == spec["last_modified"],
            "配布物identity不一致",
        )

    trace = aiohttp.TraceConfig()
    trace.on_request_end.append(response_received)
    fs = fsspec.filesystem(
        "https",
        skip_instance_cache=True,
        headers={"Accept-Encoding": "identity", "If-Match": spec["etag"]},
        allow_redirects=False,
        client_kwargs={
            "trust_env": True,
            "auto_decompress": False,
            "trace_configs": [trace],
            "timeout": aiohttp.ClientTimeout(total=90, connect=30, sock_read=30),
        },
    )
    try:
        with (
            fs.open(
                spec["url"], "rb", size=spec["bytes"], block_size=8 << 20, cache_type="readahead"
            ) as remote,
            zipfile.ZipFile(remote, allowZip64=True) as archive,
        ):
            for item in items:
                info = archive.getinfo(item["member"])
                require(
                    sum(x.filename == item["member"] for x in archive.infolist()) == 1,
                    "ZIP member名が重複しています",
                )
                require(
                    (info.file_size, info.compress_size, f"{info.CRC:08x}")
                    == (item["bytes"], item["compressed_bytes"], item["crc32"]),
                    "ZIP memberの固定値不一致",
                )
                with archive.open(info) as stream:
                    write_verified(stream, root / item["path"], item)
    finally:
        if fs._session is not None:
            sync(fs.loop, fs._session.close, timeout=1)


def prepare(args):
    data, root = metadata(), args.assets_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    reuse = dict(item.split("=", 1) for item in args.reuse)
    selected = [x for x in data["assets"] if not x.get("optional") or args.include_vector]
    require(set(reuse) <= {x["id"] for x in selected}, "未知または未選択の資源IDです")
    pending = []
    for item in selected:
        target = root / item["path"]
        if target.exists() or target.is_symlink():
            check_asset(target, item)
        elif item["id"] in reuse:
            original = Path(reuse[item["id"]]).resolve(strict=True)
            check_asset(original, item)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(original)
        else:
            pending.append(item)
    require(not args.offline or not pending, "未準備資源: " + ", ".join(x["id"] for x in pending))
    require(
        shutil.disk_usage(root).free >= sum(x["bytes"] for x in pending) + RESERVE,
        "展開用の空容量不足",
    )
    if pending:

        def deadline(signum, frame):
            raise TimeoutError("資源準備の15分上限")

        previous = signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, 900)
        try:
            fetch_zip(root, [x for x in pending if "member" in x], data["zip"]) if any(
                "member" in x for x in pending
            ) else None
            for item in pending:
                if "url" in item:
                    with urlopen(item["url"], timeout=90) as stream:
                        write_verified(stream, root / item["path"], item)
            for item in pending:
                if "tar_member" in item:
                    release = next(x for x in selected if x["id"] == "jumanpp-release")
                    with tarfile.open(root / release["path"], "r:xz") as archive:
                        info = archive.getmember(item["tar_member"])
                        require(
                            info.isfile() and info.size == item["bytes"],
                            "release model member不一致",
                        )
                        with archive.extractfile(info) as stream:
                            write_verified(stream, root / item["path"], item)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    save(
        root / "assets-manifest.json",
        {
            "contract": data["contract"],
            "metadata_sha256": sha256(METADATA),
            "assets": [
                {"id": x["id"], "path": str((root / x["path"]).resolve()), "sha256": x["sha256"]}
                for x in selected
            ],
        },
    )


def identity(path):
    stat = path.stat()
    return {
        "resolved": str(path.resolve()),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "device": stat.st_dev,
        "inode": stat.st_ino,
    }


def required_files(data):
    return [
        "bin/knp",
        "bin/jumanpp",
        "etc/knprc",
        "build.json",
        "libexec/jumanpp/jumandic.config",
        "libexec/jumanpp/jumandic.jppmdl",
        *["share/juman/dic/" + name for name in data["grammar_files"]],
        *["share/knp/rule/" + name + ".data" for name in data["rules"]],
        *["share/knp/dict/" + name for name in data["generated_dict"]],
        *[
            "share/knp/dict/" + x["path"].removeprefix("knp/")
            for x in data["assets"]
            if "member" in x and x["id"] != "wikipedia.dat.skip"
        ],
    ]


def configure(args):
    data, prefix, assets = metadata(), args.prefix.resolve(), args.assets_dir.resolve()
    proof = json.loads((prefix / "build.json").read_text())
    require(
        proof["contract"] == data["contract"]
        and proof["status"] == "complete"
        and proof["patches"] == data["knp"]["patches"]
        and proof["revision"] == data["knp"]["revision"]
        and proof["metadata_sha256"] == sha256(METADATA),
        "対応する完成build記録がありません",
    )
    config = prefix / "etc/knprc"
    require(all((prefix / name).is_file() for name in required_files(data)), "必須runtime file不足")
    for name in ("share/juman/dic", "share/knp/rule", "share/knp/dict"):
        require(str(prefix / name) in config.read_text(), "knprcが導入先に対応しません")
    for item in data["assets"]:
        if "member" in item and item["id"] != "wikipedia.dat.skip":
            check_asset(prefix / "share/knp/dict" / item["path"].removeprefix("knp/"), item)
    for relative, digest in data["generated_dict"].items():
        require(sha256(prefix / "share/knp/dict" / relative) == digest, "生成DBの固定hash不一致")
    for name, spec in data["grammar_files"].items():
        check_asset(prefix / "share/juman/dic" / name, {"id": name, **spec})
    model = next(x for x in data["assets"] if x["id"] == "jumanpp-model")
    check_asset(prefix / "libexec/jumanpp/jumandic.jppmdl", model)
    require(
        (prefix / "libexec/jumanpp/jumandic.config").read_text().splitlines()[0]
        == "--model=" + str(prefix / "libexec/jumanpp/jumandic.jppmdl"),
        "Juman++ model設定不一致",
    )
    files = [p for p in prefix.rglob("*") if p.is_file() and p.name != "native-runtime.json"]
    versions = {}
    for name, flag in [("knp", "-v"), ("jumanpp", "--version")]:
        result = subprocess.run(
            [str(prefix / "bin" / name), flag],
            capture_output=True,
            check=True,
            timeout=30,
            text=True,
        )
        versions[name] = (result.stdout + result.stderr).strip()
    save(
        prefix / "etc/native-runtime.json",
        {
            "schema": 1,
            "contract": data["contract"],
            "metadata_sha256": sha256(METADATA),
            "prefix": str(prefix),
            "assets_dir": str(assets),
            "options": ["-r", str(config), *data["options"]],
            "versions": versions,
            "files": [
                {"path": str(p.relative_to(prefix)), "sha256": sha256(p), **identity(p)}
                for p in files
            ],
        },
    )


def verify(args):
    data, record = metadata(), json.loads(args.config.read_text())
    require(
        record["schema"] == 1
        and record["contract"] == data["contract"]
        and record["metadata_sha256"] == sha256(METADATA),
        "runtime契約の不一致",
    )
    prefix = Path(record["prefix"])
    require(
        record["options"] == ["-r", str(prefix / "etc/knprc"), *data["options"]], "固定option不一致"
    )
    require(
        set(required_files(data)) <= {x["path"] for x in record["files"]}, "必須runtime記録不足"
    )
    for item in record["files"]:
        require(
            not Path(item["path"]).is_absolute() and ".." not in Path(item["path"]).parts,
            "runtime記録の相対pathが不正です",
        )
        path = prefix / item["path"]
        require(path.is_file(), f"runtime file不足: {item['path']}")
        if args.full:
            require(sha256(path) == item["sha256"], f"runtime hash不一致: {item['path']}")
            item.update(identity(path))
        else:
            require(
                identity(path) == {k: item[k] for k in identity(path)},
                f"runtime identity変更。完全verifyが必要: {item['path']}",
            )
    if args.full:
        save(args.config, record)
    print(json.dumps({"status": "verified", "contract": record["contract"], "full": args.full}))


def check_python(args):
    data = metadata()
    expected = data["python"] | (data["vector_python"] if args.vector else {})
    for name, pin in expected.items():
        require(version(name) == pin, f"依存版不一致: {name}")
    import_module("rhoknp")
    import_module("cdblib")
    require(
        hasattr(import_module("cohesion_tools.extractors"), "PasExtractor"), "公開PasExtractor不足"
    )
    if args.vector:
        load_fixed_vector(args.assets_dir.resolve())
    print(json.dumps({"status": "imported", "versions": expected}))


def load_fixed_vector(assets):
    """現行adapter同様、固定bytesだけを公開pickle loaderへ渡す。"""
    data = metadata()
    item = next(x for x in data["assets"] if x["id"] == "fasttext-ja-mini")
    with (assets / item["path"]).open("rb") as stream:
        payload = stream.read(item["bytes"] + 1)
    require(
        len(payload) == item["bytes"] and hashlib.sha256(payload).hexdigest() == item["sha256"],
        "許可した固定vectorモデルではありません",
    )
    for name, pin in data["vector_python"].items():
        require(version(name) == pin, f"依存版不一致: {name}")
    from compress_fasttext.models import CompressedFastTextKeyedVectors

    with tempfile.TemporaryDirectory(prefix="native-fixed-vector-") as directory:
        snapshot = Path(directory) / "fasttext-ja-mini"
        snapshot.write_bytes(payload)
        model = CompressedFastTextKeyedVectors.load(str(snapshot))
    require(model.vector_size == 300, "固定vectorの次元不一致")
    return model


def allocated_bytes(paths):
    total = 0
    for root in paths:
        if not root.exists():
            continue
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in files:
                path = Path(parent) / name
                try:
                    if not path.is_symlink():
                        total += path.stat().st_blocks * 512
                except FileNotFoundError:
                    # configure/compilerの一時fileは走査後に消えることがある。
                    continue
    return total


def build(args):
    data = metadata()
    require(args.timeout_seconds > 0, "buildの時間上限は正の秒数を指定してください")
    prefix, work, assets = args.prefix.resolve(), args.work_dir.resolve(), args.assets_dir.resolve()
    require(
        not prefix.exists() and not work.exists(),
        "build先とprefixは新しいdirectoryを指定してください",
    )
    require(not any(c.isspace() for c in str(prefix)), "KNP prefixの空白は未対応です")
    for name, digest in data["knp"]["patches"].items():
        require(sha256(TOOLS / "patches" / name) == digest, "patchの固定hash不一致")
    for item in data["assets"]:
        if not item.get("optional"):
            check_asset(assets / item["path"], item)
    if args.grammar_dir:
        for name, spec in data["grammar_files"].items():
            check_asset(args.grammar_dir.resolve() / name, {"id": name, **spec})
    work.mkdir(parents=True)
    free = shutil.disk_usage(work).free
    require(free >= (2 << 30) + RESERVE, "build上限2GiBと512MiBの空きを確保できません")
    command = [
        "bash",
        str(TOOLS / "build-native.sh"),
        str(prefix),
        str(assets),
        str(work),
        str(args.knp_source.resolve()) if args.knp_source else data["knp"]["url"],
        str(args.bootstrap_from.resolve()) if args.bootstrap_from else "",
        str(args.grammar_dir.resolve()) if args.grammar_dir else "",
    ]
    state = {
        "contract": data["contract"],
        "status": "building",
        "patches": data["knp"]["patches"],
        "revision": data["knp"]["revision"],
        "initial_free_bytes": free,
        "command": command,
        "metadata_sha256": sha256(METADATA),
        "build_script_sha256": sha256(TOOLS / "build-native.sh"),
        "toolchain": {
            name: subprocess.check_output([name, "--version"], text=True).splitlines()[0]
            for name in ["gcc", "g++", "make", "cmake", "autoconf", "automake"]
        },
        "maximum_seconds": args.timeout_seconds,
        "maximum_growth_bytes": 2 << 30,
    }
    save(work / "build-status.json", state)
    started = time.monotonic()
    with (work / "build.log").open("wb") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        reason = None
        growth, current_free = 0, free
        try:
            while process.poll() is None:
                growth = allocated_bytes([work, prefix])
                current_free = shutil.disk_usage(work).free
                if time.monotonic() - started >= args.timeout_seconds:
                    reason = f"buildの時間上限{args.timeout_seconds}秒"
                    break
                if growth > 2 << 30 or free - current_free > 2 << 30:
                    reason = "buildの容量増分2GiB上限"
                    break
                time.sleep(1)
        except Exception as error:
            reason = "build監視例外: " + type(error).__name__
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
        elapsed = time.monotonic() - started
        try:
            growth = allocated_bytes([work, prefix])
            current_free = shutil.disk_usage(work).free
        except Exception as error:
            reason = "build終了後の監視例外: " + type(error).__name__
        if elapsed >= args.timeout_seconds:
            reason = f"buildの時間上限{args.timeout_seconds}秒"
        if growth > 2 << 30 or free - current_free > 2 << 30:
            reason = "buildの容量増分2GiB上限"
        state.update(
            status="complete" if process.returncode == 0 and not reason else "failed",
            returncode=process.returncode,
            reason=reason,
            seconds=elapsed,
            growth_bytes=growth,
        )
        bootstrap = work / "bootstrap-inputs.json"
        if bootstrap.exists():
            state["bootstrap_inputs_sha256"] = sha256(bootstrap)
            state["bootstrap_inputs"] = json.loads(bootstrap.read_text())
        save(work / "build-status.json", state)
    require(
        state["status"] == "complete", "build停止。build-status.jsonとbuild.logを確認してください"
    )
    save(prefix / "build.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--assets-dir", type=Path, required=True)
    prepare_parser.add_argument("--reuse", action="append", default=[], metavar="ID=PATH")
    prepare_parser.add_argument("--offline", action="store_true")
    prepare_parser.add_argument("--include-vector", action="store_true")
    build_parser = commands.add_parser("build")
    for name in ("prefix", "assets-dir", "work-dir"):
        build_parser.add_argument("--" + name, type=Path, required=True)
    build_parser.add_argument("--knp-source", type=Path)
    build_parser.add_argument("--bootstrap-from", type=Path)
    build_parser.add_argument("--grammar-dir", type=Path)
    build_parser.add_argument("--timeout-seconds", type=int, default=2700)
    configure_parser = commands.add_parser("configure")
    configure_parser.add_argument("--prefix", type=Path, required=True)
    configure_parser.add_argument("--assets-dir", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--config", type=Path, required=True)
    verify_parser.add_argument("--full", action="store_true")
    python_parser = commands.add_parser("check-python")
    python_parser.add_argument("--vector", action="store_true")
    python_parser.add_argument("--assets-dir", type=Path)
    args = parser.parse_args()
    if args.action == "check-python" and args.vector:
        parser.error("--vectorには--assets-dirが必要です") if args.assets_dir is None else None
    try:
        {
            "prepare": prepare,
            "build": build,
            "configure": configure,
            "verify": verify,
            "check-python": check_python,
        }[args.action](args)
    except Exception as error:
        # 通信例外の本文にはproxy等が入り得るため、未知例外は型だけ出す。
        print(
            str(error) if isinstance(error, (ValueError, TimeoutError)) else type(error).__name__,
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
