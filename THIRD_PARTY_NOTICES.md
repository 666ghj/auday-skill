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

Optional Python package versions and distribution hashes are pinned in `scripts/uv.lock`. FFmpeg licensing depends on the selected build. Speaker segmentation, embedding, and speech-recognition weights have their own licenses and access conditions; review those at the source before use.

No Auday app source, third-party application logos, model weights, personal recordings, or private datasets are bundled. Tests generate synthetic data locally.
