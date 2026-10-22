# Local connection protocol

This document describes the unchanged iPhone protocol consumed by the macOS and Windows clients. The desktop repository does not contain the iPhone app or its data adapter. Synthetic loopback peers exercise the contract; they are not installed with the skill and do not certify physical wireless behavior. Adding a Windows client does not require changing the phone's scope or wire format.

## Discovery and authentication

- Bonjour service: `_auday-link._tcp`, domain `local.`.
- TLS PSK identity: `auday-workflow-link-v2`.
- TLS version: 1.2 only; cipher: `TLS_ECDHE_PSK_WITH_CHACHA20_POLY1305_SHA256` (`0xccac`). No plaintext fallback, certificate-validation override, or early-data mode is used.
- The phone generates 16 uniformly selected symbols from `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`. This is 80 bits of entropy. Group separators and case are normalized.
- HKDF-SHA256 derives a 32-byte key using salt `auday-workflow-link-v2` and info `tls-psk`. Derivation does not add entropy or turn this into a password-authenticated key-exchange protocol.

Use a generated secret, not a short human password. Possession of the shared secret grants the currently selected scope. This version has no per-computer authentication or revocation. Discovery metadata is unauthenticated until the handshake completes.

The Mac client uses its native Swift/Network bridge and stores secrets in macOS Keychain. The Windows client uses managed Python 3.13 TLS-PSK with OpenSSL, discovers the same Bonjour service with `zeroconf`, and stores secrets in Windows Credential Manager under the current user with local-machine persistence. Both enforce the same identity, derivation, TLS version, cipher, and framing. Windows does not implement Apple's peer-to-peer Wi-Fi extension; the normal shared-LAN path is supported.

Neither client stores secrets in the JSON connection file, SQLite cache, or a command argument. Windows pairing reads the user's password from a real local console without echo and refuses piped password input; `pair-window` launches that user-facing console. Mac uses its local prompt. Test interfaces accept synthetic secrets through process stdin solely for automated validation; they are not a user credential-entry path.

## Framing and operations

Each TLS connection carries one request and one response. A frame is a four-byte unsigned big-endian length followed by UTF-8 JSON. Maximum frame size is 2 MiB. The synthetic test listener bounds concurrent clients and connection lifetime, and listens only on loopback.

The receiver accumulates stream fragments until the declared frame length is complete; a receive callback is not assumed to correspond to a whole frame. The sender marks its response as the final message and drains the connection until the peer closes or the bounded drain timeout expires. `contentProcessed` is not treated as proof of remote delivery. Skill 0.3.1 includes this stream-handling correction; native TLS regression tests include a complete 1.5 MB inline response.

Successful replies include `ok`, `schemaVersion: 1`, `snapshot`, and `sourceDeviceID`. For an on-demand companion, control-plane status uses `null` for the snapshot and counts until preparation succeeds; this never means an empty archive. Client diagnostic output also includes the negotiated TLS version and cipher.

| `op` | Request fields | Result |
| --- | --- | --- |
| `status` | optional `lease` | Connection scope/capabilities and snapshot readiness; a lease selects its bound preparation/generation; never starts preparation |
| `prepareSnapshot` | optional `retry: true`, `useSnapshotLease: true` | Start or join one on-demand preparation; return immediately, with a lease when requested and supported |
| `acquireSnapshot` | `snapshot` | Acquire a lease for an already prepared generation, when supported |
| `releaseSnapshot` | `lease`, optional `snapshot` | Release this token, including a pending preparation without an identity |
| `list` | `offset`, `snapshot`, optional `allowRecordChunks: true` | Up to 100 records or record-transfer descriptors and `nextOffset` |
| `assets` | `offset`, `snapshot` | Up to 100 asset descriptors and `nextOffset`; no server paths |
| `read` | `id`, optional `snapshot`, optional `allowRecordChunks: true` | One inline record or record-transfer descriptor |
| `recordChunk` | `id`, `offset`, `length`, `snapshot` | Base64 bytes of one complete JSON record, with size and hashes |
| `prepareAudio` | `id`, `snapshot` | Prepare one permitted on-demand asset and return its size, hash, format, and timeline |
| `audio` | `id`, `offset`, `length`, `snapshot` | Base64 chunk, offset, total size, full hash, and chunk hash |

Audio chunks are at most 256 KiB. Unknown operations, including mutation requests, are rejected. Errors use `ok: false` and a bounded error identifier. The server accepts asset IDs, never arbitrary filesystem paths.

When `capabilities.snapshotLeases` is true, snapshot data operations (`list`,
`assets`, `read`, `recordChunk`, `prepareAudio`, and `audio`) also accept the
operation's `lease` token. Clients do not send lease fields or operations to
companions that do not advertise this capability.

### On-demand snapshots and large records

Skill 0.3.0 supports `capabilities.onDemandSnapshot` and `capabilities.chunkedRecords`. The phone opens its authenticated listener without reading the archive. Pairing and `status` are control operations only. `sync` explicitly sends `prepareSnapshot`, then polls `status` while `snapshotState` is `preparing`. The other states are `idle`, `ready`, and `failed`. One sync starts at most one preparation attempt and has a bounded 120-second polling window, with individual request timeouts. A failure needs a new explicit sync/retry; polling cannot create a retry loop.

The phone coalesces concurrent preparation requests. Source changes mark the existing snapshot non-current and do not automatically read the archive. A later explicit sync refreshes it. Closing access cancels preparation, revokes the snapshot and closes clients. A preparation failure leaves the connection available so the desktop can inspect status or request a retry.

For a lease-capable phone, `prepareSnapshot` with `useSnapshotLease: true` returns
an independent UUID `lease` while the state is either `preparing` or `ready`.
Poll `status` with that token; the result remains bound to this preparation even
if another client starts a later one. Once ready, keep its snapshot identity and
token for every data request. There is no unprotected ready-to-acquire interval.
One explicit sync captures one generation and then ends; newer arrivals are for
the next explicit sync, not a reason to repeat preparation automatically.

Release the token in a finally path after success, cancellation, or failure.
Pending/failed preparation can be released using only the token. A separate
audio operation first checks for a verified local file; if it needs the phone,
`acquireSnapshot` protects the locally cached generation and the same token is
reused through nested audio preparation and every chunk. A missing or evicted
generation stops the operation rather than silently selecting a new version.
The phone expires idle tokens after five minutes, renews them on requests, and
keeps in-flight operations protected. Its bounded retained generations can
return `snapshot_busy` when all available generations are active. A failed
release is safe to report without discarding already verified local data;
idle expiration eventually releases it.

The 2 MiB frame limit applies to each message, not an entire logical record. A record that cannot fit inline is represented in `list`/`read` as:

```json
{"id":"reflection:example","recordTransfer":{"encoding":"json-utf8","size":3500000,"sha256":"<64 lowercase hex characters>"}}
```

Clients must opt in with `allowRecordChunks: true`. Otherwise a page containing a large record fails with `chunked_records_required`, rather than silently yielding incomplete text. `recordChunk` requires the exact snapshot identity and returns `id`, `offset`, `size`, `sha256`, `chunkSHA256`, and Base64 `data`. Its raw chunk length is at most 256 KiB. Byte ranges refer to the full UTF-8 JSON representation, so splitting a multibyte character between chunks is valid; decoding happens after reassembly.

The client validates every chunk's identity, offset, length and hash, then the complete record hash and ID. Content-addressed partial files allow a subsequent sync to resume. Only fully verified records participate in the atomic cache replacement. Records keep their original identity and full payload, including all saved source citations. No truncation or artificial extra records is used. An unchanged snapshot still avoids all body downloads.

The desktop JSON parser has a separate 128 MiB per-record resource bound. Malformed, inconsistent, or larger descriptors stop synchronization without replacing the last complete cache. This is not a claim of unlimited archive size. Existing eager companions remain compatible with Skill 0.3.0; on-demand companions advertise `minimumSkillVersion: "0.3.0"`.

The separation of metadata and content follows the same engineering pattern as [LocalSend's preparation/transfer phases](https://github.com/localsend/protocol/blob/main/README.md#4-file-transfer-http-aka-upload-api). Snapshot-bound byte ranges follow the consistency principle of [HTTP Range and If-Range](https://www.rfc-editor.org/rfc/rfc9110.html#section-14). The Auday wire format remains its existing authenticated TLS JSON protocol; these references are design precedents, not claims of HTTP or LocalSend wire compatibility.

### On-demand phone audio

Skill 0.2.0 also accepts a server advertising `capabilities.onDemandAudio: true`. Its catalogue may return an asset with `preparation: "on-demand"` and a stable 64-character `contentRevision` instead of an eagerly prepared file hash and size. This revision describes the source version; it is not a download-integrity hash.

Before downloading such an asset, the client sends `prepareAudio` with `id` and `snapshot`. The reply contains a ready `asset` with the same ID, `size`, `sha256`, `mime`, `duration`, and `timeline`. The phone resolves only that catalogue entry through its existing retained-audio reader. Preparation is bounded by a 90-second client timeout; it neither uploads audio nor prepares the entire recording archive. Range transfer and final hash verification then use the existing `audio` operation. Older eager catalogues remain supported.

The phone maintains a bounded preparation cache. If an entry was evicted during transfer, the client may prepare it once more and continue only if its hash and size still match. Changed sources or snapshots stop the download for an explicit resync.

When present, `snapshotCapturedAt` and `snapshotCurrent` describe the phone's source snapshot independently of the computer's `syncedAt`. The client performs one final plain `status` check, without a lease or another preparation. It preserves the original capture time and reports `sourceSnapshotCurrent: true` only if the device, snapshot, and scope match and the final flag is the boolean `true`. A newer generation, unavailable status, or missing/invalid flag yields false. This reflects the completion check, not a promise that the phone remains unchanged afterward.

## Scopes and snapshots

`text-only-read-only` exposes transcripts, Highlights, summaries, reflections, and memories. It returns an empty audio catalogue, omits direct audio asset references from the text projection, and rejects both `prepareAudio` and `audio`.

`text-and-audio-read-only` additionally exposes saved audio assets. Neither scope permits writing, deleting, controlling recording, or generating content on the phone. A request cannot increase scope.

The test peer reads an immutable synthetic `library.json` projection. Snapshot identity hashes the projection and scope. A production adapter must produce stable IDs and a consistent saved-data snapshot; do not expose a mutable app database directly. This fixture publisher is test-only; it is not a mobile implementation or a desktop runtime feature.

The client commits complete snapshots transactionally. An unchanged identity avoids body downloads. An interrupted or inconsistent snapshot leaves the previous cache intact. This version does not implement a durable incremental event log. Scope reductions update subsequent syncs but cannot recall exported or offline copies.

Writers sharing a canonical profile directory hold a reentrant thread lock and a
cross-process `operation.lock` file for the entire sync or audio operation. This
prevents an earlier sync from overwriting a later one and prevents a prepared
asset from being written into another snapshot's catalogue. Different profiles
are independent; cached reads do not wait for network operations. The lock file
is retained rather than removed on release. Forked workers must create new
clients and SQLite connections instead of reusing inherited instances.

## Data projection

Records use stable `id` values and one of `transcript`, `highlight`, `summary`, `reflection`, or `memory`. Preserve source IDs, original timestamps/time zones, saved/generated status, and revision information. Candidate memories remain candidates.

An on-demand catalogue entry contains its `id`, `preparation: "on-demand"`, and `contentRevision`, with available source timing metadata. Prepared or eager assets include `mime`, `size`, `sha256`, duration, and timeline spans when available. Catalogue metadata does not mean the file has been prepared or downloaded. The test peer's `path` is local to its export root and never sent to the client. A timeline span maps `fileStart`/`fileEnd` seconds to a timezone-aware `recordedStart`; gaps must not be compressed into invented wall-clock continuity.

Do not include API keys, OS credential-store items, internal configuration, or unrelated files in the projection. “All text” refers to authorized Auday business data, not the entire application container.

## Current limits

- The desktop setup baseline is macOS 13+ (13.3+ for the pinned semantic runtime) and Windows 10/11 x64. Windows ARM64 and Linux are not part of this baseline.
- Physical Apple peer-to-peer Wi-Fi, background transfer, energy use, and router compatibility need device tests.
- The reference app workflow is foreground-bound; a network request cannot generally wake a suspended iPhone app.
- The cache is user-private but is not an independently encrypted database. Use OS account and storage protections.
- Shared-secret lifecycle, device management, production signing/notarization, and large-archive scaling need further work before broad distribution.
