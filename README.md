<p align="center">
  <img src="docs/assets/auday-banner.png" alt="Auday" width="100%">
</p>

<h1 align="center">Auday Skill</h1>

<p align="center">Bring what Auday knows into your workflow</p>

<p align="center">
  <strong>English</strong> &nbsp;·&nbsp; <a href="README.zh-CN.md">简体中文</a> &nbsp;·&nbsp; <a href="README.ja.md">日本語</a>
</p>

**Auday turns your Apple Watch into an all-day AI recording assistant.** It combines recordings with body signals such as heart rate, real-time stress estimates, location, and activity to connect the moments of your day on iPhone. Highlights, daily reflections, and AI conversations linked to the original words help you revisit important moments, find connections, and build a personal memory archive over time.

<p align="center">
  <a href="https://apps.apple.com/app/id6814210373">
    <img src="https://toolbox.marketingtools.apple.com/api/badges/download-on-the-app-store/black/en-us" alt="Download on the App Store" height="40">
  </a>
</p>

<p align="center"><a href="https://auday.ai/">Visit auday.ai</a></p>

This repository provides **Auday’s desktop companion skill**. It brings the context saved in the app to your desktop AI assistant for searching, writing, and deeper audio analysis on your Mac or Windows PC, including Windows 11 ARM64. Phone data stays read-only, with sources attached to the information you use.

**Auday data travels directly between your iPhone and computer over an encrypted local protocol, without passing through any cloud relay server.**

## Install through your desktop agent

Open a desktop agent that supports local skills and send:

> Install the Auday skill from https://github.com/666ghj/auday-skill for this application. Complete first-run setup, recommend local semantic search, and guide me through connecting Auday on my iPhone. Install transcription tools only when I need them.

Different hosts use different skill locations and import formats. Ask the agent to install the skill in the location supported by your application and verify that it can be discovered.

The agent uses its native skill installer, checks the new environment, and prepares the required tools. You do not need to install Python or a package manager yourself or run commands in a terminal. Windows setup needs no WSL, MSVC, or separate GPU. If the operating system needs a password, permission, or license acceptance, the agent explains that local step and continues afterward.

On the first run, the agent recommends **local semantic search**: a 333.59 MB model and runtime archives of about 11.2 MB on Mac, 18.43 MB on Windows x64, or 11.99 MB on Windows ARM64. Windows also prepares a private Microsoft runtime package when needed: about 3.22 MB for x64 or 5.05 MB for ARM64. Base tools are downloaded separately when needed; the agent reports the selected downloads. Extracted files and indexes use additional space. You can install it now or leave it for later; the choice is saved. Keyword search remains available without it. Model use is subject to the [Gemma terms](https://ai.google.dev/gemma/terms).

**Transcription is prepared on demand.** When you request it, the agent selects one suitable model, downloads and verifies it, saves the configuration, and tests the pipeline. Anonymous speaker labels are included by default. Say “do not distinguish speakers” to skip the speaker models and dependencies.

Downloads and preparation show the current stage and measured progress. The agent distinguishes tools that are ready from phone pairing and the first synchronization. See the [first-run guide](references/first-run.md) for the complete agent workflow.

For hosts without a native installer, the agent can copy the package to the supported skill directory, or use `tools/install.py` in an environment that already has Python. That installer copies only the skill runtime, references, metadata, license, and version; it does not prepare models. It refuses to overwrite an existing installation and does not modify host configuration. Restart or refresh the host if the new skill is not discovered. The agent then runs the installed `scripts/bootstrap.sh` on Mac or `scripts/bootstrap.ps1` through Windows PowerShell to complete first-run setup, even on a computer without Python.

## Connect

Before connecting: use the same Wi-Fi on your iPhone and computer, allow the required **Local Network** access when asked, and keep the phone's connection page open during transfer.

1. On iPhone, open **Settings → Connect to your workflow**.
2. Choose **All text** or **Text and audio**, then enable access. Both options are read-only.
3. Ask your desktop agent to connect to Auday. Enter the phone's generated communication password in the local prompt, never in chat.
4. Keep the iPhone page active during synchronization. Ask your agent to use the available context.

Example requests:

- “What did we decide about the launch? Show the original discussions.”
- “Turn this week's project conversations into a handoff brief.”
- “Review today's memories and separate commitments from possible follow-ups.”
- “Use my computer to analyze this recording, preserving its original timeline.”

The agent can use exact keyword search or [local semantic search](references/semantic-search.md) for concepts and paraphrases. If you chose semantic search during setup, the agent builds the persistent index after the first sync. It does not send text to an embedding API. The agent shows processing stages and measured counts during synchronization, indexing and audio analysis.

Synchronization defaults to incremental transfer with a compatible phone: a complete lightweight manifest is checked, and only new, changed, or locally damaged text bodies are downloaded. Ask for a full refresh to use `sync --mode full`. Older phones report an explicit full-transfer fallback; the first verified baseline still downloads all text once. Phone snapshot preparation remains a full-archive operation.

Your synchronized SQLite database persists after restarting the computer. Sync also maintains a [local file library](references/library.md), with an index, records organized by date and type, complete source metadata, and a separate `notes/` folder for your own work. Unchanged files are reused; removed sources and edits to managed files are preserved separately. Existing caches can create this library offline with `library`. The [readable export](references/context.md#readable-files) remains available for independent static snapshots.

## Requirements

- **Windows:** Windows 10/11 x64 or Windows 11 ARM64, with Windows PowerShell 5.1. Setup detects the native OS architecture and prepares private `uv` 0.13.3, native Python 3.13.16, and matching CPU model runtimes. No pre-installed Python, WSL, MSVC, or GPU is required.
- **Mac:** macOS 13 or later for the core client; macOS 13.3 or later for the pinned semantic runtime. Phone connection and the audio build need Xcode Command Line Tools with Swift 5.9 or later; the agent can start Apple's installer if they are missing.
- An Auday iPhone build with **Connect to your workflow**, with both devices connected to the same Wi-Fi.
- Linux and Windows 10 ARM64 are outside the supported setup baseline. Optional local model engines have their own CPU requirements; setup verifies each selected capability.
- Models are not bundled. The agent installs selected semantic or transcription assets during their setup flow, verifies their hashes, and checks that they run locally.

Core connection and context retrieval do not require an AI API key, an Auday cloud account, or a cloud relay.

For deeper recording analysis, the [audio workflow](references/audio.md) covers the integrated Mac and Windows pipeline. The [language strategy guide](references/audio-language-routing.md) helps the agent choose for the actual speech; the [Mac audio guide](references/mac-audio-models.md) covers additional Apple-specific engines and resource planning. The agent can adapt the tools to your recordings and reuse a setup that works well.

The integrated transcription setup offers multilingual Whisper `small` Q5_1 (190.09 MB) or large-v3-`turbo` Q5_0 (574.04 MB), selected for your computer and task, plus about 30 MB of speaker model files by default. Runtime downloads and dependencies are additional. Windows uses native prebuilt CPU runtimes with FFmpeg; no separate GPU is needed. The x64 transcription runtime requires AVX2, FMA, and F16C; the ARM64 route uses its own native engine. If a selected optional model runtime is incompatible with the CPU, basic connection, text sync, keyword search, and readable export remain available. The agent installs one route and reuses it. Other engines in the guides need their own compatible setup. Anonymous speaker labels do not identify real people.

## Data and privacy

Both clients use a generated 16-character communication secret, HKDF-SHA256, and TLS 1.2 ECDHE-PSK with ChaCha20-Poly1305. They do not accept arbitrary short human passwords or fall back to plaintext. Mac uses its native Swift bridge; Windows uses managed Python TLS-PSK and Bonjour discovery. See the [protocol](docs/protocol.md) for the exact boundary and current limitations.

Pairing secrets are kept in macOS Keychain or Windows Credential Manager, not in chat or the database. The default persistent text database is `~/Library/Application Support/AudayWorkflow/default/library.sqlite` on Mac or `%LOCALAPPDATA%\AudayWorkflow\default\library.sqlite` on Windows, outside the skill folder. Model files, configuration, and indexes are also stored locally. Text is synchronized first; audio is fetched when needed. Disabling access prevents future reads but cannot recall copies already downloaded to another device.

With an on-demand companion, enabling access makes the phone available immediately; the archive is prepared only when the computer requests a sync. Large text records use resumable, verified chunks, keeping complete saved content and source references intact. Skill 0.3.0 also supports earlier companions that prepare their snapshots in advance.

Local transport does not mean a cloud-based assistant performs inference locally. Only return the excerpts needed for the task. Fully local workflows also require local inference and local downstream tools.

## Development

See the [first-run validation record](docs/first-run-validation.md) for recorded
installation coverage and its limits. A CI pass or synthetic peer does not establish
physical iPhone connectivity. The following native test commands are for macOS;
Windows tests use the managed Python 3.13 runtime and separate native x64 and ARM64 Windows CI jobs.

```sh
sh tools/git/install-hooks.sh
python3 tools/verify_public.py
python3 -m unittest discover -s tests -v
swift test --package-path scripts/link
swift test --package-path tests/native-peer
```

The test suite covers encrypted local connections, read-only scopes, snapshot consistency, resumable downloads, and offline queries using synthetic data. See [device testing](docs/device-testing.md) for phone connection checks. Keychain interaction is opt-in: `AUDAY_TEST_KEYCHAIN=1 python3 -m unittest discover -s tests -p test_pairing.py -v`. No real recordings or model downloads are needed for the default suite.


## License

MIT. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). Optional dependencies and model weights retain their own licenses.
