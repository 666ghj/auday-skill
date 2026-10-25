# Third-party notices

The original source in this repository is distributed under the MIT license in `LICENSE`. The following tools and optional dependencies are installed separately; their software and model weights are not relicensed by this repository.

| Component | Use | Source |
| --- | --- | --- |
| Apple Foundation, Network, Security, CryptoKit | Native transport and Keychain | Apple platform SDKs |
| Windows Credential Manager | Current-user storage for Windows pairing secrets | Windows system APIs |
| Python and SQLite | Local client and cache | Python standard library |
| OpenSSL supplied with managed Python | Windows TLS-PSK transport | https://www.openssl.org/ |
| zeroconf | Windows Bonjour/mDNS discovery | https://github.com/python-zeroconf/python-zeroconf |
| poetry-core and setuptools | Isolated pure-Python zeroconf build on Windows ARM64 | https://github.com/python-poetry/poetry-core and https://github.com/pypa/setuptools |
| Whisper.cpp | Optional local transcription | https://github.com/ggml-org/whisper.cpp |
| FFmpeg | Optional local audio conversion | https://ffmpeg.org/ |
| sherpa-onnx and sherpa-onnx-core | Optional speaker diarization | https://github.com/k2-fsa/sherpa-onnx |
| NumPy and SoundFile | Optional audio input and numerical processing | https://numpy.org/ and https://github.com/bastibe/python-soundfile |
| llama.cpp | Optional local semantic embedding runtime | https://github.com/ggml-org/llama.cpp |
| EmbeddingGemma | Optional semantic retrieval model, subject to Gemma terms | https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF and https://ai.google.dev/gemma/terms |
| uv and Python Build Standalone | Private first-run package manager and Python runtime | https://github.com/astral-sh/uv and https://github.com/astral-sh/python-build-standalone |
| CMake | Isolated Whisper.cpp build tool | https://cmake.org/ |
| imageio-ffmpeg | Isolated FFmpeg distribution | https://github.com/imageio/imageio-ffmpeg |
| Microsoft Visual C++ runtime DLLs | Private dependencies for prebuilt Windows model runtimes | https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution |
| LLVM OpenMP | Native Windows ARM64 model runtime dependency | https://github.com/llvm/llvm-project/tree/llvmorg-20.1.8/openmp |
| BtbN FFmpeg builds | Native Windows ARM64 FFmpeg distribution | https://github.com/BtbN/FFmpeg-Builds |

Optional audio Python package versions and distribution hashes are pinned in `scripts/uv.lock`; Windows connection dependencies are pinned in `scripts/requirements-windows-link.txt`. Windows ARM64 uses zeroconf's pure-Python build, with poetry-core 2.1.3 and setuptools 80.9.0 pinned and hash-locked in `scripts/requirements-windows-arm64-build.txt`; it does not compile Cython extensions. FFmpeg licensing depends on the selected build. Speaker segmentation, embedding, and speech-recognition weights have their own licenses and access conditions; review those at the source before use.

First-run downloads are not bundled in this repository. Runtime archives are
pinned in `scripts/runtime-assets.tsv`; audio sources, weights, hashes, and model
licenses are recorded in `scripts/audio-assets.json`. Whisper.cpp and Whisper
weights use MIT; the selected public segmentation export uses MIT and CAMPPlus
uses Apache-2.0. The imageio-ffmpeg wrapper uses BSD-2-Clause. Its selected macOS
FFmpeg binary is a GPL-enabled build; the audited arm64 wheel reports
GPL-2.0-or-later. Preserve and consult the installed wheel's license files.

Windows x64 audio setup downloads the official Whisper.cpp v1.7.6 CPU archive
and an imageio-ffmpeg wheel. Windows ARM64 uses the official Whisper.cpp `b5454`
archive at the v1.9.5 source revision and BtbN's FFmpeg 8.1.3 ARM64 LGPL build,
from the pinned `autobuild-2026-09-30-13-08` release. BtbN is linked by the
[FFmpeg download page](https://ffmpeg.org/download.html). The selected ARM64
FFmpeg binary is licensed under LGPL-3.0-or-later; its license is installed
beside the executable.

Semantic and audio setup obtain Microsoft runtime DLLs from pinned official
Visual Studio 2022 CRT redistributable packages: about 3.22 MB for x64 or 5.05 MB
for ARM64. They remain beside each private engine, without installing MSVC or
changing a system runtime. The x64 engines use `msvcp140.dll`, `vcruntime140.dll`,
and `vcruntime140_1.dll`; the x64 audio engine also uses `vcomp140.dll`. ARM64
engines use native `msvcp140.dll` and `vcruntime140.dll`; their package and hashes
are recorded in `scripts/windows-arm64-runtime.json`.
Microsoft's [runtime license terms](https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/)
apply separately; see the
[redistribution list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
and [deployment documentation](https://learn.microsoft.com/en-us/cpp/windows/deployment-in-visual-cpp?view=msvc-170).

ARM64 model runtimes also use LLVM OpenMP 20.1.8 under Apache-2.0 with the LLVM
exception. Audio setup obtains the DLL and `LICENSE-LLVM-OpenMP` from the same
pinned llama.cpp `b10865` ARM64 archive used by semantic setup, and installs it
under Whisper.cpp's required import name. The Visual Studio `debug_nonredist`
OpenMP DLL inside the Whisper.cpp download is not installed. The replacement's
source, hashes, and license are recorded in `scripts/audio-assets.json`.

Preserve the licenses supplied with the downloaded runtimes and wheels. No such
binaries are committed to this repository.

No Auday app source, third-party application logos, model weights, personal recordings, or private datasets are bundled. Tests generate synthetic data locally.
