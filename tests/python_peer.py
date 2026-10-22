"""Test-only loopback TLS-PSK peer. No phone APIs, credentials or user data."""

import json
import socket
import ssl
import struct
import threading
import time

from windows_link import CIPHER, IDENTITY, communication_key, receive_frame


class PythonPeer:
    def __init__(self, password, response=None, raw_response=None, delay=0, family=socket.AF_INET):
        self.password = password
        self.response = response if response is not None else {"ok": True, "access": "text-only-read-only"}
        self.raw_response = raw_response
        self.delay = delay
        self.family = family
        self.requests = []
        self.negotiated = []
        self.identities = []
        self.stop = threading.Event()

    def __enter__(self):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = self.context.maximum_version = ssl.TLSVersion.TLSv1_2
        self.context.set_ciphers(CIPHER)
        key = communication_key(self.password)

        def choose(identity):
            self.identities.append(identity)
            return key if identity == IDENTITY else b""

        self.context.set_psk_server_callback(choose, IDENTITY)
        self.listener = socket.socket(self.family, socket.SOCK_STREAM)
        self.listener.bind(("::1" if self.family == socket.AF_INET6 else "127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        return self

    def _run(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                with connection:
                    connection.settimeout(2)
                    with self.context.wrap_socket(connection, server_side=True) as secured:
                        self.negotiated.append((secured.version(), secured.cipher()[0]))
                        value = receive_frame(secured, time.monotonic() + 2)
                        self.requests.append(value)
                        if self.stop.wait(self.delay):
                            break
                        if self.raw_response is not None:
                            frame = self.raw_response
                        else:
                            response = self.response(value) if callable(self.response) else self.response
                            data = json.dumps(response).encode()
                            frame = struct.pack(">I", len(data)) + data
                        secured.sendall(frame)
            except Exception:
                # Authentication, malformed/truncated frames and cancellation are
                # intentional test inputs. Never print data or keys.
                continue

    def __exit__(self, *args):
        self.stop.set()
        self.listener.close()
        self.worker.join(timeout=3)
