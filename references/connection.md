# Connection and local data

## Connection prerequisites

Give the user three short prerequisites: **connect both devices to the same Wi-Fi, allow Local Network access when asked, and keep the phone connection page open during transfer**. Use everyday language; do not lead with service names, network topology, or unusual connection arrangements. Explain extra troubleshooting only when a connection actually fails.

- Use the same Wi-Fi for the normal setup. If discovery fails despite that, check whether the network blocks communication between devices, as can happen on guest or public Wi-Fi.
- On iPhone, allow Auday's **Local Network** request. If it was denied, check **Settings → Privacy & Security → Local Network**. On macOS, allow the responsible desktop app or helper if prompted; the system may attribute a child tool's network access to its parent app. Do not claim permissions are granted merely because the listener started.
- On Windows, keep the required local-network access for the managed connection program available. If Windows Firewall or an organization policy blocks discovery, identify that specific block and use an allowed trusted-network path; do not disable the firewall. A manual host/port connection can help when multicast discovery alone is unavailable, but does not bypass network policy or authentication.
- Keep the phone's connection page in the foreground with access enabled during live reads. The desktop agent starts the bundled connection program as needed; there is no separate service for the user to start. Bluetooth is not required for this route.
- Proximity alone does not establish the default connection. Apple peer-to-peer Wi-Fi is an experimental macOS-only path, separately enabled on both ends, not the normal phone-page behavior. Windows rejects `--peer-to-peer`. Do not promise connection without shared LAN connectivity based on simulator results.

Apple documents that the simulator does not implement local network privacy; verify the real permission prompt on a physical device. See [local network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy) and [the iPhone permission setting](https://support.apple.com/en-us/102229).

## Install and diagnose

For a new installation, complete [first-run setup](first-run.md) first. `<auday>` below is the platform-specific bootstrap prefix defined there: `bootstrap.sh` through `/bin/sh` on Mac, or `bootstrap.ps1` through Windows PowerShell. Never assume another Auday checkout or a global Python installation exists. The agent resolves and runs the commands:

```text
<auday> check
<auday> doctor
<auday> discover
```

`check` downloads nothing and runs without Python. Other commands can prepare the private base environment. On Mac, phone connection needs Xcode Command Line Tools with Swift 5.9 or later to compile the bridge. If missing, start `xcode-select --install`, explain required system actions, and check again after they finish. On Windows, setup prepares Python 3.13.16 with TLS-PSK and `zeroconf`; no Swift installation or compilation is needed. Do not claim pairing is ready while the host's connection prerequisites are unavailable.

The iPhone must have a compatible **Connect to your workflow** page. A missing pairing on a fresh installation is expected. Discovery reports untrusted service names; the password handshake authenticates the peer. If there are several services, let the user identify the intended phone rather than guessing.

## Pair

```text
<auday> pair-window --service <discovered-name>
```

The user enters the password locally. On Windows, `pair-window` opens a real local console whose password entry is not echoed; piped password input is refused. Saved secrets use Windows Credential Manager for the current user. Mac uses its local pairing window and Keychain. Never redirect password entry through agent tool input, chat, command arguments, or a file. `awaiting_local_password_input` is not a successful pairing. After the local prompt succeeds, run `status --live`, then `sync`.

Known local endpoints can use `--host <address> --port <port>`. On Mac, Apple peer-to-peer discovery and pairing both require `--peer-to-peer`; the client then connects to the Bonjour service endpoint, not a guessed IP address. Physical wireless behavior remains a separate validation requirement.

The phone generates a 16-character random Base32 secret, displayed in four groups. Case, whitespace, and hyphens are normalized. It contains 80 bits of randomness before key derivation. HKDF does not increase that entropy. Do not replace it with a six-digit PIN, a user-chosen phrase, or a bypass of authentication.

The secret is shared by authorized clients. This version does not provide per-computer credentials or revocation. Changing the password or closing access stops future reads, not already-exported copies.

## Query and fetch

```text
<auday> status
<auday> status --live
<auday> sync
<auday> search "launch checklist" --limit 20
<auday> search --kind memory --limit 30
<auday> read <record-id>
<auday> assets --limit 20
<auday> audio <asset-id>
```

`--home` and `--profile` are global options placed before the command, with `--home` first when both are provided. Use separate profiles for separate phones. Passwords are never command arguments. Existing integrations with an appropriate Python runtime may still invoke `auday.py` directly.

The default keyword search combines whitespace-separated terms with AND and supports substring matching. It is distinct from the [local semantic mode](semantic-search.md), selected with `search --mode semantic` after preparing the local model and index. Keyword results carry `syncedAt`; use `nextOffset` with `--offset` to continue. `--date` matches a record's preserved ISO date, not a reconstructed date in the computer's current time zone. Some derived records lack `start`, so browse by kind or follow their sources.

`sync` checks the snapshot identity first. An unchanged snapshot transfers no record bodies. A changed snapshot is paginated, validated, and committed atomically; this is not a persistent change-log protocol. Scope is part of snapshot identity. Text-only mode returns no audio catalogue and rejects audio requests.

With Skill 0.3.0 and an on-demand companion, enabling access and pairing do not prepare the archive. `status --live` may show `snapshotState: "idle"` and null counts. Run `sync` to request preparation; it waits for that one attempt before reading the snapshot. Large text records are downloaded in verified chunks and reassembled completely. An interrupted transfer can resume on the next sync, while the last complete cache remains usable.

Every invocation has a finite endpoint: capture one saved-data snapshot, transfer and verify it, then finish. New recordings or transcripts arriving while it runs are excluded from that snapshot. The client checks source freshness once at the end without starting another preparation. `sourceSnapshotAt` remains the capture time; `syncedAt` describes the local synchronization. `sourceSnapshotCurrent` is true only when that final check confirms the same device, snapshot, and scope are still current. A failed check, an unknown flag, or a newer snapshot leaves it false. This is not live monitoring after completion.

When the user says that new content is available or asks to refresh/sync again, run:

```text
<auday> sync
<auday> index-status
```

If an existing semantic index is configured, run `<auday> index` once after the sync. It reuses unchanged vectors and updates changed records. If no index exists, finish the text update without downloading a model or enabling a new capability. Missing dependencies or an index-update failure should be reported as a separate remaining limitation, not retried indefinitely. State the completed snapshot's capture time and whether more phone content may be available for the next explicit update. Do not restart merely because `sourceSnapshotCurrent` is false, and do not run a background update loop.

Operations that change the same local profile—sync, audio preparation, and audio download—wait for one another. Other profiles and cached reads remain available. A compatible phone also keeps the selected snapshot available under a temporary lease throughout a transfer, so another computer's update does not switch the content halfway through. Leases are released on success or failure; they do not make phone access permanent.

`audio` resumes partial downloads, verifies every chunk and the final SHA-256, and returns a local file path plus asset metadata. The adjacent `.asset.json` preserves provenance and timeline mappings. Audio metadata does not imply that the audio is downloaded.

With a compatible phone, the audio catalogue is lightweight: the `audio` command prepares only the requested recording before downloading it. It keeps one snapshot identity for preparation and every chunk. Verified downloaded files can be reused offline without contacting the phone. Allow bounded preparation to finish rather than fetching every asset to test a connection. Do not treat a recent `syncedAt` as proof that the source is current.

## Recovery

| Result | Action |
| --- | --- |
| `not_paired` or missing OS credential | Start the local pairing prompt |
| `awaiting_phone_or_wrong_password` | Check the active phone page, reachable LAN, local-network permission, and matching password; do not retry without a bound |
| `snapshot_changed` | The incomplete snapshot was not committed; sync the current snapshot once |
| `snapshot_busy` | Other transfers hold the phone's retained snapshots; let them finish, then retry the requested update once |
| `snapshot_preparation_failed` | Inspect the phone's preparation message; retry sync once after resolving it |
| `snapshot_preparation_timed_out` | Keep the page open and check status; do not start an unbounded retry loop |
| `record_chunk_hash_mismatch` or `record_hash_mismatch` | The cache was not replaced; retry the sync once |
| `source_device_changed` | Use a new profile; do not merge unrelated device archives |
| Interrupted audio | Repeat the same audio request to resume |
| `audio_hash_mismatch` | A corrupt partial is retained separately; a new attempt starts clean |
| `audio_access_denied` | Explain the text-only scope; request a phone-side scope change only if audio is needed |
| Phone offline | Use cached records and downloaded audio, stating the last successful sync time |

The cache is private local data, not project source. Do not commit it or move it into a cloud-synced folder without the user's direction. A scope reduction does not silently delete existing files; offline copies cannot be remotely recalled.

The cache is a persistent SQLite file, with `default` as the normal profile:

| Host | Default database |
| --- | --- |
| Mac | `~/Library/Application Support/AudayWorkflow/<profile>/library.sqlite` |
| Windows | `%LOCALAPPDATA%\AudayWorkflow\<profile>\library.sqlite` |

It survives closing the agent or restarting the computer; it is not an in-memory-only cache. Downloaded audio lives in that profile's `audio` directory. Resolve and show the actual local path when the user wants to browse it. Use the [readable export workflow](context.md#readable-files) for ordinary files they can open and search.
