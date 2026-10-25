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

## Skill 0.7.0: native Windows ARM64 preparation

Local validation completed on 2026-10-25. This implementation targets Windows 11
ARM64 while preserving Windows 10/11 x64 and macOS. **Native Windows execution
acceptance is pending; the checks below are not Windows ARM64 runtime proof.**

- On macOS with Python 3.13.16 and portable PowerShell 7.6.8, the integrated suite
  passed 255 tests with 6 platform/optional skips. It exercised architecture
  selection, PowerShell quoting and extraction, synthetic encrypted transport,
  source preservation, installed-package contracts, and local retrieval logic.
  After the follow-up speaker-validation migration fix, all 34 audio setup tests
  passed, including preservation and revalidation across architectures.
- The exact uv 0.13.3/Python 3.13.16 combination installed the locked ARM64
  discovery dependency recipe using zeroconf's pure-Python build. No Cython or
  native compiler was used. Both private-uv and explicit-pip test routes passed
  on macOS; native Windows installation remains a separate check.
- Downloaded Windows ARM64 uv, llama.cpp, Microsoft CRT, Whisper, FFmpeg, and
  LLVM OpenMP assets were checked against their pinned hashes, byte counts,
  archive layouts, and PE architecture. Whisper's required OpenMP symbols are
  present in the separately sourced LLVM runtime. These are static checks, not
  execution of Windows binaries.
- The frozen audio dependency set resolved Windows ARM64 wheels without
  imageio-ffmpeg or x64 wheels. Existing x64 dependencies remain pinned.
- Skill metadata, relative references, public-source checks, and whitespace
  validation passed. Independent source review found no remaining blocker.

The Windows regression and clean-install workflows now have separate native
`windows-2025` x64 and `windows-11-arm` jobs. Clean acceptance checks the OS,
interpreter, executables, and DLL machine types as well as semantic retrieval,
public-sample transcription, default diarization, and speaker opt-out. Their
results must be recorded before describing Windows ARM64 execution as verified.

Physical Windows-to-iPhone LAN pairing, firewall prompts, phone background
behavior, and personal-audio accuracy remain outside synthetic acceptance.
The pinned ARM64 semantic engine targets ARMv8.7-A; a successful hosted run
cannot establish compatibility with every older ARM64 processor.
