#!/usr/bin/env python3
"""Windows Auday link: the existing iPhone TLS-PSK protocol, with no app changes.

Requires Python 3.13+ built with PSK and the pinned discovery dependencies.
Protocol functions remain importable on other hosts for synthetic interop tests.
"""

import argparse
import hmac
import ipaddress
import json
import math
import queue
import socket
import ssl
import struct
import sys
import threading
import time

from windows_credentials import CredentialError, read_secret, save_secret

MAXIMUM = 2 * 1024 * 1024
IDENTITY = "auday-workflow-link-v2"
CIPHER = "ECDHE-PSK-CHACHA20-POLY1305"
SERVICE_TYPE = "_auday-link._tcp.local."
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class LinkError(Exception):
    pass


def communication_key(password):
    if not isinstance(password, str):
        raise LinkError("use_generated_16_character_communication_password")
    normalized = "".join(char for char in password.upper() if not char.isspace() and char != "-")
    if (len(normalized) != 16 or any(char not in ALPHABET for char in normalized)
            or len(set(normalized)) < 4):
        raise LinkError("use_generated_16_character_communication_password")
    # HKDF-SHA256, exactly matching CryptoKit in the iPhone and macOS client.
    extracted = hmac.digest(IDENTITY.encode(), normalized.encode(), "sha256")
    return hmac.digest(extracted, b"tls-psk\x01", "sha256")


def tls_context(password):
    key = communication_key(password)
    if not getattr(ssl, "HAS_PSK", False) or not hasattr(ssl.SSLContext, "set_psk_client_callback"):
        raise LinkError("python_313_with_tls_psk_required_run_setup")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # This single PSK-authenticated suite does not send certificates. These two
    # settings enable PSK; they never allow an unauthenticated certificate suite.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.set_ciphers(CIPHER)
        context.set_psk_client_callback(lambda hint: (IDENTITY, key))
    except (ssl.SSLError, NotImplementedError):
        raise LinkError("python_313_with_tls_psk_required_run_setup") from None
    return context


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LinkError("awaiting_phone_or_wrong_password")
    return remaining


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 300:
        raise LinkError("invalid_timeout")
    return float(value)


def _zeroconf():
    try:
        import zeroconf
    except ImportError:
        raise LinkError("discovery_dependencies_missing_run_setup") from None
    return zeroconf


def discover(timeout=3):
    module = _zeroconf()
    services = set()
    lock = threading.Lock()

    class Listener:
        def add_service(self, client, type_, name):
            if name.endswith(SERVICE_TYPE):
                with lock:
                    services.add(name[:-len(SERVICE_TYPE)].removesuffix("."))

        def update_service(self, client, type_, name):
            self.add_service(client, type_, name)

        def remove_service(self, client, type_, name):
            with lock:
                services.discard(name[:-len(SERVICE_TYPE)].removesuffix("."))

    try:
        with module.Zeroconf(ip_version=module.IPVersion.All) as client:
            browser = module.ServiceBrowser(client, SERVICE_TYPE, listener=Listener())
            try:
                threading.Event().wait(_timeout(timeout))
            finally:
                browser.cancel()
        with lock:
            return {"services": sorted(services), "authenticated": False}
    except (OSError, RuntimeError):
        raise LinkError("local_network_discovery_failed") from None


def _address_candidates(addresses, port):
    candidates = []
    for address in addresses:
        try:
            base, *scope = address.split("%", 1)
            parsed = ipaddress.ip_address(base)
            if parsed.is_unspecified or parsed.is_multicast:
                continue
            if parsed.version == 4:
                candidate = (socket.AF_INET, (str(parsed), port))
            else:
                scope_id = 0
                if scope:
                    scope_id = int(scope[0]) if scope[0].isdigit() else socket.if_nametoindex(scope[0])
                if parsed.is_link_local and not scope_id:
                    continue
                candidate = (socket.AF_INET6, (str(parsed), port, 0, scope_id))
            if candidate not in candidates:
                candidates.append(candidate)
        except (ValueError, OSError):
            continue
    # IPv4 is usually reachable across a shared Wi-Fi. Keep scoped IPv6 as a
    # fallback instead of spending the whole timeout on an unreachable family.
    return sorted(candidates, key=lambda item: item[0] != socket.AF_INET)[:16]


def endpoint_candidates(destination, deadline):
    if destination.get("peerToPeer"):
        raise LinkError("windows_requires_same_wifi_peer_to_peer_unavailable")
    name = destination.get("service")
    if name is not None:
        if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 63 or any(ord(char) < 32 for char in name):
            raise LinkError("invalid_service_name")
        module = _zeroconf()
        try:
            with module.Zeroconf(ip_version=module.IPVersion.All) as client:
                info = client.get_service_info(SERVICE_TYPE, name + "." + SERVICE_TYPE,
                                               timeout=max(1, int(_remaining(deadline) * 1000)))
                addresses = info.parsed_scoped_addresses() if info else []
                candidates = _address_candidates(addresses, info.port) if info else []
        except (OSError, RuntimeError):
            raise LinkError("local_network_discovery_failed") from None
        if not candidates:
            raise LinkError("phone_service_not_found_keep_connection_page_open")
        return candidates
    host, port = destination.get("host"), destination.get("port")
    if (not isinstance(host, str) or not host or len(host) > 253 or "\x00" in host
            or isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536):
        raise LinkError("missing_endpoint")
    direct = _address_candidates([host], port)
    if direct:
        return direct
    # getaddrinfo itself has no timeout. A daemon thread bounds the caller;
    # the CLI exits after this one request even if a resolver is unresponsive.
    resolved = queue.Queue(maxsize=1)

    def resolve():
        try:
            resolved.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            resolved.put(None)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        rows = resolved.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise LinkError("awaiting_phone_or_wrong_password") from None
    if not rows:
        raise LinkError("authentication_or_connection_failed")
    candidates = []
    for family, _, _, _, address in rows:
        if family in (socket.AF_INET, socket.AF_INET6) and (family, address) not in candidates:
            candidates.append((family, address))
    return sorted(candidates, key=lambda item: item[0] != socket.AF_INET)[:16]


def _read_exact(connection, size, deadline):
    result = bytearray()
    while len(result) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(min(64 * 1024, size - len(result)))
        if not chunk:
            raise LinkError("connection_closed")
        result.extend(chunk)
    return bytes(result)


def receive_frame(connection, deadline):
    size = struct.unpack(">I", _read_exact(connection, 4, deadline))[0]
    if not 0 < size <= MAXIMUM:
        raise LinkError("invalid_frame_size")
    try:
        response = json.loads(_read_exact(connection, size, deadline))
    except (ValueError, UnicodeError, RecursionError):
        raise LinkError("invalid_response") from None
    if not isinstance(response, dict):
        raise LinkError("invalid_response")
    return response


def request(destination, password, payload, timeout=10):
    if not isinstance(payload, dict):
        raise LinkError("missing_request")
    try:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise LinkError("invalid_request") from None
    if not 0 < len(data) <= MAXIMUM:
        raise LinkError("request_too_large")
    deadline = time.monotonic() + _timeout(timeout)
    context = tls_context(password)
    candidates = endpoint_candidates(destination, deadline)
    for index, (family, address) in enumerate(candidates):
        raw = socket.socket(family, socket.SOCK_STREAM)
        secured = None
        sent = False
        try:
            # Reserve time for other addresses if an advertised interface is down.
            budget = _remaining(deadline)
            if index + 1 < len(candidates):
                budget = min(2, budget / (len(candidates) - index))
            raw.settimeout(budget)
            raw.connect(address)
            raw.settimeout(min(budget, _remaining(deadline)))
            secured = context.wrap_socket(raw)
            if secured.version() != "TLSv1.2" or secured.cipher()[0] != CIPHER:
                raise LinkError("unexpected_tls_parameters")
            secured.settimeout(_remaining(deadline))
            sent = True
            secured.sendall(struct.pack(">I", len(data)) + data)
            response = receive_frame(secured, deadline)
            response["transport"] = {"tlsVersion": "0x0303", "cipherSuite": "0xccac"}
            return response
        except (OSError, TimeoutError):
            if sent:
                # Do not repeat requests after partial delivery.
                raise LinkError("authentication_or_connection_failed") from None
        finally:
            if secured is not None:
                secured.close()
            raw.close()
    raise LinkError("authentication_or_connection_failed")


def local_password():
    """Read only from a real Windows console, never getpass's echoing fallback."""
    if sys.platform != "win32" or sys.stdin is not sys.__stdin__ or not sys.stdin.isatty():
        raise LinkError("pair_requires_controlling_local_terminal")
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetConsoleMode.restype = wintypes.BOOL
    mode = wintypes.DWORD()
    if not kernel.GetConsoleMode(msvcrt.get_osfhandle(sys.stdin.fileno()), ctypes.byref(mode)):
        raise LinkError("pair_requires_controlling_local_terminal")
    print("Auday communication password (hidden): ", end="", file=sys.stderr, flush=True)
    characters = []
    while True:
        char = msvcrt.getwch()  # Console read with no echo; no redirected-input path.
        if char in ("\r", "\n"):
            print(file=sys.stderr)
            break
        if char == "\x03":
            raise KeyboardInterrupt
        if char in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if char == "\b":
            if characters:
                characters.pop()
        elif len(characters) < 128:
            characters.append(char)
        else:
            raise LinkError("invalid_password_input")
    password = "".join(characters)
    communication_key(password)
    return password


def stdin_object():
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    data = stream.readline(MAXIMUM + 1)
    if not data or len(data) > MAXIMUM:
        raise LinkError("invalid_stdin")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise LinkError("invalid_stdin") from None
    if not isinstance(value, dict):
        raise LinkError("invalid_stdin")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["discover", "pair", "request", "probe"])
    parser.add_argument("--profile")
    parser.add_argument("--service")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--peer-to-peer", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            tls_context("23456789ABCDEFGH")  # Public synthetic capability check only.
            _zeroconf()
            result = {"ok": True, "python": list(sys.version_info[:3]), "tlsPSK": True,
                      "cipherSuite": "0xccac", "discovery": "zeroconf"}
        elif args.command == "discover":
            if args.peer_to_peer:
                raise LinkError("windows_requires_same_wifi_peer_to_peer_unavailable")
            result = discover()
        elif args.command == "request":
            value = stdin_object()
            password = value.get("password")
            if password is None:
                password = read_secret(value.get("profile", "default"))
            result = request(value, password, value.get("request"), value.get("timeout", 10))
        else:
            if not args.profile:
                raise LinkError("pair_requires_local_terminal_and_profile")
            destination = {"service": args.service, "host": args.host, "port": args.port,
                           "peerToPeer": args.peer_to_peer}
            password = local_password()
            result = request(destination, password, {"op": "status"})
            if result.get("ok") is not True:
                raise LinkError("pair_failed")
            save_secret(password, args.profile)
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    except (LinkError, CredentialError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "password_input_cancelled"}))
        return 1
    except Exception:
        # Do not leak credentials, endpoints, or peer-controlled data via tracebacks.
        print(json.dumps({"ok": False, "error": "authentication_or_connection_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
