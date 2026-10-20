# Third-party notices

The original source in this repository is distributed under the MIT license in `LICENSE`. The following tools and optional dependencies are installed separately; their software and model weights are not relicensed by this repository.

| Component | Use | Source |
| --- | --- | --- |
| Apple Foundation, Network, Security, CryptoKit | Native transport and Keychain | Apple platform SDKs |
| Python and SQLite | Local client and cache | Python standard library |
| Whisper.cpp | Optional local transcription | https://github.com/ggml-org/whisper.cpp |
| FFmpeg | Optional local audio conversion | https://ffmpeg.org/ |
| sherpa-onnx and sherpa-onnx-core | Optional speaker diarization | https://github.com/k2-fsa/sherpa-onnx |
| NumPy and SoundFile | Optional audio input and numerical processing | https://numpy.org/ and https://github.com/bastibe/python-soundfile |
| llama.cpp | Optional local semantic embedding runtime | https://github.com/ggml-org/llama.cpp |
| EmbeddingGemma | Optional semantic retrieval model, subject to Gemma terms | https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF and https://ai.google.dev/gemma/terms |
| uv and Python Build Standalone | Private first-run package manager and Python runtime | https://github.com/astral-sh/uv and https://github.com/astral-sh/python-build-standalone |
| CMake | Isolated Whisper.cpp build tool | https://cmake.org/ |
| imageio-ffmpeg | Isolated FFmpeg distribution | https://github.com/imageio/imageio-ffmpeg |

Optional Python package versions and distribution hashes are pinned in `scripts/uv.lock`. FFmpeg licensing depends on the selected build. Speaker segmentation, embedding, and speech-recognition weights have their own licenses and access conditions; review those at the source before use.

First-run downloads are not bundled in this repository. Runtime archives are
pinned in `scripts/runtime-assets.tsv`; audio sources, weights, hashes, and model
licenses are recorded in `scripts/audio-assets.json`. Whisper.cpp and Whisper
weights use MIT; the selected public segmentation export uses MIT and CAMPPlus
uses Apache-2.0. The imageio-ffmpeg wrapper uses BSD-2-Clause. Its selected macOS
FFmpeg binary is a GPL-enabled build; the audited arm64 wheel reports
GPL-2.0-or-later. Preserve and consult the installed wheel's license files.

No Auday app source, third-party application logos, model weights, personal recordings, or private datasets are bundled. Tests generate synthetic data locally.
