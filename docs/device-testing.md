# First-run and physical-device test checklist

Validate the installed package, then test a compatible Auday iPhone build separately. Keep clean runtime setup, synthetic inference, simulator results, and physical-device observations distinct.

## Clean runtime setup

Use a newly installed copy of the skill and a new empty private data root. Do not seed it with models, package caches, a database, pairing, or configuration from a developer's working installation. Record OS/architecture and any system tools already present. An isolated data root on a developer computer is not a factory-reset OS. Use the entrypoint for the platform being tested; do not infer Windows behavior from a Mac run.

On macOS, limit command lookup to system tools so Homebrew and other developer-installed executables cannot satisfy requirements accidentally:

```text
env PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  /bin/sh "<installed-skill>/scripts/bootstrap.sh" --home "<absolute-empty-data-root>" check
env PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  /bin/sh "<installed-skill>/scripts/bootstrap.sh" --home "<absolute-empty-data-root>" doctor
env PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  /bin/sh "<installed-skill>/scripts/bootstrap.sh" --home "<absolute-empty-data-root>" setup
```

On Windows 10/11 x64, use Windows PowerShell 5.1 from the installed package:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<installed-skill>\scripts\bootstrap.ps1" --home "<absolute-empty-data-root>" check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<installed-skill>\scripts\bootstrap.ps1" --home "<absolute-empty-data-root>" doctor
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<installed-skill>\scripts\bootstrap.ps1" --home "<absolute-empty-data-root>" setup
```

The execution-policy flag applies only to each child process. Verify the persistent policy was not changed. Windows must use private Python 3.13.16 and the connection environment under `runtime/windows-link/venv/Scripts/python.exe`. macOS keeps private Python 3.12.15 and its native Swift bridge. In hosted acceptance, put failing sentinels ahead of global Python, uv, FFmpeg, and Whisper commands and verify no sentinel was executed. A test harness may use the runner's Python for packaging/assertions; the installed skill must not borrow it.

For each platform, replace `<auday>` below with its full bootstrap prefix and `--home` argument. These opt-in commands download real assets:

```text
<auday> setup --defer-semantic
<auday> setup
<auday> setup --semantic
<auday> setup --audio --model small --no-diarize
<auday> setup --audio --model small
```

Verify the following without reading personal Auday data:

- `check` succeeds without Python, does not download, and reports capability prerequisites.
- The first real setup obtains private `uv` and managed Python under the isolated data root. It does not depend on a global Python, Homebrew, WSL, MSVC installation, or pre-existing model paths.
- A new installation shows the semantic recommendation and the expected unpaired/empty-library state.
- Deferring semantic setup records the choice; another setup invocation respects it without downloading weights or repeating the recommendation. A later explicit semantic request still works.
- Selected semantic setup downloads the pinned runtime/model, verifies hashes, saves usable local paths, and passes synthetic inference. Run again to check reuse of verified assets.
- Selected audio setup downloads only one requested transcription model. The default route also prepares and tests anonymous diarization. In a separate empty root, `--no-diarize` succeeds without speaker weights or speaker-analysis packages.
- A failed or interrupted download cannot become a ready configuration. Test corrupt bytes and incomplete artifacts with synthetic fixtures; a successful later attempt must verify its result.
- The configured `transcribe` command uses installed assets, creates a new output directory, and does not trigger a surprise download.
- Progress exposes download bytes and observable preparation stages while final stdout remains valid JSON. Unknown-duration inference stages must not invent a percentage.

Use synthetic text and the public audio sample for these checks. Passing setup or a synthetic pipeline does not establish language accuracy, speaker quality on meetings, physical-phone connectivity, or performance on another computer.

On Windows, verify CPU semantic inference binds only `127.0.0.1`, rejects requests without the ephemeral token, and cleans up its process and temporary key file. Verify prebuilt Whisper.cpp/FFmpeg and default sherpa-onnx run without a compiler or GPU. Credential Manager integration, the user-facing console, and Windows Firewall behavior need Windows tests; a mocked store or Mac subprocess test is separate evidence.

For the missing Xcode Command Line Tools branch, verify a real fresh macOS account/VM or use an explicit prerequisite test fixture and label which was used. The agent should explain and launch the system installation path, then recheck; a mock cannot prove Apple's dialogs or installation completed. Check supported Intel and Apple Silicon routes separately before claiming both have been run. macOS 13 core behavior and the semantic runtime's macOS 13.3 minimum are separate boundaries.

## Before connecting

- Record the skill version and phone app version in private test notes.
- Confirm the phone has **Connect to your workflow**. If it does not, install a compatible build before testing; the skill cannot enable this remotely.
- Use the same reachable LAN first. Disable no system security protections just to make discovery pass.
- Run `doctor` through the platform's installed bootstrap entrypoint, not from a development checkout.
- Keep real test notes, recordings, IP addresses, device IDs, credentials, and screenshots outside the repository.

## First connection

1. Select **All text** on the phone and enable access.
2. Discover the phone. Enter the generated communication password in the local prompt.
3. Confirm `status --live` reports `text-only-read-only`.
4. Synchronize. Compare a few known saved records and their source references with the phone.
5. Confirm the audio catalogue is empty and a direct audio request is rejected.
6. Change the phone to **Text and audio**, reopen access, and synchronize again.
7. Download one selected recording. Verify its hash, duration, availability, and timeline mapping.

On Windows, confirm that `pair-window` opens a separate local console, password entry is not echoed, and the agent receives only `awaiting_local_password_input` while waiting. That state does not prove successful pairing. Check successful authentication afterward with `status --live`, and confirm the saved secret belongs to the current user's Credential Manager rather than the JSON profile. On Mac, verify the corresponding local prompt and Keychain behavior. Keep actual secrets out of test notes and screenshots.

## Recovery and lifecycle

- Use an incorrect password and confirm no record metadata is returned.
- Interrupt a download; reconnect and verify recovery without publishing a corrupt asset.
- Close the phone page and confirm cached queries still work while live requests fail or wait explicitly.
- Change the password and confirm the old password cannot read new data.
- Change the access scope and confirm the next sync reflects it. Existing copies should not be silently erased.
- Lock the phone, switch apps, disconnect Wi-Fi, and sleep the computer. Record the actual outcomes rather than assuming background support.
- During a sync, add a recording and finish a transcription. Confirm that the transfer finishes with its original snapshot, reports its capture time, and includes the new saved content only after another explicit sync. A stale final flag must not trigger an automatic repeat.
- Start two transfers for the same local profile and verify that the second waits; start a different profile and verify it remains independent. Check that an older audio preparation cannot overwrite a newer catalogue.
- With a lease-capable phone, start an update from another computer while the first transfer is active. Confirm that each transfer keeps its selected generation and that completion/failure releases its lease. Keep this device observation separate from synthetic concurrency tests.

## Wireless and no-cloud claims

Test the shared LAN on each desktop OS. On Mac only, separately test Apple peer-to-peer discovery without a shared access point; Windows must reject that option. Simulator or CI loopback is not wireless evidence. On Windows, test permitted Bonjour discovery and an explicit host/port fallback when multicast is unavailable; neither may bypass authentication or require disabling the firewall. For a no-cloud transport claim, disable WAN access or inspect the actual connection path while discovery, pairing, sync, and local queries complete. AI inference is a separate data-flow question.

## Optional audio analysis

Start with a short user-authorized recording. If local tools are missing, use the selected on-demand setup flow and verify it before processing the recording. Save outputs separately. Confirm that the default route includes anonymous speaker turns, and that an explicit request to omit speakers uses `--no-diarize` during both setup and transcription without requiring speaker models or packages. Missing default-route dependencies must be resolved before transcription starts, not silently skipped.

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
