# Auday Skill

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Bring what Auday knows into your workflow.

Use your desktop AI assistant to find a conversation, connect ideas across your memories, prepare a project brief, or explore recordings with your Mac's compute. Phone data stays read-only, with sources attached to the information you use.

This repository ships only the desktop skill. The Swift code is a macOS client bridge for Bonjour discovery, TLS connections, and Keychain access—not iPhone app code. The iPhone UI and data-export implementation stay in the Auday app repository.

## Current status

This is an early, installable macOS client and agent skill. It requires a compatible Auday iPhone build that exposes **Connect to your workflow**. The repository alone does not enable phone access, and this project does not claim that the currently distributed App Store build supports the connection.

The client has automated coverage for real local TLS connections, read-only scopes, complete snapshots, interrupted audio downloads, integrity checks, and offline queries. Physical iPhone networking and app-version compatibility must be checked separately. See [device testing](docs/device-testing.md).

## Install through your desktop agent

Open a desktop agent that supports local skills and send:

> Install the Auday skill from https://github.com/666ghj/auday-skill for this application. Verify that the installation works, then guide me through connecting Auday on my iPhone.

The repository must be accessible to the agent. While it is private, use your existing GitHub sign-in; do not paste access tokens into the conversation. Different hosts use different skill locations and import formats. A successful clone is not, by itself, a successful installation.

The agent can use its native skill installer. A deterministic local installer is also included:

```sh
python3 tools/install.py --destination ~/.agents/skills/auday
```

Use the directory supported by your host. The installer copies only the skill runtime, references, metadata, license, and version. It refuses to overwrite an existing installation and does not modify host configuration. Restart or refresh the host if the new skill is not discovered.

## Connect

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
- A compatible Auday iPhone build and a reachable local network. Apple peer-to-peer discovery is experimental and is not required for ordinary LAN use.
- Optional audio analysis: `ffmpeg`, `whisper-cli`, local model weights, and `uv` with Python 3.11–3.13 for speaker-analysis dependencies. Models are not bundled or downloaded automatically.

Core connection and context retrieval do not require an AI API key, an Auday cloud account, or a cloud relay.

## Data and privacy

The native bridge uses a generated 16-character communication secret, HKDF-SHA256, and TLS 1.2 ECDHE-PSK with ChaCha20-Poly1305. It does not accept arbitrary short human passwords, disable certificate checks, or fall back to plaintext. See the [protocol](docs/protocol.md) for the exact boundary and current limitations.

Pairing secrets are kept in macOS Keychain. The local cache lives in your user application-data directory, outside the source checkout. Text is synchronized first; audio is fetched when needed. Disabling access prevents future reads but cannot recall copies already downloaded to another device.

Local transport does not mean a cloud-based assistant performs inference locally. Only return the excerpts needed for the task. Fully local workflows also require local inference and local downstream tools.

## Development

```sh
sh tools/git/install-hooks.sh
python3 tools/verify_public.py
python3 -m unittest discover -s tests -v
swift test --package-path scripts/link
swift test --package-path tests/native-peer
```

On macOS, the test suite exercises actual encrypted loopback connections with synthetic data. Keychain interaction is opt-in: `AUDAY_TEST_KEYCHAIN=1 python3 -m unittest discover -s tests -p test_pairing.py -v`. No real recordings or model downloads are needed for the default suite.

The phone-side app is maintained separately. Tests use a synthetic macOS loopback peer under `tests/native-peer`; it is excluded from skill installations. No iPhone target, mobile UI, or production phone data adapter is included.

## License

MIT. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). Optional dependencies and model weights retain their own licenses.
