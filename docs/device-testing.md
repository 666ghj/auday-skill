# Physical-device test checklist

Run this after installing the skill and a compatible Auday iPhone build. Keep source validation, simulator results, and physical-device observations separate.

## Before connecting

- Record the skill version and phone app version in private test notes.
- Confirm the phone has **Connect to your workflow**. If it does not, install a compatible build before testing; the skill cannot enable this remotely.
- Use the same reachable LAN first. Disable no system security protections just to make discovery pass.
- Run `doctor` from the installed skill directory, not from a development checkout.
- Keep real test notes, recordings, IP addresses, device IDs, credentials, and screenshots outside the repository.

## First connection

1. Select **All text** on the phone and enable access.
2. Discover the phone. Enter the generated communication password in the local prompt.
3. Confirm `status --live` reports `text-only-read-only`.
4. Synchronize. Compare a few known saved records and their source references with the phone.
5. Confirm the audio catalogue is empty and a direct audio request is rejected.
6. Change the phone to **Text and audio**, reopen access, and synchronize again.
7. Download one selected recording. Verify its hash, duration, availability, and timeline mapping.

## Recovery and lifecycle

- Use an incorrect password and confirm no record metadata is returned.
- Interrupt a download; reconnect and verify recovery without publishing a corrupt asset.
- Close the phone page and confirm cached queries still work while live requests fail or wait explicitly.
- Change the password and confirm the old password cannot read new data.
- Change the access scope and confirm the next sync reflects it. Existing copies should not be silently erased.
- Lock the phone, switch apps, disconnect Wi-Fi, and sleep the Mac. Record the actual outcomes rather than assuming background support.

## Wireless and no-cloud claims

Test LAN first, then separately test Apple peer-to-peer discovery without a shared access point. Simulator loopback is not wireless evidence. For a no-cloud transport claim, disable WAN access or inspect the actual connection path while discovery, pairing, sync, and local queries complete. AI inference is a separate data-flow question.

## Optional audio analysis

Start with a short user-authorized recording and already-approved local models. Save outputs separately. Review names, timing, speaker boundaries, and difficult acoustic regions. Do not call the result ground truth, claim identity recognition from anonymous clusters, or extrapolate synthetic smoke tests to all languages.

Only publish synthetic or carefully redacted test summaries. A failed physical connection is a test result to investigate, not a reason to extract the app container or bypass its permissions.
