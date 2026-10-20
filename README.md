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

> Install the Auday skill from https://github.com/666ghj/auday-skill for this application. Verify that the installation works, then guide me through connecting Auday on my iPhone.

Different hosts use different skill locations and import formats. Ask the agent to install the skill in the location supported by your application and verify that it can be discovered.

The agent can use its native skill installer. A deterministic local installer is also included:

```sh
python3 tools/install.py --destination ~/.agents/skills/auday
```

Use the directory supported by your host. The installer copies only the skill runtime, references, metadata, license, and version. It refuses to overwrite an existing installation and does not modify host configuration. Restart or refresh the host if the new skill is not discovered.

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

## Requirements

- macOS 13 or later, Python 3.9 or later, and Xcode Command Line Tools with Swift 5.9 or later.
- An Auday iPhone build with **Connect to your workflow**, with the iPhone and Mac connected to the same Wi-Fi.
- Optional audio analysis: `ffmpeg`, `whisper-cli`, local model weights, and `uv` with Python 3.11–3.13 for speaker-analysis dependencies. Models are not bundled or downloaded automatically.

Core connection and context retrieval do not require an AI API key, an Auday cloud account, or a cloud relay.

For deeper recording analysis, the [language strategy guide](references/audio-language-routing.md) helps the agent choose among Japanese, Chinese and dialect-specific, English, Korean, French, German, and multilingual routes. The [Mac audio guide](references/mac-audio-models.md) covers speaker analysis and resource planning. The agent can adapt the tools to your recordings and reuse a setup that works well.

## Data and privacy

The native bridge uses a generated 16-character communication secret, HKDF-SHA256, and TLS 1.2 ECDHE-PSK with ChaCha20-Poly1305. It does not accept arbitrary short human passwords, disable certificate checks, or fall back to plaintext. See the [protocol](docs/protocol.md) for the exact boundary and current limitations.

Pairing secrets are kept in macOS Keychain. The local cache lives in your user application-data directory, outside the source checkout. Text is synchronized first; audio is fetched when needed. Disabling access prevents future reads but cannot recall copies already downloaded to another device.

With an on-demand companion, enabling access makes the phone available immediately; the archive is prepared only when the computer requests a sync. Large text records use resumable, verified chunks, keeping complete saved content and source references intact. Skill 0.3.0 also supports earlier companions that prepare their snapshots in advance.

Local transport does not mean a cloud-based assistant performs inference locally. Only return the excerpts needed for the task. Fully local workflows also require local inference and local downstream tools.

## Development

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
