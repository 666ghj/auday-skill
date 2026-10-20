# First-run validation for Skill 0.5.0

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
