# Choosing local audio models on Mac

Reviewed 2026-10-20. Use this guide when selecting an engine or planning deeper analysis. Recommendations are starting points for a representative audio check, not measured accuracy rankings on the user's recordings.

This reference covers Mac-specific engines and hardware choices. Windows 10/11 x64 and Windows 11 ARM64 use the integrated native CPU workflow in [Local audio analysis](audio.md), with prebuilt Whisper.cpp, FFmpeg, and optional default sherpa-onnx diarization. They do not need these Apple runtimes or a developer-tool installation.

## Choose a route

Start with [language and dialect routing](audio-language-routing.md) for Japanese, Chinese, English, Korean, French, German and mixed-language recordings. These are adaptable strategies; select for the actual speech and the user's quality/turnaround needs rather than assigning every user the same engine.

The bundled `refine_audio.py` currently runs **Whisper.cpp**, with **sherpa-onnx** anonymous diarization enabled by default. For a new Mac, [first-run setup](first-run.md) can install and verify multilingual `small` or `turbo` plus the required tools, without pre-existing global Python or Homebrew. Honor an explicit request to omit speaker labels with `--no-diarize` during both setup and transcription; that route skips speaker models and dependencies. Apply the same preference when selecting another route. The other routes below use separately installed local tools; their model IDs are not arguments supported by the integrated setup or runner.

| Need | Recommended starting point | Why and what to verify |
| --- | --- | --- |
| Use the existing Auday runner | A language-appropriate Whisper.cpp model, such as multilingual large-v3-turbo or a Japanese Kotoba GGML checkpoint | Metal acceleration and quantized models fit Mac workflows. Compare large-v3 where quality warrants it. Check each checkpoint's language coverage and installed-runtime compatibility; see [audio.md](audio.md). |
| Chinese-first or Chinese/English conversations on Apple Silicon | Qwen3-ASR-1.7B via MLX Audio, starting with the 8-bit conversion | Qwen documents Chinese, English and dialect coverage; MLX Audio provides an Apple Silicon runtime. Evaluate names, English terms inside Chinese sentences, omissions and repetitions against the Whisper baseline. ASR language coverage alone does not prove code-switch accuracy. |
| Less available memory or a quick first pass | A smaller or quantized model within the selected language route | Examples include a smaller multilingual Whisper model, Qwen3-ASR-0.6B for a supported language, or Japanese-focused ReazonSpeech K2. Reduce model size before increasing concurrency. |
| Meeting speaker turns | pyannote Community-1 as a separate local diarization stage | It provides ordinary overlapping turns plus an exclusive timeline useful for matching transcription. Keep both when overlap matters. Verify short interjections and speaker-count errors. |
| Lightweight speaker turns using the bundled runner | sherpa-onnx segmentation + speaker embeddings + clustering | Already supported. Use actual local segmentation and embedding files; automatic clusters may merge or split voices. |
| Reuse a native WhisperKit installation | WhisperKit for ASR, SpeakerKit for diarization | These are separate products in Argmax's open-source Swift package. Check the installed CLI and model versions; features from the separate Pro SDK are not implied by installing the open-source package. |
| Long conversational context with joint text, times and speaker labels | VibeVoice-ASR long-form through a compatible MLX runtime | A promising larger-memory comparison candidate. Confirm the exact checkpoint and output schema; the long-form, streaming and BitNet variants have different contracts. |
| Native Apple integration or English/European-language throughput | FluidAudio/Core ML, with the exact language-appropriate checkpoint | Useful for a Swift/ANE pipeline. NVIDIA Parakeet TDT v3 itself lists 25 European languages, not Chinese. FluidAudio's separate Chinese engines do not make every Parakeet checkpoint Chinese-capable. |

Sources: [Whisper.cpp](https://github.com/ggml-org/whisper.cpp), [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR), [MLX Audio Qwen integration](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/stt/models/qwen3_asr/README.md), [Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), [sherpa-onnx diarization](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html), [VibeVoice-ASR](https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-asr.md), [FluidAudio](https://github.com/FluidInference/FluidAudio), [Parakeet TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3).

For the native WhisperKit/SpeakerKit route, see the [Argmax open-source SDK](https://github.com/argmaxinc/argmax-oss-swift). Reuse a working local setup before installing another overlapping runtime.

WhisperX remains useful for a combined ASR/alignment/diarization workflow, especially in an existing installation. Its upstream Mac example uses CPU INT8. Do not assume its faster-whisper backend uses Metal, or copy CUDA installation commands to a Mac. Prefer an MLX or Whisper.cpp ASR stage when Apple GPU execution is the objective. See [upstream instructions](https://github.com/m-bain/whisperX).

## Match the machine and the task

Inspect chip, architecture, installed models, free disk and current memory pressure before installing another engine. Model download size is not peak unified-memory use. Audio buffers, activations, decoder state, diarization and the desktop agent all share memory.

These are conservative scheduling heuristics, not hardware support requirements:

- **8 GB or already under memory pressure:** begin with a small model and one short asset; run ASR and diarization in separate processes.
- **16 GB:** sample the selected language-appropriate model, sequentially; lower its size or precision if the measured run swaps heavily.
- **24–32 GB:** there is more room to compare a relevant specialist and a working baseline when selection is needed, then run the chosen ASR, alignment and diarization stages sequentially. The machine's memory does not determine the recording's language.
- **Larger-memory Macs:** add VibeVoice as a measured alternative when long context or joint speaker output helps. Do not infer that a full hour fits from parameter count or a model-card demonstration.
- **Intel Macs:** retain a CPU-compatible Whisper.cpp route. MLX Apple Silicon guidance does not apply.

Choose from the user's goals: a quick project brief can often use existing Auday text; a detailed meeting transcript benefits from selected audio analysis. Mac compute expands the workflow without invalidating the phone's existing transcription.

## Prepare a reproducible local environment

For the integrated route, use `bootstrap.sh setup --audio --model small|turbo` with the model choice resolved to one value. The agent owns downloading, hash verification, configuration, and synthetic verification. `--no-diarize` excludes the optional diarization dependency group. Retain the saved working configuration for later tasks.

For other engines, use an isolated, task-local `uv` project for each optional runtime. Keep MLX Audio and pyannote/PyTorch dependencies separate from the bundled client's frozen environment. Record the tested runtime version or commit and lock dependencies after the first working installation.

Obtain only the selected model set. Record repository, immutable revision, quantization, file hashes and license. Community conversions are distinct artifacts from the original model release. For example, [Qwen3-ASR-1.7B-8bit](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit) is an MLX Community conversion, not a different Qwen architecture.

Community-1 requires accepting its model conditions and authenticated retrieval before offline use. Use the user's local credential mechanism; never request a token in chat. Load the downloaded pipeline from its local directory. Disable optional pyannote telemetry for a local-only workflow with `PYANNOTE_METRICS_ENABLED=0`; see the [telemetry documentation](https://github.com/pyannote/pyannote-audio#telemetry). CPU is the portable baseline; validate a chosen Apple acceleration path separately instead of assuming CUDA examples work on macOS.

After local weights and a compatible MLX Audio environment exist, its documented CLI can be used with a local model directory:

```text
HF_HUB_OFFLINE=1 uv run --project <local-mlx-project> --frozen \
  python -m mlx_audio.stt.generate \
  --model <local-qwen3-asr-mlx-directory> \
  --audio <local-sample.wav> --output-path <new-private-output-prefix>
```

Check the installed CLI's help before running it. This is an upstream-runtime example, not an Auday wrapper or evidence of a successful local inference. Do not substitute a model repository ID that silently downloads weights during the analysis stage.

## Build a useful meeting workflow

1. **Keep the source mapping.** Retain the asset ID, audio hash, source metadata and original time spans. Use a temporary 16 kHz mono derivative when a model needs it. Do not discard independent microphone channels before deciding whether their channel separation is useful.
2. **Check a representative sample when selecting or changing a route.** About 3–5 minutes is a practical starting point; include relevant language switches, terminology, quiet speech, speaker changes and overlap. Compare a working baseline with one relevant challenger when needed. Reuse an established satisfactory setup for routine work.
3. **Transcribe in recoverable units.** Preserve each chunk's source offset, overlap and deduplication decisions. For recordings with long silence, consider VAD with edge padding; test that it retains quiet words and brief replies. Whisper.cpp supports a separate VAD model, but the bundled Auday runner does not currently expose that option. Use a separately configured engine workflow when VAD is needed.
4. **Align when the task needs precise times.** Qwen3-ForcedAligner is a separate model. Its upstream contract supports inputs up to five minutes; chunk alignment accordingly. Text-generation deltas and coarse chunk extents are not word timestamps. Do not align a rewritten summary as if it were a literal transcript.
5. **Assign anonymous speaker turns unless the user opted out.** Keep a session-wide speaker space, or explicitly reconcile chunk-local embeddings before joining. Never concatenate `speaker_0` from unrelated chunks as the same person. Retain overlap and ambiguous attribution instead of forcing every sentence to one voice.
6. **Review the passages that matter.** Recheck names, numbers, decisions, commitments and unintelligible spans against audio. A second ASR engine supplies another hypothesis, not truth. Keep raw output and corrections separate; an agent's fluent rewrite is not acoustic evidence.
7. **Produce the requested result.** Deliver a source-linked transcript, meeting brief, decision history or follow-up list. Separate directly stated commitments from suggestions, and keep uncertainty visible at the affected passage.

For the sample, record elapsed time, peak process memory, memory pressure, completion coverage and repeated/omitted passages. If human annotations exist, use language-appropriate CER/WER with documented normalization, entity accuracy, speaker DER and timestamp error as appropriate. Do not call agreement with another machine transcript “accuracy,” or turn a synthetic smoke test into a real-meeting ranking.

## Speaker labels and real identities

Diarization answers which voice spoke when. Naming that voice requires additional evidence. Preserve existing confirmed Auday attribution, or ask the user to confirm a clean reference turn. Self-introductions and question/answer roles can suggest a candidate but do not establish a verified voice identity.

Cross-recording matching needs an explicitly scoped, user-approved enrollment/reference workflow and calibrated unknown/rejection handling. Voice similarity is not proof of identity. Do not create a persistent voiceprint collection as a side effect of ordinary meeting analysis.

Diarization also does not isolate simultaneous voices into separate audio tracks. Treat source separation as another optional task; do not promise recovery of words that are absent or masked in the source.
