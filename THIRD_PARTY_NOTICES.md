# Third-party notices

The original source in this repository is distributed under the MIT license in `LICENSE`. The following tools and optional dependencies are installed separately; their software and model weights are not relicensed by this repository.

| Component | Use | Source |
| --- | --- | --- |
| Apple Foundation, Network, Security, CryptoKit | Native transport and Keychain | Apple platform SDKs |
| Windows Credential Manager | Current-user storage for Windows pairing secrets | Windows system APIs |
| Python and SQLite | Local client and cache | Python standard library |
| OpenSSL supplied with managed Python | Windows TLS-PSK transport | https://www.openssl.org/ |
| zeroconf | Windows Bonjour/mDNS discovery | https://github.com/python-zeroconf/python-zeroconf |
| Whisper.cpp | Optional local transcription | https://github.com/ggml-org/whisper.cpp |
| FFmpeg | Optional local audio conversion | https://ffmpeg.org/ |
| sherpa-onnx and sherpa-onnx-core | Optional speaker diarization | https://github.com/k2-fsa/sherpa-onnx |
| NumPy and SoundFile | Optional audio input and numerical processing | https://numpy.org/ and https://github.com/bastibe/python-soundfile |
| llama.cpp | Optional local semantic embedding runtime | https://github.com/ggml-org/llama.cpp |
| EmbeddingGemma | Optional semantic retrieval model, subject to Gemma terms | https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF and https://ai.google.dev/gemma/terms |
| uv and Python Build Standalone | Private first-run package manager and Python runtime | https://github.com/astral-sh/uv and https://github.com/astral-sh/python-build-standalone |
| CMake | Isolated Whisper.cpp build tool | https://cmake.org/ |
| imageio-ffmpeg | Isolated FFmpeg distribution | https://github.com/imageio/imageio-ffmpeg |
| Microsoft Visual C++ runtime DLLs | Private dependencies for the prebuilt Windows Whisper.cpp runtime | https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution |

Optional audio Python package versions and distribution hashes are pinned in `scripts/uv.lock`; Windows connection dependencies are pinned in `scripts/requirements-windows-link.txt`. FFmpeg licensing depends on the selected build. Speaker segmentation, embedding, and speech-recognition weights have their own licenses and access conditions; review those at the source before use.

First-run downloads are not bundled in this repository. Runtime archives are
pinned in `scripts/runtime-assets.tsv`; audio sources, weights, hashes, and model
licenses are recorded in `scripts/audio-assets.json`. Whisper.cpp and Whisper
weights use MIT; the selected public segmentation export uses MIT and CAMPPlus
uses Apache-2.0. The imageio-ffmpeg wrapper uses BSD-2-Clause. Its selected macOS
FFmpeg binary is a GPL-enabled build; the audited arm64 wheel reports
GPL-2.0-or-later. Preserve and consult the installed wheel's license files.

Windows audio setup downloads the official Whisper.cpp CPU archive and a matching
FFmpeg wheel. Required Microsoft runtime DLLs are obtained from the pinned official
Visual Studio 2022 CRT redistributable package and kept beside the private audio
runtime; this does not install MSVC or change a system runtime. The selected DLLs
are `msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`, and `vcomp140.dll`.
Microsoft's [runtime license terms](https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/)
apply separately; see the
[redistribution list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
and [deployment documentation](https://learn.microsoft.com/en-us/cpp/windows/deployment-in-visual-cpp?view=msvc-170).
Preserve the licenses supplied with the downloaded runtimes and wheels. No such
binaries are committed to this repository.

No Auday app source, third-party application logos, model weights, personal recordings, or private datasets are bundled. Tests generate synthetic data locally.
