"""Private setup invariants with synthetic archives and mocked HTTPS responses."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_runtime as runtime


def digest(value):
    return hashlib.sha256(value).hexdigest()


class Response:
    """A bounded response whose reads may fail after already-delivered bytes."""

    def __init__(self, body=b"", *, status=200, headers=None, chunks=None,
                 url="https://example.invalid/component"):
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self.url = url
        self.chunks = iter(chunks if chunks is not None else [body, b""])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, _size):
        result = next(self.chunks, b"")
        if isinstance(result, BaseException):
            raise result
        return result


class RuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="auday-runtime-test-")
        self.root = Path(self.temporary.name)
        self.payload = b"Synthetic local runtime bytes."
        self.target = self.root / "download.bin"
        self.progress = patch.object(runtime, "report")
        self.progress.start()
        self.addCleanup(self.progress.stop)
        self.addCleanup(self.temporary.cleanup)
        self.no_sleep = patch.object(runtime.time, "sleep")
        self.no_sleep.start()
        self.addCleanup(self.no_sleep.stop)

    def download(self, **kwargs):
        return runtime.download_asset(
            "https://example.invalid/component", self.target,
            digest(self.payload), len(self.payload), **kwargs,
        )

    def test_interrupted_https_download_resumes_verified_partial(self):
        boundary = 10
        replies = [
            Response(chunks=[self.payload[:boundary], OSError("synthetic disconnect")]),
            Response(self.payload[boundary:], status=206, headers={
                "Content-Range": f"bytes {boundary}-{len(self.payload) - 1}/{len(self.payload)}",
            }),
        ]
        with patch.object(runtime.urllib.request, "urlopen", side_effect=replies) as opened:
            self.assertEqual(self.download(), self.target)
        self.assertIsNone(opened.call_args_list[0].args[0].get_header("Range"))
        self.assertEqual(opened.call_args_list[1].args[0].get_header("Range"), "bytes=10-")
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertFalse(self.target.with_name("download.bin.partial").exists())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        partial = self.target.with_name("download.bin.partial")
        partial.write_bytes(b"old-part")
        with patch.object(runtime.urllib.request, "urlopen",
                          return_value=Response(self.payload)) as opened:
            self.download()
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=8-")
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_wrong_range_never_publishes_or_appends(self):
        partial = self.target.with_name("download.bin.partial")
        partial.write_bytes(self.payload[:4])
        with patch.object(runtime.urllib.request, "urlopen", return_value=Response(
            self.payload[4:], status=206,
            headers={"Content-Range": f"bytes 0-{len(self.payload)-1}/{len(self.payload)}"},
        )):
            with self.assertRaisesRegex(ValueError, "invalid_download_range"):
                self.download()
        self.assertFalse(self.target.exists())
        self.assertEqual(partial.read_bytes(), self.payload[:4])

    def test_bad_hash_retains_evidence_without_publishing_final_file(self):
        corrupt = b"x" * len(self.payload)
        with patch.object(runtime.urllib.request, "urlopen", return_value=Response(corrupt)):
            with self.assertRaisesRegex(ValueError, "download_hash_mismatch"):
                self.download()
        self.assertFalse(self.target.exists())
        preserved = list(self.root.glob("download.bin.partial.corrupt-*"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), corrupt)

    def test_persistent_network_failure_is_bounded_and_keeps_partial(self):
        replies = [
            Response(chunks=[self.payload[:4], OSError("synthetic disconnect")]),
            OSError("still unavailable"),
            OSError("still unavailable"),
        ]
        with patch.object(runtime.urllib.request, "urlopen", side_effect=replies) as opened:
            with self.assertRaisesRegex(ValueError, "download_interrupted"):
                self.download()
        self.assertEqual(opened.call_count, 3)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.target.with_name("download.bin.partial").read_bytes(), self.payload[:4])

    def test_existing_verified_download_is_reused_and_user_change_is_preserved(self):
        self.target.write_bytes(self.payload)
        with patch.object(runtime.urllib.request, "urlopen") as opened:
            self.assertEqual(self.download(), self.target)
            opened.assert_not_called()
            self.target.write_bytes(b"user replacement")
            with self.assertRaisesRegex(ValueError, "existing_component_hash_mismatch"):
                self.download()
            opened.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"user replacement")

    def test_http_redirect_and_symlink_destinations_are_rejected(self):
        with patch.object(runtime.urllib.request, "urlopen", return_value=Response(
            self.payload, url="http://example.invalid/redirect",
        )):
            with self.assertRaisesRegex(ValueError, "insecure_download_redirect"):
                self.download()
        self.assertFalse(self.target.exists())
        original = self.root / "original"
        original.write_bytes(b"user-owned")
        for name in ("download.bin", "download.bin.partial"):
            with self.subTest(destination=name):
                path = self.root / name
                path.symlink_to(original)
                with patch.object(runtime.urllib.request, "urlopen") as opened:
                    with self.assertRaisesRegex(ValueError, "is_symlink"):
                        self.download()
                    opened.assert_not_called()
                self.assertEqual(original.read_bytes(), b"user-owned")
                path.unlink()

    def archive(self, entries):
        archive = self.root / "synthetic.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name, kind, content in entries:
                member = tarfile.TarInfo(name)
                if kind == "file":
                    member.size = len(content)
                    member.mode = 0o755
                    output.addfile(member, io.BytesIO(content))
                else:
                    member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                    member.linkname = content
                    output.addfile(member)
        return archive

    def test_tar_preserves_contained_dylib_symlink_and_executable(self):
        archive = self.archive([
            ("runtime/libggml.1.dylib", "file", b"synthetic dynamic library"),
            ("runtime/libggml.dylib", "symlink", "libggml.1.dylib"),
            ("runtime/tool", "file", b"synthetic executable"),
        ])
        output = runtime.extract_tar(archive, self.root / "unpacked")
        alias = output / "runtime/libggml.dylib"
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.read_bytes(), b"synthetic dynamic library")
        self.assertEqual((output / "runtime/tool").stat().st_mode & 0o111, 0o111)

    def test_tar_rejects_path_and_link_escape_without_leaving_output(self):
        outside = self.root / "outside"
        outside.write_bytes(b"keep this file")
        cases = [
            [("../outside", "file", b"overwrite")],
            [(str(outside), "file", b"overwrite")],
            [("runtime/link", "symlink", "../../outside")],
            [("runtime/link", "symlink", str(outside))],
            [("runtime/link", "hardlink", "../outside")],
            [("link", "symlink", "safe"), ("link/child", "file", b"written through link")],
            [("runtime/one", "symlink", "two"), ("runtime/two", "symlink", "one")],
        ]
        for entries in cases:
            with self.subTest(entries=entries):
                archive = self.archive(entries)
                destination = self.root / "unpacked"
                with self.assertRaises(ValueError):
                    runtime.extract_tar(archive, destination)
                self.assertFalse(destination.exists())
                self.assertEqual(outside.read_bytes(), b"keep this file")

    def test_extraction_preserves_existing_directory(self):
        destination = self.root / "unpacked"
        destination.mkdir()
        (destination / "personal-file").write_bytes(b"retain")
        archive = self.archive([("replacement", "file", b"replace")])
        with self.assertRaisesRegex(ValueError, "extraction_destination_exists"):
            runtime.extract_tar(archive, destination)
        self.assertEqual(list(destination.iterdir()), [destination / "personal-file"])
        self.assertEqual((destination / "personal-file").read_bytes(), b"retain")

    def test_modified_runtime_installation_is_not_overwritten(self):
        asset = runtime.runtime_asset("uv", "arm64")
        destination = self.root / "runtime" / asset["install_dir"]
        destination.mkdir(parents=True)
        (destination / "user-note").write_bytes(b"preserve")
        with patch.object(runtime, "runtime_asset", return_value=asset), \
                patch.object(runtime, "require_macos"), \
                patch.object(runtime, "download_asset") as downloaded:
            with self.assertRaisesRegex(ValueError, "existing_runtime_incomplete"):
                runtime.install_runtime(self.root, "uv")
        downloaded.assert_not_called()
        self.assertEqual((destination / "user-note").read_bytes(), b"preserve")

    def test_managed_environment_isolates_caches_and_interpreters(self):
        contaminated = {
            "PATH": "/user/tools:/usr/bin", "LANG": "en_US.UTF-8",
            "UV_CACHE_DIR": "/user/cache", "UV_PYTHON_INSTALL_DIR": "/user/python",
            "UV_PYTHON": "/user/venv/bin/python", "UV_PROJECT_ENVIRONMENT": "/user/venv",
            "UV_INDEX_URL": "https://example.invalid/custom", "PIP_CONFIG_FILE": "/user/pip.conf",
            "PYTHONPATH": "/user/injection", "PYTHONHOME": "/user/python",
            "PYTHONSTARTUP": "/user/startup.py", "VIRTUAL_ENV": "/user/venv",
            "CONDA_PREFIX": "/user/conda", "HF_HOME": "/user/hf", "TORCH_HOME": "/user/torch",
        }
        with patch.dict(os.environ, contaminated, clear=True):
            environment = runtime.managed_env(self.root)
        for key in ("UV_PROJECT_ENVIRONMENT", "UV_INDEX_URL", "PIP_CONFIG_FILE",
                    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "CONDA_PREFIX"):
            self.assertNotIn(key, environment)
        for key in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "HF_HOME", "TORCH_HOME"):
            self.assertTrue(Path(environment[key]).is_relative_to(self.root.resolve()))
        self.assertEqual(environment["UV_PYTHON"], runtime.PYTHON_VERSION)
        self.assertEqual(environment["UV_MANAGED_PYTHON"], "1")
        self.assertEqual(environment["UV_NO_CONFIG"], "1")
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        self.assertEqual(environment["LANG"], "en_US.UTF-8")

    def test_python_discovery_rejects_an_interpreter_outside_private_root(self):
        external = self.root / "unmanaged-python"
        external.write_bytes(b"synthetic interpreter")
        responses = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0, stdout=str(external)),
        ]
        with patch.object(runtime.subprocess, "run", side_effect=responses) as run:
            with self.assertRaisesRegex(ValueError, "python_runtime_outside_private_setup"):
                runtime.ensure_python(self.root, uv_path=self.root / "uv")
        self.assertEqual(run.call_count, 2)
        self.assertNotIn(str(external), run.call_args_list[0].args[0])
        self.assertEqual(external.read_bytes(), b"synthetic interpreter")

    def test_both_architectures_have_distinct_complete_runtime_entries(self):
        for component in ("uv", "llama"):
            arm = runtime.runtime_asset(component, "arm64")
            intel = runtime.runtime_asset(component, "x86_64")
            self.assertNotEqual(arm["url"], intel["url"])
            self.assertNotEqual(arm["sha256"], intel["sha256"])
            self.assertNotEqual(arm["install_dir"], intel["install_dir"])
            for asset in (arm, intel):
                self.assertTrue(asset["url"].startswith("https://"))
                self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(asset["size"], 0)
                self.assertFalse(Path(asset["archive_root"]).is_absolute())
        with self.assertRaisesRegex(ValueError, "unsupported_runtime_asset"):
            runtime.runtime_asset("uv", "unsupported")

    def test_macos_13_core_and_13_3_semantic_have_separate_boundaries(self):
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.platform, "mac_ver", return_value=("13.2", ("", "", ""), "")):
            runtime.require_macos("13.0")
            with self.assertRaisesRegex(ValueError, "requires_macos_13.3"):
                runtime.require_macos("13.3")
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.platform, "mac_ver", return_value=("13.3", ("", "", ""), "")):
            runtime.require_macos("13.3")
        with patch.object(runtime.sys, "platform", "linux"):
            with self.assertRaisesRegex(ValueError, "setup_requires_macos"):
                runtime.require_macos("13.0")

    @unittest.skipUnless(sys.platform == "darwin", "bootstrap is a macOS system-shell entrypoint")
    def test_bootstrap_check_needs_no_runtime_and_creates_no_private_files(self):
        data_root = self.root / "uncreated-data"
        # Copy only the shell entrypoint. If check accidentally falls through
        # into installation, a missing manifest prevents any real download.
        bootstrap = self.root / "bootstrap.sh"
        bootstrap.write_bytes((runtime.HERE / "bootstrap.sh").read_bytes())
        result = subprocess.run(
            ["/bin/sh", str(bootstrap), "--home", str(data_root), "check"],
            env={**os.environ, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["downloadsStarted"])
        self.assertFalse(data_root.exists())

    def test_swift_stub_or_old_compiler_is_not_connection_ready(self):
        results = [
            subprocess.CompletedProcess([], 0, stdout="selected-tools"),
            subprocess.CompletedProcess([], 0, stdout="swift-executable"),
            subprocess.CompletedProcess([], 0, stdout="Apple Swift version 5.8.1"),
        ]
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.subprocess, "run", side_effect=results), \
                patch.object(Path, "is_file", return_value=True):
            status = runtime.command_line_tools_status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["reason"], "swift_5_9_or_later_required")


if __name__ == "__main__":
    unittest.main()
