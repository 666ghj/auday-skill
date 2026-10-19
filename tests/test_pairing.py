"""Exercise the real hidden terminal and Keychain flow with a disposable synthetic key."""

import fcntl, json, os, pty, secrets, select, subprocess, sys, tempfile, termios, time, unittest, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from auday import bridge

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixture import make
from native_peer import peer_binary


@unittest.skipUnless(
    sys.platform == "darwin" and os.environ.get("AUDAY_TEST_KEYCHAIN") == "1",
    "Set AUDAY_TEST_KEYCHAIN=1 on macOS for the local Keychain test",
)
class PairingTests(unittest.TestCase):
    def test_hidden_password_and_keychain_reconnect(self):
        binary = str(bridge())
        password = "".join(
            secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(16)
        )
        account = "synthetic-pair-test-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="auday-pair-") as directory:
            make(Path(directory) / "source")
            server = subprocess.Popen(
                [str(peer_binary())],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            server.stdin.write(
                json.dumps(
                    {"root": str(Path(directory) / "source"), "password": password}
                )
                + "\n"
            )
            server.stdin.flush()
            port = json.loads(server.stdout.readline())["port"]
            master, slave = pty.openpty()

            def terminal_session():
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

            process = subprocess.Popen(
                [
                    binary,
                    "pair",
                    "--profile",
                    account,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                preexec_fn=terminal_session,
            )
            os.close(slave)
            captured = b""
            sent = False
            deadline = time.monotonic() + 15
            try:
                while time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            chunk = os.read(master, 8192)
                        except OSError:
                            break
                        if not chunk:
                            break
                        captured += chunk
                        if not sent and "不回显".encode() in captured:
                            self.assertFalse(
                                termios.tcgetattr(master)[3] & termios.ECHO
                            )
                            os.write(master, password.encode() + b"\n")
                            sent = True
                    if process.poll() is not None:
                        break
                self.assertEqual(process.wait(timeout=2), 0)
                self.assertNotIn(password.encode(), captured)
                payload = {
                    "host": "127.0.0.1",
                    "port": port,
                    "profile": account,
                    "request": {"op": "status"},
                }
                result = subprocess.run(
                    [binary, "request"],
                    input=json.dumps(payload) + "\n",
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertTrue(json.loads(result.stdout)["ok"])
                self.assertNotIn(password, result.stdout + result.stderr)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                os.close(master)
                server.terminate()
                server.communicate(timeout=5)
                subprocess.run(
                    [
                        "security",
                        "delete-generic-password",
                        "-s",
                        "ai.auday.workflow-link.experimental",
                        "-a",
                        account,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
