# Connection and local data

## Install and diagnose

Resolve `<skill-root>` to the directory containing `SKILL.md`. Never assume another Auday checkout exists.

```text
python3 <skill-root>/scripts/auday.py doctor
python3 <skill-root>/scripts/auday.py discover
```

The iPhone must have a compatible **Connect to your workflow** page. Discovery reports untrusted service names; the password handshake authenticates the peer. If there are several services, let the user identify the intended phone rather than guessing.

## Pair

```text
python3 <skill-root>/scripts/auday.py pair-window --service <discovered-name>
```

The user enters the password locally. `awaiting_local_password_input` is not a successful pairing. After the local prompt succeeds, run `status --live`, then `sync`.

Known local endpoints can use `--host <address> --port <port>`. Apple peer-to-peer discovery and pairing both require `--peer-to-peer`; the client then connects to the Bonjour service endpoint, not a guessed IP address. Physical wireless behavior remains a separate validation requirement.

The phone generates a 16-character random Base32 secret, displayed in four groups. Case, whitespace, and hyphens are normalized. It contains 80 bits of randomness before key derivation. HKDF does not increase that entropy. Do not replace it with a six-digit PIN, a user-chosen phrase, or a bypass of authentication.

The secret is shared by authorized clients. This version does not provide per-computer credentials or revocation. Changing the password or closing access stops future reads, not already-exported copies.

## Query and fetch

```text
python3 <skill-root>/scripts/auday.py status
python3 <skill-root>/scripts/auday.py status --live
python3 <skill-root>/scripts/auday.py sync
python3 <skill-root>/scripts/auday.py search "launch checklist" --limit 20
python3 <skill-root>/scripts/auday.py search --kind memory --limit 30
python3 <skill-root>/scripts/auday.py read <record-id>
python3 <skill-root>/scripts/auday.py assets --limit 20
python3 <skill-root>/scripts/auday.py audio <asset-id>
```

`--home` and `--profile` are global options placed before the command. Use separate profiles for separate phones. Passwords are never command arguments.

Search combines whitespace-separated terms with AND, supports substring matching, and is not semantic cross-language retrieval. Results carry `syncedAt`; use `nextOffset` with `--offset` to continue. `--date` matches a record's preserved ISO date, not a reconstructed date in the Mac's current time zone. Some derived records lack `start`, so browse by kind or follow their sources.

`sync` checks the snapshot identity first. An unchanged snapshot transfers no record bodies. A changed snapshot is paginated, validated, and committed atomically; this is not a persistent change-log protocol. Scope is part of snapshot identity. Text-only mode returns no audio catalogue and rejects audio requests.

`audio` resumes partial downloads, verifies every chunk and the final SHA-256, and returns a local file path plus asset metadata. The adjacent `.asset.json` preserves provenance and timeline mappings. Audio metadata does not imply that the audio is downloaded.

## Recovery

| Result | Action |
| --- | --- |
| `not_paired` or missing Keychain secret | Start the local pairing prompt |
| `awaiting_phone_or_wrong_password` | Check the active phone page, network, and matching password; do not retry without a bound |
| `snapshot_changed` | The incomplete snapshot was not committed; sync the current snapshot once |
| `source_device_changed` | Use a new profile; do not merge unrelated device archives |
| Interrupted audio | Repeat the same audio request to resume |
| `audio_hash_mismatch` | A corrupt partial is retained separately; a new attempt starts clean |
| `audio_access_denied` | Explain the text-only scope; request a phone-side scope change only if audio is needed |
| Phone offline | Use cached records and downloaded audio, stating the last successful sync time |

The cache is private local data, not project source. Do not commit it or move it into a cloud-synced folder without the user's direction. A scope reduction does not silently delete existing files; offline copies cannot be remotely recalled.
