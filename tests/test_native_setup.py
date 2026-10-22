"""大容量取得や解析なしで、完成公開前の検証と既存資源保全を確かめる。"""

import argparse
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "native_setup", Path(__file__).parents[1] / "tools/setup-native.py"
)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
resources = importlib.import_module("native_japanese.resources")


def asset(payload):
    return {
        "id": "fixture",
        "path": "fixture",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


class NativeSetupTest(unittest.TestCase):
    def test_disappearing_compiler_temporary_does_not_stop_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def snapshot_then_remove(*args, **kwargs):
                temporary = root / "conftest.o"
                temporary.write_bytes(b"temporary")
                temporary.unlink()
                yield str(root), [], ["conftest.o"]

            with patch.object(setup.os, "walk", side_effect=snapshot_then_remove):
                self.assertEqual(setup.allocated_bytes([root]), 0)

    def test_hash_mismatch_never_publishes_completed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "complete"
            with self.assertRaisesRegex(ValueError, "不一致"):
                setup.write_verified(io.BytesIO(b"wrong"), target, asset(b"right"))
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".part").exists())
            setup.write_verified(io.BytesIO(b"right"), target, asset(b"right"))
            self.assertEqual(target.read_bytes(), b"right")

    def test_offline_reuse_is_a_reference_and_does_not_replace_bad_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            original.write_bytes(b"fixed")
            args = argparse.Namespace(
                assets_dir=root / "cache",
                reuse=[f"fixture={original}"],
                include_vector=False,
                offline=True,
            )
            data = {"contract": "fixture", "assets": [asset(b"fixed")]}
            with patch.object(setup, "metadata", return_value=data):
                setup.prepare(args)
                target = args.assets_dir / "fixture"
                self.assertTrue(target.is_symlink())
                self.assertEqual(target.resolve(), original)
                original.write_bytes(b"other")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    setup.prepare(args)
                self.assertEqual(original.read_bytes(), b"other")

    def test_untrusted_pickle_is_rejected_before_import_or_load(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "fasttext-ja-mini"
            model.write_bytes(b"arbitrary pickle")
            with self.assertRaisesRegex(ValueError, "固定vector"):
                resources.load_fixed_vector(Path(directory))

    def test_verified_runtime_change_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            binary = prefix / "binary"
            binary.write_bytes(b"original")
            config = prefix / "runtime.json"
            data = {"contract": "fixture", "options": []}
            setup.save(
                config,
                {
                    "schema": 1,
                    "contract": "fixture",
                    "metadata_sha256": resources.metadata_sha256(),
                    "prefix": str(prefix),
                    "options": ["-r", str(prefix / "etc/knprc")],
                    "files": [
                        {"path": "binary", "sha256": setup.sha256(binary), **setup.identity(binary)}
                    ],
                },
            )
            with (
                patch.object(resources, "metadata", return_value=data),
                patch.object(resources, "required_files", return_value=["binary"]),
            ):
                self.assertEqual(resources.read_runtime(config)["contract"], "fixture")
                binary.write_bytes(b"modified")
                with self.assertRaisesRegex(ValueError, "identity変更"):
                    resources.read_runtime(config)
                with self.assertRaisesRegex(ValueError, "hash不一致"):
                    resources.read_runtime(config, full=True)
                record = json.loads(config.read_text())
                self.assertEqual(
                    record["files"][0]["sha256"], hashlib.sha256(b"original").hexdigest()
                )


if __name__ == "__main__":
    unittest.main()
