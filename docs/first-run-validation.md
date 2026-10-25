# First-run validation records

## Skill 0.5.0: macOS installation and inference

Validated on 2026-10-20 from installed skill packages, without personal recordings,
phone pairing, inherited model files, or a developer's existing Python environment.

## What “clean” means here

Each hosted job starts on a fresh standard GitHub macOS runner. The harness creates
a new skill installation, data directory, model directory, dependency environment,
and download cache. It places failing placeholder commands ahead of system tools
for `python`, `python3`, `uv`, `cmake`, `ffmpeg`, and `whisper-cli`. Any attempt by
the skill to use those global commands fails the run.

The harness uses runner Python to assemble the installation and assert results.
The installed skill starts through `/bin/sh scripts/bootstrap.sh`; it downloads
its own pinned uv and Python, then uses only explicitly configured private tools.
macOS system utilities and Apple developer tools remain available. This is not a
test of reinstalling macOS or automatically accepting Apple's installer dialogs.

The test operates on eight synthetic text records and the public JFK sample
included in the pinned Whisper.cpp source. It never reads a user's Auday archive.

## Successful hosted runs

| Platform | Transcription preset | Evidence |
| --- | --- | --- |
| Apple Silicon, macOS 15 | Small Q5_1 | [Fresh installation and inference](https://github.com/666ghj/auday-skill/actions/runs/39433823740/job/118665491634) |
| Intel, macOS 15 | Small Q5_1 | [Fresh installation and inference](https://github.com/666ghj/auday-skill/actions/runs/39435103680/job/118669955337) |
| Apple Silicon, macOS 15 | Large-v3-turbo Q5_0 | [Fresh installation and inference](https://github.com/666ghj/auday-skill/actions/runs/39435103680/job/118669955893) |

The Apple Silicon small-model run used commit `ced253f`; the other runs used `e4eb452`, which
changes the acceptance harness and matrix without changing installed runtime
files. The Intel check caught an SDK type difference in the TLS cipher constant;
the corrected bridge compiled successfully before the audio checks continued.

Each complete run verifies:

1. The shell-only check downloads nothing and creates no application-data directory.
2. Base preparation installs private Python and uv without optional models.
3. Deferring semantic setup persists across invocations.
4. Explicit semantic setup downloads the fixed runtime and model, verifies their
   hashes, configures the profile, and passes three synthetic retrieval checks.
5. The synthetic library is indexed; Chinese, English, and mixed-language queries
   return the expected records. An unchanged index is reused.
6. Readable exports contain the expected record count, and the native connection
   bridge builds from the installed package.
7. Audio setup with `--no-diarize` installs the selected ASR model without speaker
   weights, sherpa-onnx, or SoundFile. Public-sample transcription contains the
   expected words.
8. A later default audio setup adds and verifies anonymous diarization.
9. A subsequent explicit speaker opt-out runs successfully.
10. All configured model and runtime paths remain inside the new data directory;
    no poisoned global tool was executed.

The repeatable entrypoint is `tools/check_clean_setup.py`. It requires an explicitly
chosen new output directory and is opt-in because it downloads real assets.
The ordinary unit suite does not download models. The hosted workflow tests
`turbo` on Apple Silicon and `small` on Intel, with both speaker preferences.

## Scope of the result

These checks establish reproducible setup and executable local inference for the
listed combinations. They do not establish transcription accuracy on arbitrary
recordings, general multilingual retrieval quality, phone permissions, wireless
pairing, or background phone transfer.

Missing or outdated Apple developer tools are detected before claiming connection
readiness. The agent can start the Apple installer and continue independent local
setup, while leaving required passwords and system acceptance to the user.
The tested startup and recovery contracts include retaining a requested semantic
installation after interruption, preserving an earlier working configuration on
failed verification, and rejecting unverified downloaded bytes.

## Skill 0.6.0: Windows installation and inference

Validated on 2026-10-22 at commit `06469248c3f0e131517cc8a247e189cb834965e3`.
The [Windows clean acceptance job](https://github.com/666ghj/auday-skill/actions/runs/39795456300/job/119881572636)
completed on a standard Windows x64 hosted runner (OS version `10.0.26100`).
Its [synthetic report and setup logs](https://github.com/666ghj/auday-skill/actions/runs/39795456300/artifacts/12432218824)
are retained temporarily by GitHub Actions.

The harness installed the distributable package into a new directory, isolated
the user home, caches, and temporary directories, and started it with Windows
PowerShell 5.1. Installation, data, and temporary paths contained Chinese and
Japanese characters. Failing `.cmd` and `.exe` sentinels verified that the skill
did not borrow global developer tools. The harness itself used runner Python for
packaging and assertions; this is an isolated installation on a hosted image,
not a factory-reset Windows computer.

The completed run verified:

1. The check-only entrypoint made no downloads; base setup installed private uv,
   Python 3.13.16, and the hashed connection dependencies without optional models.
2. Deferring semantic setup persisted. Explicit setup downloaded and verified
   EmbeddingGemma and the Windows CPU runtime, then passed three setup checks.
3. Eight Chinese, English, and mixed-language retrieval queries passed, an
   unchanged index was reused, and readable export preserved the source database.
4. The pinned Whisper `small` model transcribed the public JFK sample with an
   explicit speaker opt-out and with default anonymous diarization. The opt-out
   installation contained no speaker models or speaker-analysis packages.
5. Model hashes passed. A recursive native import audit confirmed that the
   engines supplied their required Microsoft VC/OpenMP runtime DLLs locally.
   No global-tool sentinel was executed.

The same commit passed the [Windows regression suite](https://github.com/666ghj/auday-skill/actions/runs/39795456332/job/119881571912)
(225 tests, 7 platform/optional skips), including native Credential Manager,
directory permissions, process cleanup, Bonjour discovery, and the full synthetic
encrypted workflow. The [macOS regression job](https://github.com/666ghj/auday-skill/actions/runs/39795456332/job/119881571966)
passed both native test packages and 225 Python tests (19 platform/optional skips).
A separate Python 3.13 pass ran 25 transport tests (1 Windows-only skip), checking
the Windows client's TLS-PSK transport against the Apple Network framework peer.

This evidence covers executable Windows x64 installation and synthetic local
inference. It does not establish physical Windows-to-iPhone LAN pairing, firewall
prompts, phone background behavior, or accuracy on personal recordings. The
target desktop baseline is Windows 10/11 x64; this hosted run does not claim that
every Windows release, ARM64 machine, or CPU has been tested. The optional Windows
Whisper runtime requires AVX2, FMA, and F16C. Windows `turbo` was not exercised in
this acceptance run; the recorded Windows inference preset is `small`.

## Skill 0.7.0: native Windows ARM64 installation and inference

Validated on 2026-10-25 at commit `e81c44adcf2672ac27f20c7629845ecb269e4fd4`.
The [native Windows clean acceptance run](https://github.com/666ghj/auday-skill/actions/runs/40191571138)
passed on both `windows-11-arm` (OS `10.0.26200`) and `windows-2025` x64
(OS `10.0.26100`). The ARM64 host, runner Python, managed Python, uv, model
executables, and app-local DLLs were checked for native architecture; x64
emulation was not accepted as ARM64 proof.

Each architecture completed an installation from an installed skill package,
with empty private caches, Unicode installation/data paths, and failing global
tool sentinels. The ARM64 run took 203.6 seconds and the x64 run 104.2 seconds
on these hosted machines; these are observations, not user-device performance
estimates. The completed checks verified:

1. The system-only check downloaded nothing. Base setup installed native private
   uv and Python 3.13.16 without optional models. ARM64 installed zeroconf through
   its locked pure-Python build, with no native compiler or global Python.
2. Semantic setup passed three checks; eight English, Chinese, and mixed-language
   queries passed. An unchanged index was reused and readable export left the
   source database unchanged.
3. Whisper `small` transcribed public sample audio with speaker opt-out and with
   default anonymous diarization. The opt-out environment contained no speaker
   dependencies or weights. A 48 kHz stereo AAC/M4A derivative also passed the
   transcription path, and both WAV and AAC source files remained unchanged.
4. Model hashes, native executable architecture, and recursive DLL import checks
   passed. No forbidden global tool was invoked. FFmpeg and Whisper startup
   checks run before model downloads.

The [cross-platform regression run](https://github.com/666ghj/auday-skill/actions/runs/40191571170)
passed 261 tests on each Windows architecture (7 platform/optional skips),
including native Credential Manager, local discovery, and synthetic encrypted
transport. macOS passed 261 Python tests (19 skips), both native test packages,
and a Python 3.13 transport pass with 26 tests (1 Windows-only skip).
Public-source/history checks and independent source review also passed.

This evidence covers native Windows 11 ARM64 and Windows x64 hosted execution,
not a factory-reset PC or physical Windows-to-iPhone LAN acceptance. Firewall
prompts, phone background behavior, and personal-audio accuracy remain separate
checks. Windows `turbo` was not exercised; the recorded inference preset is
`small`. The pinned ARM64 semantic engine targets ARMv8.7-A, so a successful
hosted run does not establish support for every older ARM64 processor.
