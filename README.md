<p align="center">
  <img src="docs/assets/auday-banner.png" alt="Auday" width="100%">
</p>

<h1 align="center">Auday Skill</h1>

<p align="center">Bring what Auday knows into your workflow</p>

<p align="center">
  <strong>English</strong> &nbsp;·&nbsp; <a href="README.zh-CN.md">简体中文</a> &nbsp;·&nbsp; <a href="README.ja.md">日本語</a>
</p>

**Auday data travels directly between your iPhone and Mac over an encrypted local protocol, without passing through any cloud relay server.**

Use your desktop AI assistant to find a conversation, connect ideas across your memories, prepare a project brief, or explore recordings with your Mac's compute. Phone data stays read-only, with sources attached to the information you use.

## Install through your desktop agent

Open a desktop agent that supports local skills and send:

> Install the Auday skill from https://github.com/666ghj/auday-skill for this application. Complete first-run setup, recommend local semantic search, and guide me through connecting Auday on my iPhone. Install transcription tools only when I need them.

Different hosts use different skill locations and import formats. Ask the agent to install the skill in the location supported by your application and verify that it can be discovered.

The agent uses its native skill installer, checks the new environment, and prepares the required tools. You do not need to install Python or Homebrew yourself or run commands in a terminal. If macOS needs a system password or a license acceptance, the agent explains that system step and continues afterward.

On the first run, the agent recommends **local semantic search**: a 333.59 MB model and about 11.2 MB of runtime archives, plus about 40 MB for the base environment when needed. Extracted files and indexes use additional space. You can install it now or leave it for later; the choice is saved. Keyword search remains available without it. Model use is subject to the [Gemma terms](https://ai.google.dev/gemma/terms).

**Transcription is prepared on demand.** When you request it, the agent selects one suitable model, downloads and verifies it, saves the configuration, and tests the pipeline. Anonymous speaker labels are included by default. Say “do not distinguish speakers” to skip the speaker models and dependencies.

Downloads and preparation show the current stage and measured progress. The agent distinguishes tools that are ready from phone pairing and the first synchronization. See the [first-run guide](references/first-run.md) for the complete agent workflow.

For hosts without a native installer, the package can be copied to the supported skill directory. A deterministic local installer is also available when Python is already present:

```sh
python3 tools/install.py --destination ~/.agents/skills/auday
```

Use the directory supported by your host. This installer copies only the skill runtime, references, metadata, license, and version; it does not prepare models. It refuses to overwrite an existing installation and does not modify host configuration. Restart or refresh the host if the new skill is not discovered. The agent then runs the installed `scripts/bootstrap.sh` to complete first-run setup, even on a computer without Python.

## Connect

Before connecting: use the same Wi-Fi on your iPhone and Mac, allow **Local Network** access when asked, and keep the phone's connection page open during transfer.

1. On iPhone, open **Settings → Connect to your workflow**.
2. Choose **All text** or **Text and audio**, then enable access. Both options are read-only.
3. Ask your desktop agent to connect to Auday. Enter the phone's generated communication password in the local prompt, never in chat.
4. Keep the iPhone page active during synchronization. Ask your agent to use the available context.

Example requests:

- “What did we decide about the launch? Show the original discussions.”
- “Turn this week's project conversations into a handoff brief.”
- “Review today's memories and separate commitments from possible follow-ups.”
- “Use my Mac to analyze this recording, preserving its original timeline.”

The agent can use exact keyword search or [local semantic search](references/semantic-search.md) for concepts and paraphrases. If you chose semantic search during setup, the agent builds the persistent index after the first sync. It does not send text to an embedding API. The agent shows processing stages and measured counts during synchronization, indexing and audio analysis.

Your synchronized library is a SQLite file on disk, retained after restarting the Mac. Ask the agent to [export readable files](references/context.md#readable-files) for an index and daily Markdown documents you can browse or search in a text editor.

## Requirements

- macOS 13 or later for the core client; macOS 13.3 or later for the pinned semantic runtime.
- An Auday iPhone build with **Connect to your workflow**, with the iPhone and Mac connected to the same Wi-Fi.
- Xcode Command Line Tools with Swift 5.9 or later for phone connection and the integrated audio build. The agent checks for them and can start Apple's installer when they are missing; required system dialogs remain on your Mac.
- The agent prepares a private `uv` and managed Python when needed. No pre-existing global Python or Homebrew installation is assumed.
- Models are not bundled. The agent installs selected semantic or transcription assets during their setup flow, verifies their hashes, and checks that they run locally.

Core connection and context retrieval do not require an AI API key, an Auday cloud account, or a cloud relay.

For deeper recording analysis, the [language strategy guide](references/audio-language-routing.md) helps the agent choose among Japanese, Chinese and dialect-specific, English, Korean, French, German, and multilingual routes. The [Mac audio guide](references/mac-audio-models.md) covers speaker analysis and resource planning. The agent can adapt the tools to your recordings and reuse a setup that works well.

The integrated transcription setup offers multilingual Whisper `small` Q5_1 (190.09 MB) or large-v3-`turbo` Q5_0 (574.04 MB), selected for your Mac and task, plus about 30 MB of speaker model files by default. Runtime downloads and dependencies are additional. The agent installs one route and reuses it. Other engines in the guides need their own setup. Anonymous speaker labels do not identify real people.

## Data and privacy

The native bridge uses a generated 16-character communication secret, HKDF-SHA256, and TLS 1.2 ECDHE-PSK with ChaCha20-Poly1305. It does not accept arbitrary short human passwords, disable certificate checks, or fall back to plaintext. See the [protocol](docs/protocol.md) for the exact boundary and current limitations.

Pairing secrets are kept in macOS Keychain. The default text database is the persistent file `~/Library/Application Support/AudayWorkflow/default/library.sqlite`, outside the skill folder. Model files, configuration, and indexes are also stored locally. Text is synchronized first; audio is fetched when needed. Disabling access prevents future reads but cannot recall copies already downloaded to another device.

With an on-demand companion, enabling access makes the phone available immediately; the archive is prepared only when the computer requests a sync. Large text records use resumable, verified chunks, keeping complete saved content and source references intact. Skill 0.3.0 also supports earlier companions that prepare their snapshots in advance.

Local transport does not mean a cloud-based assistant performs inference locally. Only return the excerpts needed for the task. Fully local workflows also require local inference and local downstream tools.

## Development

See the [first-run validation record](docs/first-run-validation.md) for clean
Apple Silicon and Intel installation coverage and its limits.

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
