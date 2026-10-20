# Physical-device test checklist

Run this after installing the skill and a compatible Auday iPhone build. Keep source validation, simulator results, and physical-device observations separate.

## Before connecting

- Record the skill version and phone app version in private test notes.
- Confirm the phone has **Connect to your workflow**. If it does not, install a compatible build before testing; the skill cannot enable this remotely.
- Use the same reachable LAN first. Disable no system security protections just to make discovery pass.
- Run `doctor` from the installed skill directory, not from a development checkout.
- Keep real test notes, recordings, IP addresses, device IDs, credentials, and screenshots outside the repository.

## First connection

1. Select **All text** on the phone and enable access.
2. Discover the phone. Enter the generated communication password in the local prompt.
3. Confirm `status --live` reports `text-only-read-only`.
4. Synchronize. Compare a few known saved records and their source references with the phone.
5. Confirm the audio catalogue is empty and a direct audio request is rejected.
6. Change the phone to **Text and audio**, reopen access, and synchronize again.
7. Download one selected recording. Verify its hash, duration, availability, and timeline mapping.

## Recovery and lifecycle

- Use an incorrect password and confirm no record metadata is returned.
- Interrupt a download; reconnect and verify recovery without publishing a corrupt asset.
- Close the phone page and confirm cached queries still work while live requests fail or wait explicitly.
- Change the password and confirm the old password cannot read new data.
- Change the access scope and confirm the next sync reflects it. Existing copies should not be silently erased.
- Lock the phone, switch apps, disconnect Wi-Fi, and sleep the Mac. Record the actual outcomes rather than assuming background support.

## Wireless and no-cloud claims

Test LAN first, then separately test Apple peer-to-peer discovery without a shared access point. Simulator loopback is not wireless evidence. For a no-cloud transport claim, disable WAN access or inspect the actual connection path while discovery, pairing, sync, and local queries complete. AI inference is a separate data-flow question.

## Optional audio analysis

Start with a short user-authorized recording and already-approved local models. Save outputs separately. Confirm that the default route includes anonymous speaker turns, and that an explicit request to omit speakers uses `--no-diarize` without requiring speaker models or packages. Missing default-route dependencies must be reported before transcription starts, not silently skipped.

Review names, timing, speaker boundaries, and difficult acoustic regions. Confirm that source audio and phone text remain unchanged. Do not call the result ground truth, claim identity recognition from anonymous clusters, or extrapolate synthetic smoke tests to all languages.

## Local retrieval and readable files

- After a successful sync, close phone access and confirm cached keyword search still works.
- With the pinned local semantic model and runtime configured, build the index. Check its record coverage and `index-status` freshness, then search a known topic using a paraphrase and inspect the returned source record.
- Use the synthetic multilingual fixture for repeatable cross-language checks. Keep personal-record observations separate from model accuracy claims.
- Confirm a changed source snapshot requires an index update, while refreshing only an unchanged snapshot's sync time does not invalidate it.
- Export to a new private directory. Check that the index links to the daily Markdown files, counts match the cached snapshot, and text, timestamps, states, and source IDs survive.
- Confirm that an existing export directory is rejected without overwriting user edits. The export is static; later syncs do not update its files automatically.
- Observe progress during sync, indexing, export, and audio analysis. Counts and percentages must reflect actual work; unknown-duration model stages should show their stage rather than an estimated percentage. Final stdout must remain valid JSON.
- Measure database/index file size separately from process memory. If reporting peak RSS, record which processes were included and whether the run used a real phone or a local replay.

Only publish synthetic or carefully redacted test summaries. A failed physical connection is a test result to investigate, not a reason to extract the app container or bypass its permissions.
