# Auday Skill

This repository distributes the Auday desktop skill. Keep every commit suitable for redistribution. Preserve the existing license and never commit personal recordings, transcripts, pairing secrets, local caches, machine-specific paths, or private development notes.

- `SKILL.md`, `references/`, `agents/`, and `scripts/` are the installable skill. Keep references relative to the skill root. No other Auday checkout may be required at runtime.
- The desktop runtime supports macOS, Windows 10/11 x64, and Windows 11 ARM64. Swift targets remain macOS-only; Windows uses native managed Python and prebuilt local model runtimes for the host architecture. Keep synthetic peers under `tests/`; never add iPhone UI, app targets, or production phone data adapters.
- Phone data is read-only. Enforce `text-only-read-only` and `text-and-audio-read-only`; client parameters cannot expand the phone's scope.
- Password input stays on the user's computer. Never request a password in chat or pass one in command arguments. Use macOS Keychain or Windows Credential Manager for saved pairing secrets; never replace them with plaintext configuration.
- Retain provenance and source timestamps. Derived audio analysis never overwrites the original transcript or audio.
- Use synthetic fixtures. Optional models are not bundled or downloaded during normal tests. Do not use external inference services in tests.
- On macOS, install hooks with `sh tools/git/install-hooks.sh`. Run `tools/verify_public.py` and relevant Python tests with the selected platform's supported interpreter. Run `swift test --package-path scripts/link` for native macOS changes, and validate Windows runtime changes on both x64 and ARM64 Windows CI; a Mac test or emulated x64 run does not prove native Windows ARM64 support.
- Public repository and collaboration text is English. Respond to users in their preferred language.
- Keep source validation, simulator checks, physical-device testing, and published app support as separate claims.
- Do not publish a release or change repository visibility without explicit authorization.
