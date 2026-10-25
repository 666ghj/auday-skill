"""Windows connection security/interop tests, using synthetic secrets and peers."""

import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_link as link
import windows_credentials as credentials
from python_peer import PythonPeer

HAS_PSK = getattr(ssl, "HAS_PSK", False) and hasattr(ssl.SSLContext, "set_psk_client_callback")


def generated_password():
    return "".join(secrets.choice(link.ALPHABET) for _ in range(16))


class WindowsLinkBoundaries(unittest.TestCase):
    def test_password_normalization_and_weak_rejection(self):
        self.assertEqual(link.communication_key("23456789ABCDEFGH"),
                         link.communication_key("2345-6789 abcd-efgh"))
        self.assertEqual(len(link.communication_key("23456789ABCDEFGH")), 32)
        for password in [None, "password123", "A" * 16, "23456789ABCDEFGI"]:
            with self.assertRaises(link.LinkError):
                link.communication_key(password)

    def test_missing_psk_runtime_fails_closed(self):
        with mock.patch.object(ssl, "HAS_PSK", False, create=True):
            with self.assertRaisesRegex(link.LinkError, "python_313_with_tls_psk_required"):
                link.tls_context(generated_password())

    def test_probe_reports_interpreter_target_instead_of_host_architecture(self):
        for target in ("win-arm64", "win-amd64", "macosx-11.0-arm64"):
            with self.subTest(target=target), \
                    mock.patch.object(link, "tls_context"), \
                    mock.patch.object(link, "_zeroconf"), \
                    mock.patch.object(link.sysconfig, "get_platform", return_value=target), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(link.main(["probe"]), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["tlsPSK"])
            self.assertEqual(result["interpreterPlatform"], target)

    def test_pair_requires_real_local_console(self):
        with mock.patch.object(sys, "stdin", io.StringIO("must-not-read")):
            with self.assertRaisesRegex(link.LinkError, "controlling_local_terminal"):
                link.local_password()

    def test_stdin_limit_and_invalid_json(self):
        for value in ["[1]\n", "broken\n", "x" * (link.MAXIMUM + 1)]:
            with mock.patch.object(sys, "stdin", io.StringIO(value)):
                with self.assertRaisesRegex(link.LinkError, "invalid_stdin"):
                    link.stdin_object()

    def test_addresses_are_bounded_and_scoped(self):
        addresses = ["::", "224.0.0.1", "fe80::1", "fe80::1%2", "::1", "127.0.0.1", "127.0.0.1"]
        values = link._address_candidates(addresses, 1234)
        self.assertEqual(values[0], (socket.AF_INET, ("127.0.0.1", 1234)))
        self.assertIn((socket.AF_INET6, ("fe80::1", 1234, 0, 2)), values)
        self.assertEqual(len(values), 3)

    def test_peer_to_peer_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(link.LinkError, "same_wifi"):
            link.endpoint_candidates({"peerToPeer": True}, time.monotonic() + 1)

    def test_service_resolution_uses_scoped_addresses_and_port(self):
        info = mock.Mock(port=1234)
        info.parsed_scoped_addresses.return_value = ["fe80::1%2", "127.0.0.1"]
        client = mock.Mock()
        client.get_service_info.return_value = info
        module = mock.Mock()
        module.Zeroconf.return_value.__enter__ = mock.Mock(return_value=client)
        module.Zeroconf.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(link, "_zeroconf", return_value=module):
            result = link.endpoint_candidates({"service": "Auday Fixture"}, time.monotonic() + 1)
        self.assertEqual(result[0], (socket.AF_INET, ("127.0.0.1", 1234)))
        self.assertEqual(client.get_service_info.call_args.args,
                         (link.SERVICE_TYPE, "Auday Fixture." + link.SERVICE_TYPE))
        self.assertLessEqual(client.get_service_info.call_args.kwargs["timeout"], 1000)

    def test_missing_service_does_not_fall_back_to_arbitrary_host(self):
        client = mock.Mock()
        client.get_service_info.return_value = None
        module = mock.Mock()
        module.Zeroconf.return_value.__enter__ = mock.Mock(return_value=client)
        module.Zeroconf.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(link, "_zeroconf", return_value=module):
            with self.assertRaisesRegex(link.LinkError, "phone_service_not_found"):
                link.endpoint_candidates({"service": "Missing", "host": "127.0.0.1", "port": 1234},
                                         time.monotonic() + 1)

    def test_dns_resolution_has_deadline(self):
        with mock.patch.object(socket, "getaddrinfo", side_effect=lambda *a, **kw: time.sleep(0.4)):
            started = time.monotonic()
            with self.assertRaisesRegex(link.LinkError, "awaiting_phone"):
                link.endpoint_candidates({"host": "fixture.invalid", "port": 1234}, started + 0.05)
            self.assertLess(time.monotonic() - started, 0.3)

    def test_cli_failure_does_not_echo_secret_or_exception(self):
        password = generated_password()
        with mock.patch.object(link, "request", side_effect=RuntimeError(password)), \
             mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"password": password, "request": {"op": "status"}}))), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(link.main(["request"]), 1)
        self.assertNotIn(password, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["error"], "authentication_or_connection_failed")

    def test_invalid_credential_profile(self):
        for profile in ["", "../fixture", "x" * 65, None]:
            with self.assertRaises(credentials.CredentialError):
                credentials._target(profile)


@unittest.skipUnless(HAS_PSK, "Python 3.13+ with TLS-PSK required")
class WindowsPSKTests(unittest.TestCase):
    def call(self, peer, password=None, payload=None, timeout=2):
        return link.request({"host": "127.0.0.1", "port": peer.port},
                            password or peer.password, payload or {"op": "status"}, timeout)

    def test_real_tls_psk_and_identity(self):
        with PythonPeer(generated_password()) as peer:
            response = self.call(peer)
            self.assertTrue(response["ok"])
            self.assertEqual(response["transport"], {"tlsVersion": "0x0303", "cipherSuite": "0xccac"})
            self.assertEqual(peer.identities, [link.IDENTITY])
            self.assertEqual(peer.negotiated, [("TLSv1.2", link.CIPHER)])
            self.assertEqual(peer.requests, [{"op": "status"}])

    def test_tls_configuration_has_no_certificate_or_downgrade_fallback(self):
        context = link.tls_context(generated_password())
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_2)
        suites = [row["name"] for row in context.get_ciphers() if row["protocol"] != "TLSv1.3"]
        self.assertEqual(suites, [link.CIPHER])
        self.assertIsNone(context.keylog_filename)

    def test_wrong_password_releases_no_request(self):
        with PythonPeer(generated_password()) as peer:
            with self.assertRaisesRegex(link.LinkError, "authentication_or_connection_failed"):
                self.call(peer, generated_password())
            self.assertEqual(peer.requests, [])

    def test_plaintext_is_not_accepted(self):
        with PythonPeer(generated_password()) as peer:
            with socket.create_connection(("127.0.0.1", peer.port), timeout=1) as client:
                data = b'{"op":"status"}'
                client.sendall(struct.pack(">I", len(data)) + data)
                try:
                    result = client.recv(4096)
                except OSError:
                    result = b""
                self.assertNotIn(b"access", result)
            self.assertEqual(peer.requests, [])

    def test_large_response(self):
        response = {"ok": True, "text": "x" * 1500000}
        with PythonPeer(generated_password(), response=response) as peer:
            self.assertEqual(self.call(peer)["text"], response["text"])

    def test_frame_limits_truncation_and_invalid_json(self):
        frames = [(struct.pack(">I", 0), "invalid_frame_size"),
                  (struct.pack(">I", link.MAXIMUM + 1), "invalid_frame_size"),
                  (struct.pack(">I", 8) + b"{}", "connection_closed"),
                  (struct.pack(">I", 3) + b"bad", "invalid_response"),
                  (struct.pack(">I", 2) + b"[]", "invalid_response")]
        for frame, error in frames:
            with self.subTest(error=error), PythonPeer(generated_password(), raw_response=frame) as peer:
                with self.assertRaisesRegex(link.LinkError, error):
                    self.call(peer)

    def test_request_limit_prevents_connection(self):
        with PythonPeer(generated_password()) as peer:
            with self.assertRaisesRegex(link.LinkError, "request_too_large"):
                self.call(peer, payload={"padding": "x" * link.MAXIMUM})
            self.assertEqual(peer.requests, [])

    def test_read_timeout_does_not_resend(self):
        with PythonPeer(generated_password(), delay=1) as peer:
            started = time.monotonic()
            with self.assertRaises(link.LinkError):
                self.call(peer, timeout=0.15)
            self.assertLess(time.monotonic() - started, 0.7)
            self.assertEqual(len(peer.requests), 1)

    def test_failed_first_address_falls_back(self):
        with PythonPeer(generated_password()) as peer:
            dead = socket.socket()
            dead.bind(("127.0.0.1", 0))
            dead_port = dead.getsockname()[1]
            dead.close()
            candidates = [(socket.AF_INET, ("127.0.0.1", dead_port)),
                          (socket.AF_INET, ("127.0.0.1", peer.port))]
            with mock.patch.object(link, "endpoint_candidates", return_value=candidates):
                self.assertTrue(self.call(peer)["ok"])

    def test_cli_json_stdin_contract(self):
        password = generated_password()
        with PythonPeer(password) as peer:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/windows_link.py"), "request"],
                                    input=json.dumps({"password": password, "host": "127.0.0.1", "port": peer.port,
                                                      "request": {"op": "status"}, "timeout": 2}),
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(json.loads(result.stdout)["ok"])
            self.assertNotIn(password, result.stdout + result.stderr)

    @unittest.skipUnless(socket.has_ipv6, "IPv6 unavailable on host")
    def test_ipv6_loopback_connection(self):
        try:
            peer = PythonPeer(generated_password(), family=socket.AF_INET6).__enter__()
        except OSError:
            self.skipTest("IPv6 loopback unavailable on host")
        try:
            result = link.request({"host": "::1", "port": peer.port}, peer.password, {"op": "status"}, 2)
            self.assertTrue(result["ok"])
        finally:
            peer.__exit__()


@unittest.skipUnless(HAS_PSK and os.environ.get("AUDAY_TEST_MDNS") == "1",
                     "Set AUDAY_TEST_MDNS=1 for synthetic multicast discovery")
class WindowsMDNSTests(unittest.TestCase):
    def test_discover_resolve_and_authenticate_synthetic_service(self):
        from zeroconf import Zeroconf, ServiceInfo, IPVersion
        import zeroconf
        name = "Auday Synthetic " + uuid.uuid4().hex[:8]

        def real_discovery_socket(**options):
            # Keep this same-host fixture independent of macOS hosted VMs'
            # virtual LAN interfaces by using real IPv4 multicast on loopback.
            # Windows still exercises the production
            # All-interfaces configuration; no discovery answers are mocked.
            if sys.platform == "darwin":
                options.update(interfaces=["127.0.0.1"], ip_version=IPVersion.V4Only)
            return Zeroconf(**options)

        with PythonPeer(generated_password()) as peer, \
             real_discovery_socket(ip_version=IPVersion.All) as browser, \
             mock.patch.object(zeroconf, "Zeroconf", side_effect=real_discovery_socket):
            info = ServiceInfo(link.SERVICE_TYPE, name + "." + link.SERVICE_TYPE,
                               server="auday-synthetic-" + uuid.uuid4().hex + ".local.",
                               addresses=[socket.inet_aton("127.0.0.1")], port=peer.port)
            browser.register_service(info)
            try:
                self.assertIn(name, link.discover()["services"])
                response = link.request({"service": name}, peer.password, {"op": "status"}, timeout=5)
                self.assertTrue(response["ok"])
            finally:
                browser.unregister_service(info)


@unittest.skipUnless(HAS_PSK and sys.platform == "darwin" and shutil.which("swift"),
                     "Requires macOS Swift synthetic peer and Python 3.13 TLS-PSK")
class ApplePSKInteropTests(unittest.TestCase):
    def test_existing_apple_network_peer_hkdf_protocol_and_scope(self):
        from make_fixture import make
        from native_peer import peer_binary
        password = generated_password()
        with tempfile.TemporaryDirectory(prefix="auday-apple-interop-") as directory:
            make(Path(directory) / "source")
            server = subprocess.Popen([str(peer_binary())], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                server.stdin.write(json.dumps({"root": str(Path(directory) / "source"), "password": password,
                                               "access": "text-only-read-only"}) + "\n")
                server.stdin.flush()
                import select
                self.assertTrue(select.select([server.stdout], [], [], 15)[0])
                ready = json.loads(server.stdout.readline())
                endpoint = {"host": "127.0.0.1", "port": ready["port"]}
                response = link.request(endpoint, password, {"op": "status"}, timeout=5)
                self.assertTrue(response["ok"])
                self.assertEqual(response["access"], "text-only-read-only")
                self.assertEqual(response["transport"], {"tlsVersion": "0x0303", "cipherSuite": "0xccac"})
                self.assertEqual(link.request(endpoint, password, {"op": "delete", "id": "memory-1"})["error"],
                                 "read_only_operation_not_supported")
                self.assertFalse(link.request(endpoint, password, {"op": "audio", "id": "audio-demo"})["ok"])
                with self.assertRaises(link.LinkError):
                    link.request(endpoint, generated_password(), {"op": "status"}, timeout=2)
            finally:
                server.terminate()
                server.communicate(timeout=5)


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("AUDAY_TEST_CREDENTIALS") == "1",
                     "Set AUDAY_TEST_CREDENTIALS=1 on Windows for disposable Credential Manager test")
class WindowsCredentialTests(unittest.TestCase):
    def test_synthetic_secret_roundtrip_update_and_scoped_delete(self):
        profile = "synthetic-test-" + uuid.uuid4().hex
        password, updated = generated_password(), generated_password()
        saved = False
        try:
            credentials.save_secret(password, profile)
            saved = True
            self.assertEqual(credentials.read_secret(profile), password)
            credentials.save_secret(updated, profile)
            self.assertEqual(credentials.read_secret(profile), updated)
        finally:
            if saved:
                credentials.delete_secret(profile)
        with self.assertRaisesRegex(credentials.CredentialError, "pair_on_local_terminal_first"):
            credentials.read_secret(profile)


if __name__ == "__main__":
    unittest.main(verbosity=2)
