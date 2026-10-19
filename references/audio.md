# Local audio analysis

Use Mac compute to explore recordings in more depth. Preserve the phone's existing work and create separate results.

## Capability boundaries

- **Transcription:** the included runner supports local Whisper.cpp model files.
- **Speaker diarization:** optional sherpa-onnx segmentation, embeddings, and clustering produce anonymous labels within one asset.
- **Identity:** map labels to names only using user-confirmed evidence. The same label in two files is not necessarily the same person. Do not overwrite existing confirmed phone-side attribution.
- **Source separation:** diarization does not separate overlapping voices into individual audio tracks. No source-separation model is included.

## Prepare

Fetch the selected asset with `auday.py audio <asset-id>` and retain its `.asset.json`. If the phone permits text only, do not bypass that scope. Stored audio may contain enhancements, missing intervals, or omitted silence; analysis cannot recover sound that no longer exists.

Use `doctor` to check `ffmpeg`, `whisper-cli`, and `uv`. The base transcription runner uses Python's standard library. Diarization dependencies are installed locally with:

```text
uv sync --project <skill-root>/scripts --frozen
```

Weights must already exist locally. The runner neither downloads models nor sends audio to a service. When obtaining new weights, use an approved source, pin a revision and hash, and honor the model's license and any required user acceptance. Do not silently download multiple large models.

Whisper large-v3 and large-v3-turbo are candidates for different quality/resource tradeoffs, not guaranteed upgrades for every recording. Evaluate a representative sample in the user's language before processing a large archive. Synthetic smoke-test success is not evidence of real-world language or far-field accuracy.

## Run

Transcription:

```text
python3 <skill-root>/scripts/refine_audio.py \
  --audio <local-audio> --model <local-ggml-model> \
  --output <new-private-output-directory> --language auto
```

With anonymous diarization:

```text
uv run --project <skill-root>/scripts --frozen python <skill-root>/scripts/refine_audio.py \
  --audio <local-audio> --model <local-ggml-model> \
  --output <new-private-output-directory> --language auto --diarize \
  --segmentation-model <local-pyannote-onnx> \
  --embedding-model <local-campplus-onnx>
```

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
