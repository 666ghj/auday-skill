# Local audio analysis

Use local compute on Mac or Windows to explore recordings in more depth. Preserve the phone's existing work and create separate results.

Choose an engine using [language and dialect routing](audio-language-routing.md), which covers Japanese, Chinese, English, Korean, French, German and mixed-language recordings. For Mac-specific engines and resource planning, read [Choosing local audio models on Mac](mac-audio-models.md). The commands below describe the bundled Whisper.cpp/sherpa-onnx runner. `<auday>` means the platform-specific bootstrap prefix from [first-run setup](first-run.md); optional upstream engines need their own compatible local environment.

## Capability boundaries

- **Transcription:** the included runner supports local Whisper.cpp model files.
- **Speaker diarization:** enabled by default; sherpa-onnx segmentation, embeddings, and clustering produce anonymous labels within one asset. An explicit request such as “不要说话人识别” or “do not distinguish speakers” disables this stage with `--no-diarize`.
- **Identity:** map labels to names only using user-confirmed evidence. The same label in two files is not necessarily the same person. Do not overwrite existing confirmed phone-side attribution.
- **Source separation:** diarization does not separate overlapping voices into individual audio tracks. No source-separation model is included.

## Prepare

Fetch the selected asset with `<auday> audio <asset-id>` and retain its `.asset.json`. If the phone permits text only, do not bypass that scope. Stored audio may contain enhancements, missing intervals, or omitted silence; analysis cannot recover sound that no longer exists.

For a new computer, follow [first-run setup](first-run.md); do not require the user to install command-line tools or models manually. Prepare transcription only when the task needs it. The integrated route installs one selected Whisper model, the required runtime/audio conversion tools, and anonymous diarization by default:

```text
<auday> doctor
<auday> setup --audio --model small
```

`small` downloads the 190.09 MB multilingual Whisper small Q5_1 model; `turbo` selects the 574.04 MB large-v3-turbo Q5_0 model. Choose one based on the machine, actual speech, and task. Speaker weights add about 30 MB; runtime archives and Python dependencies are additional. Explain the selected downloads and show progress. Setup verifies hashes, saves paths, and tests a public sample; it does not establish transcription quality on the user's recording.

On Windows 10/11 x64, setup installs an official prebuilt Whisper.cpp CPU runtime, its private runtime DLLs, and FFmpeg. It requires neither a GPU nor MSVC/WSL installation. Start with `small` when memory or CPU turnaround matters. On Mac, the Whisper.cpp build needs Xcode Command Line Tools; complete that system setup if missing before downloading large models. Apple Silicon acceleration and Mac-specific engine guidance do not apply to Windows.

If the user declines speaker labels, use `setup --audio --model small --no-diarize` (or the selected `turbo` route). This skips speaker weights and packages. Keep that choice in the transcription command as well. Do not add an unwanted speaker installation merely to make default checks pass.

For an existing manually managed environment, `doctor` checks `ffmpeg`, `whisper-cli`, and `uv`. The low-level runner accepts explicit local model paths. Its default diarization dependencies can be prepared with `uv sync --project <skill-root>/scripts --frozen --extra diarization`; the no-diarization runner needs only Python's standard library in addition to the native audio tools.

The transcription runner does not download models or send audio to a service. Resolve missing assets through setup before inference. Other engines need their own pinned runtime/model installation and license handling; do not install every model mentioned in the guides.

Select a model for the actual spoken language and task; a working language-specific model may be preferable to a general model. Whisper large-v3 and large-v3-turbo offer different quality/resource tradeoffs. Known single-language audio can use an explicit `--language` hint (`ja`, `zh`, `en`, `ko`, `fr`, `de`); use `auto` and inspect detection for uncertain or mixed audio. Preserve the original language unless translation is requested. Check a representative sample when choosing a new route; synthetic smoke-test success is not real-world quality evidence.

## Run

After setup, use the saved configuration:

```text
<auday> transcribe --audio <local-audio> --output <new-private-output-directory> --language auto
```

If the user explicitly declines speaker labels:

```text
<auday> transcribe --audio <local-audio> --output <new-private-output-directory> --language auto --no-diarize
```

For manually configured tools, the low-level runner remains available. The following advanced examples use macOS shell syntax and an existing `uv`/Python installation; new Windows and Mac installations should use the bootstrap commands above. Default transcription with anonymous diarization:

```text
uv run --project <skill-root>/scripts --frozen --extra diarization python <skill-root>/scripts/refine_audio.py \
  --audio <local-audio> --model <local-ggml-model> \
  --output <new-private-output-directory> --language auto \
  --segmentation-model <local-pyannote-onnx> \
  --embedding-model <local-campplus-onnx>
```

The low-level speaker opt-out:

```text
python3 <skill-root>/scripts/refine_audio.py \
  --audio <local-audio> --model <local-ggml-model> \
  --output <new-private-output-directory> --language auto --no-diarize
```

The opt-out does not require diarization weights or Python packages. If the default route lacks these prerequisites, explain and resolve the selected setup within the task's authorization; do not silently switch to transcription only. The default separates anonymous voices, not real-world identities.

Pass `--speakers N` only when the user knows the count and wants to supply it. Automatic clustering can split or merge speakers. Never provide a hidden true count to make an evaluation look better.

Process individual assets, up to two hours each, sequentially. The runner rejects longer inputs rather than silently truncating them. Confirm suitability on a short representative asset first.

## Outputs

- `result.json`: source/model hashes, configuration, relative times, original timeline spans, speaker turns and attribution candidates, and elapsed time.
- `transcript.md`: readable timestamped output.
- `whisper.json` and `engine.log`: local raw output and diagnostics, potentially containing private text.
- `prepared.wav`: a derived 16 kHz mono input, never a replacement for the source.

The output directory must be new. A failed run retains intermediate files; use a new directory after fixing the cause. Do not overwrite prior evidence. Ambiguous or poorly covered segments remain `unknown` instead of being forcibly assigned.

Review names, timestamps, silent/noisy regions, repetitions, speaker changes, and source mappings. Preserve time gaps rather than mapping compressed file offsets directly onto wall-clock time. Report quality limits separately from successful execution.

Primary references: [Whisper.cpp](https://github.com/ggml-org/whisper.cpp), [sherpa-onnx speaker diarization](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html).
