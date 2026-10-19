# Local connection protocol

This document describes the protocol consumed by the macOS client. The desktop repository does not contain the iPhone app or its data adapter. A synthetic loopback peer under `tests/native-peer` exercises the contract; it is not installed with the skill and does not certify physical wireless behavior.

## Discovery and authentication

- Bonjour service: `_auday-link._tcp`, domain `local.`.
- TLS PSK identity: `auday-workflow-link-v2`.
- TLS version: 1.2 only; cipher: `TLS_ECDHE_PSK_WITH_CHACHA20_POLY1305_SHA256` (`0xccac`). No plaintext fallback, certificate-validation override, or early-data mode is used.
- The phone generates 16 uniformly selected symbols from `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`. This is 80 bits of entropy. Group separators and case are normalized.
- HKDF-SHA256 derives a 32-byte key using salt `auday-workflow-link-v2` and info `tls-psk`. Derivation does not add entropy or turn this into a password-authenticated key-exchange protocol.

Use a generated secret, not a short human password. Possession of the shared secret grants the currently selected scope. This version has no per-computer authentication or revocation. Discovery metadata is unauthenticated until the handshake completes.

The local client stores secrets in macOS Keychain. It does not store them in the JSON connection file, SQLite cache, or a command argument. Test interfaces accept synthetic secrets through process stdin solely for automated validation.

## Framing and operations

Each TLS connection carries one request and one response. A frame is a four-byte unsigned big-endian length followed by UTF-8 JSON. Maximum frame size is 2 MiB. The synthetic test listener bounds concurrent clients and connection lifetime, and listens only on loopback.

Successful replies include `ok`, `schemaVersion: 1`, `snapshot`, and `sourceDeviceID`. Client diagnostic output also includes the negotiated TLS version and cipher.

| `op` | Request fields | Result |
| --- | --- | --- |
| `status` | none | Counts, categories, and read-only scope |
| `list` | `offset`, `snapshot` | Up to 100 records and `nextOffset` |
| `assets` | `offset`, `snapshot` | Up to 100 asset descriptors and `nextOffset`; no server paths |
| `read` | `id`, optional `snapshot` | One record |
| `audio` | `id`, `offset`, `length`, `snapshot` | Base64 chunk, offset, total size, full hash, and chunk hash |

Audio chunks are at most 256 KiB. Unknown operations, including mutation requests, are rejected. Errors use `ok: false` and a bounded error identifier. The server accepts asset IDs, never arbitrary filesystem paths.

## Scopes and snapshots

`text-only-read-only` exposes transcripts, Highlights, summaries, reflections, and memories. It returns an empty audio catalogue, omits direct audio asset references from the text projection, and rejects the audio operation.

`text-and-audio-read-only` additionally exposes saved audio assets. Neither scope permits writing, deleting, controlling recording, or generating content on the phone. A request cannot increase scope.

The test peer reads an immutable synthetic `library.json` projection. Snapshot identity hashes the projection and scope. A production adapter must produce stable IDs and a consistent saved-data snapshot; do not expose a mutable app database directly. This fixture publisher is test-only; it is not a mobile implementation or a desktop runtime feature.

The client commits complete snapshots transactionally. An unchanged identity avoids body downloads. An interrupted or inconsistent snapshot leaves the previous cache intact. This version does not implement a durable incremental event log. Scope reductions update subsequent syncs but cannot recall exported or offline copies.

## Data projection

Records use stable `id` values and one of `transcript`, `highlight`, `summary`, `reflection`, or `memory`. Preserve source IDs, original timestamps/time zones, saved/generated status, and revision information. Candidate memories remain candidates.

Assets include `id`, `mime`, `size`, `sha256`, duration, and optional timeline spans. The test peer's `path` is local to its export root and never sent to the client. A timeline span maps `fileStart`/`fileEnd` seconds to a timezone-aware `recordedStart`; gaps must not be compressed into invented wall-clock continuity.

Do not include API keys, Keychain items, internal configuration, or unrelated files in the projection. “All text” refers to authorized Auday business data, not the entire application container.

## Current limits

- macOS is the supported desktop runtime. Windows/Linux clients are not implemented.
- Physical Apple peer-to-peer Wi-Fi, background transfer, energy use, and router compatibility need device tests.
- The reference app workflow is foreground-bound; a network request cannot generally wake a suspended iPhone app.
- The cache is user-private but is not an independently encrypted database. Use OS account and storage protections.
- Shared-secret lifecycle, device management, production signing/notarization, and large-archive scaling need further work before broad distribution.
