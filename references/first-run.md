# First-run setup on Mac or Windows

Use this flow after the host installs the skill files or when a requested capability is missing. The user should be able to ask for the result in ordinary language; the agent performs the commands, downloads, configuration, and checks.

Do not infer readiness from the developer's computer or another skill installation. A new computer may have no Python, `uv`, developer tools, models, pairing, or Auday data. The file installer only copies the skill package. Complete this capability check before describing the installation as ready to use.

## Check without changing the computer

Resolve `<skill-root>` to the installed folder containing `SKILL.md`; quote absolute paths in real commands. Use the entrypoint for the actual host:

On macOS:

```sh
/bin/sh "<skill-root>/scripts/bootstrap.sh" check
```

On Windows:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<skill-root>\scripts\bootstrap.ps1" check
```

In the remaining examples, `<auday>` means that full platform-specific prefix, without the trailing `check`. Replace this documentation shorthand with the real prefix; it is not a command or alias already on the user's computer. Both entrypoints accept the same command names and arguments. Keep Windows commands on one line rather than copying a shell backslash continuation.

`check` works without Python, creates no setup directories, and downloads nothing. Inspect OS, architecture, available disk/memory, and existing managed setup. Explain only the missing prerequisites that affect the user's next action.

| Host | Supported setup | Platform requirements |
| --- | --- | --- |
| Windows x64 | Windows 10/11, Windows PowerShell 5.1 | Native private Python 3.13.16; no pre-installed Python, WSL, Homebrew, MSVC, or separate GPU is needed |
| Windows ARM64 | Windows 11, Windows PowerShell 5.1 | Native private Python 3.13.16 and ARM64 model runtimes; the same commands and no developer tools are needed |
| macOS | macOS 13 or later; Apple Silicon or Intel | Private Python 3.12.15; the pinned semantic runtime requires macOS 13.3 or later; connection/audio builds require Apple developer tools |

Linux and Windows 10 ARM64 are outside this setup baseline. The Windows entrypoint detects the native OS architecture even from an emulated x64 agent; use its result rather than the agent process's architecture. Check each capability independently: an unavailable optional runtime must not block available connection, keyword search, or readable exports. Do not install a binary for a different architecture.

## Prepare the base environment

```text
<auday> doctor
<auday> setup
```

The bootstrap prepares private `uv` 0.13.3 and the host's managed Python without relying on a global Python or package manager. Read the selected download sizes from the setup output; the base environment and platform dependencies are additional to model downloads, and extracted files use more disk space. `doctor` checks the environment, and `setup` reads saved onboarding state and capability suggestions.

On Windows, the entrypoint prepares the locked Python connection environment, including Bonjour discovery through `zeroconf` and Python's TLS-PSK support. ARM64 uses the pinned pure-Python `zeroconf` build with hash-locked build dependencies; no Cython or compiler is required. Audio tools are prebuilt for the native architecture. `-ExecutionPolicy Bypass` affects only the launched PowerShell process; do not change the user's persistent execution policy. If an organization blocks scripts or network access, explain that specific policy and continue with available cached capabilities; do not disable security software.

On macOS, phone discovery and encrypted connection use a bundled Swift bridge that requires Swift 5.9 or later. The integrated audio setup also needs Apple's compiler to build Whisper.cpp. If Xcode Command Line Tools are missing, the agent can start the macOS installer:

```text
xcode-select --install
```

Explain the system dialog and wait only for steps the user must perform, such as entering a system password or accepting a system license. Recheck afterward. Do not describe this as a fully unattended fresh-Mac install, and do not treat a toolchain issue as a reason to download audio models. Local capabilities that do not need the bridge can be prepared separately.

If the available Swift is too old, update the compatible Apple tools; an older
macOS installation may need a system update to obtain them. Do not treat the
presence of `/usr/bin/swift` alone as a ready compiler.

## Recommend semantic search once

At the first onboarding, recommend installing local semantic search so the user can search ideas and paraphrases as well as exact words. Give one concise choice in the user's language:

> I recommend preparing local semantic search so I can find ideas even when you use different words. The model is 333.59 MB, with a small local runtime and base tools downloaded separately. It runs on this computer. Shall I prepare it now, or leave it for later?

Give the relevant runtime download size: about 11.2 MB on Mac, 18.43 MB for Windows x64, or 11.99 MB for Windows ARM64. Windows may also need its private Microsoft runtime package: about 3.22 MB for x64 or 5.05 MB for ARM64. Explain that extraction and future indexes need additional disk space.

Mention the model's [Gemma terms](https://ai.google.dev/gemma/terms) when presenting this setup. This does not make the entire desktop assistant local: relevant excerpts returned to a cloud assistant can still reach its model.

If the user already asked to configure semantic search, proceed under that request without asking the same question again. If they choose now:

```text
<auday> setup --semantic
```

The setup downloads the pinned compatible runtime and complete model, verifies SHA-256 hashes, saves the local configuration, and runs a small synthetic verification. Only successful verification means the semantic capability is ready. It can be prepared before pairing; the user's actual archive is indexed after the first successful sync.

The pinned Windows ARM64 semantic engine targets ARMv8.7-A. An older ARM processor may run the base client but fail this optional engine's runtime check. Report that capability limit and retain keyword search and export; do not retry with an x64 binary or treat it as a failed phone connection. See [semantic search](semantic-search.md) for the exact runtime.

The selected installation is remembered before downloads begin. If it is
interrupted, `setup` reports `resume_semantic_setup`; continue the requested
preparation instead of asking the same installation question again.

If the user chooses later or declines:

```text
<auday> setup --defer-semantic
```

Keep keyword search and readable export available. Honor the saved choice instead of repeating the same offer on every invocation. Revisit it when the user requests semantic search or asks to change the setup. Do not silently download the model when a keyword query would satisfy the task.

## Connect and obtain useful context

Follow [Connection](connection.md): same Wi-Fi, local-network permission, phone connection page open, and a local password prompt. Keep “skill files installed,” “tools verified,” “phone paired,” and “first sync complete” as separate states.

After sync, build the semantic index if that capability was selected:

```text
<auday> index
<auday> index-status
```

Show actual record counts while indexing. A successful synthetic check before pairing does not imply that the user's archive has been indexed. If the phone is not available, finish the selected local setup and explain the single connection step still needed.

## Prepare transcription only when needed

A request to summarize existing text usually needs no new transcription. When the user requests re-transcription or audio analysis, inspect the actual spoken language, chip, memory pressure, and disk space. Reuse a suitable verified setup or install one selected route.

For a new environment, the integrated Whisper.cpp route supplies two executable starting choices:

| Setup choice | Model download | When to begin here |
| --- | ---: | --- |
| `small` — Whisper small Q5_1 | 190.09 MB | Limited memory, a CPU-first Windows workflow, or a lightweight first pass |
| `turbo` — Whisper large-v3-turbo Q5_0 | 574.04 MB | Enough memory and a task that warrants the larger multilingual model |

These are model downloads, not complete installed size or peak memory. Windows installs an official prebuilt Whisper.cpp CPU runtime and a matching FFmpeg binary for x64 or ARM64; it does not compile them or require a GPU. The x64 audio runtime requires AVX2, FMA, and F16C. Check the selected platform and processor prerequisites before audio setup; a failed audio capability does not block ordinary connection, text sync, keyword retrieval, or readable exports. Mac builds the selected Whisper.cpp runtime with Apple tools. Read the download sizes and model choices returned by `setup`, then explain the selected downloads before starting; a request to prepare transcription already authorizes the necessary setup. Do not install both models by default.

Allow room for extracted runtimes, build products, and resumable download caches.
As a practical starting allowance, keep at least 1 GB free for semantic setup,
2 GB for the small audio route, or 3 GB for turbo; these are planning allowances,
not measured peak memory or exact final disk sizes.

Anonymous speaker diarization is included by default, with approximately 30 MB of speaker model files plus its runtime dependencies. If the user says “do not distinguish speakers,” pass `--no-diarize` during both setup and transcription so speaker weights and packages are not required or downloaded for that route.

```text
<auday> setup --audio --model small
<auday> transcribe --audio <local-audio> --output <new-private-output-directory> --language auto
```

For the explicit speaker opt-out:

```text
<auday> setup --audio --model small --no-diarize
<auday> transcribe --audio <local-audio> --output <new-private-output-directory> --language auto --no-diarize
```

Setup downloads and verifies the selected assets, configures their paths, and verifies the pipeline with a public sample recording. `transcribe` reuses that setup; it does not download missing models itself. Fix a missing prerequisite through setup instead of silently changing the task or dropping speaker labels.

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

Show useful local output locations separately from progress. User data defaults to `~/Library/Application Support/AudayWorkflow/` on Mac and `%LOCALAPPDATA%\AudayWorkflow\` on Windows; each phone profile contains its persistent database and index. These are disk files the user can browse, not memory-only caches. For an isolated environment use `--home <absolute-data-root>` and, when needed, `--profile <name>`. Both are global options before the command, with `--home` first:

```text
<auday> --home "<absolute-data-root>" --profile <name> setup
```

Runtime readiness, synthetic validation, representative-audio quality, and physical-phone behavior are separate results. Report the evidence actually obtained.
