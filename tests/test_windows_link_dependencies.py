"""ARM64 discovery setup uses only hashed inputs and the pure-Python backend."""

from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import windows_link_dependencies as dependencies


class Arm64DiscoverySetupTests(unittest.TestCase):
    def test_build_and_runtime_inputs_are_locked_without_native_compilation(self):
        environment = {"PATH": "test-path", "SKIP_CYTHON": "", "REQUIRE_CYTHON": "1"}
        with patch.object(dependencies.subprocess, "run") as run, \
                patch.object(dependencies, "report"):
            dependencies.install_arm64_link_dependencies("private-python", environment, "private-uv")
        build, runtime = run.call_args_list
        for call in (build, runtime):
            command = call.args[0]
            self.assertEqual(command[:6], ["private-uv", "--no-config", "pip", "install",
                                          "--python", "private-python"])
            self.assertIn("--require-hashes", command)
            self.assertIn("--no-deps", command)
            self.assertNotIn("--system", command)
            self.assertEqual(call.kwargs["env"]["SKIP_CYTHON"], "1")
            self.assertNotIn("REQUIRE_CYTHON", call.kwargs["env"])
            self.assertTrue(call.kwargs["check"])
        self.assertEqual(build.args[0][-4:], ["--only-binary", ":all:", "-r",
                                            str(dependencies.BUILD_REQUIREMENTS)])
        self.assertIn("--no-build-isolation", runtime.args[0])
        self.assertEqual(runtime.args[0][-6:], ["--no-binary", "zeroconf", "--only-binary",
                                              "ifaddr", "-r", str(dependencies.LINK_REQUIREMENTS)])
        self.assertEqual(environment["SKIP_CYTHON"], "")
        self.assertEqual(environment["REQUIRE_CYTHON"], "1")

    def test_ci_uses_explicit_python_and_the_same_locked_inputs(self):
        with patch.object(dependencies.subprocess, "run") as run, \
                patch.object(dependencies, "report"):
            dependencies.install_arm64_link_dependencies("ci-python", {})
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:6], ["ci-python", "-I", "-m", "pip", "--isolated", "install"])
            self.assertIn("--require-hashes", call.args[0])
        self.assertEqual(run.call_args_list[0].args[0][-1], str(dependencies.BUILD_REQUIREMENTS))
        self.assertEqual(run.call_args_list[1].args[0][-1], str(dependencies.LINK_REQUIREMENTS))

    def test_failed_build_dependency_install_stops_before_source_build(self):
        with patch.object(dependencies.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "synthetic")) as run, \
                patch.object(dependencies, "report"):
            with self.assertRaises(subprocess.CalledProcessError):
                dependencies.install_arm64_link_dependencies("private-python", {}, "private-uv")
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
