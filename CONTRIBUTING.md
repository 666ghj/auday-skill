# Contributing

Keep changes focused and preserve the read-only phone contract. Include relevant tests and describe the observable behavior, limitations, and validation performed.

Install repository hooks after cloning:

```sh
sh tools/git/install-hooks.sh
```

Before submitting:

```sh
python3 tools/verify_public.py --history
python3 -m unittest discover -s tests -v
swift test --package-path scripts/link
git diff --check
```

Use synthetic fixtures. Do not attach recordings, transcript bodies, pairing passwords, local databases, model caches, device IDs, or machine-specific paths to commits or public reports. Tests should assert meaningful behavior, not documentation wording.

New runtime dependencies need a clear purpose and a reviewed license/source. Optional model downloads must be explicit and pinned. Preserve the distinction between a runnable toolchain and measured quality on real audio.

For a defect report, include a redacted error identifier, software versions, transport type, expected outcome, and observed outcome. Do not paste credentials or private data. Use private reporting for potentially exploitable issues rather than publishing personal data as a reproduction.
