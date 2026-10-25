"""Install the locked Windows ARM64 discovery stack without native compilation."""

import argparse
from pathlib import Path
import subprocess
import sys

from progress import report

HERE = Path(__file__).resolve().parent
BUILD_REQUIREMENTS = HERE / "requirements-windows-arm64-build.txt"
LINK_REQUIREMENTS = HERE / "requirements-windows-link.txt"


def install_arm64_link_dependencies(python, environment, uv=None):
    """Use zeroconf's official pure-Python build with no implicit build downloads.

    Runtime setup supplies its private uv and interpreter. CI may omit uv to use
    pip from the explicitly selected test interpreter with the same locked inputs.
    The caller owns the target environment and handles incomplete setup cleanup.
    """
    environment = dict(environment)
    environment["SKIP_CYTHON"] = "1"
    environment.pop("REQUIRE_CYTHON", None)
    if uv is not None:
        installer = [str(uv), "--no-config", "pip", "install", "--python", str(python)]
    else:
        installer = [str(python), "-I", "-m", "pip", "--isolated", "install",
                     "--disable-pip-version-check", "--no-input"]
    # Explicit PyPI and hashes prevent a user's package-manager configuration
    # from redirecting either stage to unverified build dependencies.
    common = ["--index-url", "https://pypi.org/simple", "--require-hashes", "--no-deps"]
    report("Preparing pure-Python Windows discovery dependencies", force=True)
    subprocess.run(
        installer + common + ["--only-binary", ":all:", "-r", str(BUILD_REQUIREMENTS)],
        env=environment, check=True, timeout=600, stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        installer + common + ["--no-build-isolation", "--no-binary", "zeroconf",
                              "--only-binary", "ifaddr", "-r", str(LINK_REQUIREMENTS)],
        env=environment, check=True, timeout=600, stdout=subprocess.DEVNULL,
    )


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Explicit target test/private interpreter")
    parser.add_argument("--home", required=True, help="Private setup/cache root")
    parser.add_argument("--uv", help="Verified private uv executable; otherwise use target pip")
    args = parser.parse_args(arguments)
    from setup_runtime import managed_env

    install_arm64_link_dependencies(args.python, managed_env(args.home), args.uv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
