"""固定公式wheelへsource差分を適用し、標準wheelツールで再梱包する。"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

import patch_ng


def main():
    vendor = Path(__file__).resolve().parents[1] / "vendor" / "ginza"
    manifest = json.loads((vendor / "upstream.json").read_text())
    with tempfile.TemporaryDirectory(prefix="native-japanese-ginza-") as temporary:
        work = Path(temporary)
        original = work / f"ginza-{manifest['upstream_version']}-py3-none-any.whl"
        with urlopen(manifest["upstream_url"], timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != manifest["upstream_sha256"]:
            raise ValueError("GiNZA公式wheelのSHA-256が記録と一致しません。")
        original.write_bytes(data)
        subprocess.run(
            [sys.executable, "-m", "wheel", "unpack", str(original), "-d", str(work)],
            check=True,
        )
        unpacked = work / f"ginza-{manifest['upstream_version']}"
        patch = patch_ng.fromfile(str(vendor / "token-head.patch"))
        if patch is None or not patch.apply(strip=1, root=str(unpacked)):
            raise ValueError("GiNZAのsource差分を適用できませんでした。")
        original_info = unpacked / f"ginza-{manifest['upstream_version']}.dist-info"
        metadata = original_info / "METADATA"
        version_line = f"Version: {manifest['upstream_version']}\n".encode()
        content = metadata.read_bytes()
        if content.count(version_line) != 1:
            raise ValueError("GiNZA公式wheelの版情報が想定と異なります。")
        metadata.write_bytes(content.replace(version_line, f"Version: {manifest['version']}\n".encode()))
        original_info.rename(unpacked / f"ginza-{manifest['version']}.dist-info")
        subprocess.run(
            [sys.executable, "-m", "wheel", "pack", str(unpacked), "-d", str(work)],
            check=True,
            env={**os.environ, "SOURCE_DATE_EPOCH": str(manifest["source_date_epoch"])},
        )
        wheel = (work / manifest["wheel"]).read_bytes()
        digest = hashlib.sha256(wheel).hexdigest()
        if manifest.get("sha256") and digest != manifest["sha256"]:
            raise ValueError("再作成したGiNZA wheelのSHA-256が記録と一致しません。")
        (vendor / manifest["wheel"]).write_bytes(wheel)
        print(f"{manifest['wheel']}: sha256:{digest}")


if __name__ == "__main__":
    main()
