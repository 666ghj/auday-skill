# First-run setup on a new Mac

Use this flow after the host installs the skill files or when a requested capability is missing. The user should be able to ask for the result in ordinary language; the agent performs the commands, downloads, configuration, and checks.

Do not infer readiness from the developer's computer or another skill installation. A new Mac may have no Python, `uv`, Homebrew, Swift tools, models, pairing, or Auday data. The file installer only copies the skill package. Complete this capability check before describing the installation as ready to use.

## Check without changing the computer

Resolve `<skill-root>` to the installed folder containing `SKILL.md`; quote absolute paths in real commands.

```text
/bin/sh <skill-root>/scripts/bootstrap.sh check
```

This check works without Python and downloads nothing. Inspect macOS version, architecture, available disk/memory, command-line tools, and existing managed setup. Explain only the missing prerequisites that affect the user's next action.

The core client requires macOS 13 or later. The pinned semantic runtime requires macOS 13.3 or later. Check each capability independently: an unavailable optional runtime must not block available keyword search or readable exports. Apple Silicon and Intel paths differ; do not use an Apple Silicon-only runtime on Intel.

## Prepare the base environment

```text
/bin/sh <skill-root>/scripts/bootstrap.sh doctor
/bin/sh <skill-root>/scripts/bootstrap.sh setup
```

The bootstrap can prepare a private `uv` executable and managed Python without relying on a globally installed Python or Homebrew. Explain the approximately 40 MB base download when needed; extracted files use more disk space. `doctor` checks the environment, and `setup` reads the saved onboarding state and capability suggestions.

Phone discovery and encrypted connection use a bundled Swift bridge that must be compiled. The integrated audio setup also needs Apple's compiler to build Whisper.cpp. If Xcode Command Line Tools are missing, the agent can start the macOS installer:

```text
xcode-select --install
```

Explain the system dialog and wait only for steps the user must perform, such as entering a system password or accepting a system license. Recheck afterward. Do not describe this as a fully unattended fresh-Mac install, and do not treat a toolchain issue as a reason to download audio models. Local capabilities that do not need the bridge can be prepared separately.

## Recommend semantic search once

At the first onboarding, recommend installing local semantic search so the user can search ideas and paraphrases as well as exact words. Give one concise choice in the user's language:

> I recommend preparing local semantic search. It downloads a 333.59 MB model and about 11.2 MB of runtime archives, plus the base environment if needed; extracted files and future indexes take additional space. The embedding model runs on your Mac. Shall I prepare it now, or leave it for later?

Mention the model's [Gemma terms](https://ai.google.dev/gemma/terms) when presenting this setup. This does not make the entire desktop assistant local: relevant excerpts returned to a cloud assistant can still reach its model.

If the user already asked to configure semantic search, proceed under that request without asking the same question again. If they choose now:

```text
/bin/sh <skill-root>/scripts/bootstrap.sh setup --semantic
```

The setup downloads the pinned compatible runtime and complete model, verifies SHA-256 hashes, saves the local configuration, and runs a small synthetic verification. Only successful verification means the semantic capability is ready. It can be prepared before pairing; the user's actual archive is indexed after the first successful sync.

If the user chooses later or declines:

```text
/bin/sh <skill-root>/scripts/bootstrap.sh setup --defer-semantic
```

Keep keyword search and readable export available. Honor the saved choice instead of repeating the same offer on every invocation. Revisit it when the user requests semantic search or asks to change the setup. Do not silently download the model when a keyword query would satisfy the task.

## Connect and obtain useful context

Follow [Connection](connection.md): same Wi-Fi, local-network permission, phone connection page open, and a local password prompt. Keep “skill files installed,” “tools verified,” “phone paired,” and “first sync complete” as separate states.

After sync, build the semantic index if that capability was selected:

```text
/bin/sh <skill-root>/scripts/bootstrap.sh index
/bin/sh <skill-root>/scripts/bootstrap.sh index-status
```

Show actual record counts while indexing. A successful synthetic check before pairing does not imply that the user's archive has been indexed. If the phone is not available, finish the selected local setup and explain the single connection step still needed.

## Prepare transcription only when needed

A request to summarize existing text usually needs no new transcription. When the user requests re-transcription or audio analysis, inspect the actual spoken language, chip, memory pressure, and disk space. Reuse a suitable verified setup or install one selected route.

For a new environment, the integrated Whisper.cpp route supplies two executable starting choices:

| Setup choice | Model download | When to begin here |
| --- | ---: | --- |
| `small` — Whisper small Q5_1 | 190.09 MB | Limited memory, an Intel Mac, or a lightweight first pass |
| `turbo` — Whisper large-v3-turbo Q5_0 | 574.04 MB | A capable Mac and a task that warrants the larger multilingual model |

These are model downloads, not complete installed size or peak memory. The runtime, audio conversion tools, and any required dependencies are additional. Inspect the setup plan and explain the selected downloads before starting; a request to prepare transcription already authorizes the necessary setup. Do not install both models by default.

Anonymous speaker diarization is included by default, with approximately 30 MB of speaker model files plus its runtime dependencies. If the user says “do not distinguish speakers,” pass `--no-diarize` during both setup and transcription so speaker weights and packages are not required or downloaded for that route.

```text
/bin/sh <skill-root>/scripts/bootstrap.sh setup --audio --model small
/bin/sh <skill-root>/scripts/bootstrap.sh transcribe --audio <local-audio> \
  --output <new-private-output-directory> --language auto
```

For the explicit speaker opt-out:

```text
/bin/sh <skill-root>/scripts/bootstrap.sh setup --audio --model small --no-diarize
/bin/sh <skill-root>/scripts/bootstrap.sh transcribe --audio <local-audio> \
  --output <new-private-output-directory> --language auto --no-diarize
```

Setup downloads and verifies the selected assets, configures their paths, and verifies the pipeline with synthetic input. `transcribe` reuses that setup; it does not download missing models itself. Fix a missing prerequisite through setup instead of silently changing the task or dropping speaker labels.

Use [Language strategies](audio-language-routing.md) when a specialist is justified by the recording. Those other engines require their own installation and verification; their appearance in a guide does not mean `setup --audio` can install them. Obtain only the selected route. Hand off required upstream account, license, or secret entry to the user's local interface; never ask for credentials in chat.

## Make the work visible

Before starting, name the selected capability, download size when known, and purpose. Relay the tools' meaningful stages: environment check, download, byte/hash verification, extraction or dependency preparation, configuration, synthetic check, and ready. Use actual bytes or counts for measurable work. For inference and installation steps with no reliable denominator, report the current stage instead of inventing a percentage.

An interrupted or failed setup is not ready. Explain the failed stage, preserve the previous working capability, and fix the concrete cause before retrying. The user should not have to infer success from a downloaded file or a running process.

At completion, give a compact capability report in the user's language:

- Skill installed and discoverable by the host.
- Keyword retrieval ready to use after the first phone sync.
- Semantic search verified, deferred, or unavailable with a specific reason.
- Transcription not installed yet, or the selected route verified with the speaker preference.
- Phone paired and synced, or the remaining phone/system action.

Show useful local output locations separately from progress. User data defaults to `~/Library/Application Support/AudayWorkflow/`; each phone profile contains its persistent database and index. For an isolated environment use `--home <data-root>` and, when needed, `--profile <name>`. Both are global options before the command, with `--home` first:

```text
/bin/sh <skill-root>/scripts/bootstrap.sh --home <data-root> --profile <name> setup
```

Runtime readiness, synthetic validation, representative-audio quality, and physical-phone behavior are separate results. Report the evidence actually obtained.
