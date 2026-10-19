# Auday Skill

This repository distributes the Auday desktop skill. Keep every commit suitable for redistribution. Preserve the existing license and never commit personal recordings, transcripts, pairing secrets, local caches, machine-specific paths, or private development notes.

- `SKILL.md`, `references/`, `agents/`, and `scripts/` are the installable skill. Keep references relative to the skill root. No other Auday checkout may be required at runtime.
- Runtime native targets are macOS-only. Keep synthetic peers under `tests/`; never add iPhone UI, app targets, or production phone data adapters.
- Phone data is read-only. Enforce `text-only-read-only` and `text-and-audio-read-only`; client parameters cannot expand the phone's scope.
- Password input stays on the user's computer. Never request a password in chat or pass one in command arguments. macOS Keychain holds saved pairing secrets.
- Retain provenance and source timestamps. Derived audio analysis never overwrites the original transcript or audio.
- Use synthetic fixtures. Optional models are not bundled or downloaded during normal tests. Do not use external inference services in tests.
- Install hooks with `sh tools/git/install-hooks.sh`. Run `python3 tools/verify_public.py`, relevant Python tests, and `swift test --package-path scripts/link` for native changes.
- Public repository and collaboration text is English. Respond to users in their preferred language.
- Keep source validation, simulator checks, physical-device testing, and published app support as separate claims.
- Do not publish a release or change repository visibility without explicit authorization.
