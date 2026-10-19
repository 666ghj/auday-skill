# Choosing a transcription strategy by language

Reviewed 2026-10-20. This is a decision guide for an agent, not a fixed language-to-model dispatcher. Choose for the recording's language or dialect, acoustic conditions, task, available Mac resources and useful models already installed. The entries below suggest what to try first; they are not universal accuracy rankings.

## Starting candidates

| Recording | Starting candidates | When to choose or compare another route |
| --- | --- | --- |
| **Japanese** | For Japanese-dominant speech, consider **Kotoba-Whisper v2.0 GGML** through Whisper.cpp. Multilingual **Whisper large-v3 / large-v3-turbo** is a practical comparison or an existing-installation choice. | Prefer a multilingual candidate for substantial Japanese/English mixing. For constrained CPU/memory budgets, investigate **ReazonSpeech K2** with its exact checkpoint/runtime. Check names, kanji readings, numbers and short replies. |
| **Mandarin Chinese** | **Qwen3-ASR-1.7B through MLX** is a strong first candidate; **Whisper large-v3 / turbo** provides a reusable alternative. | Use 0.6B or a suitable smaller installed model when resources matter more. Compare specialist terms, proper nouns and embedded English when they drive the task. |
| **Chinese dialects / Cantonese** | Prioritize **Qwen3-ASR** when its documented coverage includes the actual dialect. | Check the specific regional speech rather than treating every accent as the same dialect. Keep a multilingual Whisper comparison when the existing route works or the sample exposes omissions. Verify the MLX runtime's supported language arguments separately from the original model's coverage. |
| **English** | **Parakeet TDT v3 through a Mac runtime such as FluidAudio** is a throughput-oriented candidate; **Whisper turbo** is a convenient general route. | Prefer multilingual candidates when other languages occur. For difficult accents, noise or terminology, compare large-v3 or Qwen3-ASR on the affected passages rather than assuming a faster model is more accurate. |
| **Korean** | **Whisper large-v3 / turbo** is a practical starting route; **Qwen3-ASR-1.7B** is a multilingual alternative. | Check names, loanwords, spacing and code-switching. Choose from the same-audio comparison or an already validated installation; do not infer a Korean winner from an aggregate multilingual score. |
| **French** | **Whisper large-v3 / turbo** for general recordings; **Parakeet TDT v3** when supported-language throughput is the priority. | Compare Qwen3-ASR or the other baseline when regional accents, names, numbers or mixed-language speech are problematic. Preserve diacritics in the delivered transcript. |
| **German** | **Whisper large-v3 / turbo** for general recordings; **Parakeet TDT v3** for a throughput-oriented route. | Check compounds, names, numbers, regional speech and English terms. Consider Qwen3-ASR where the sample supports it; do not import a French/English quality result as German evidence. |
| **Other or uncertain languages** | Check the exact model's language list. A multilingual Whisper checkpoint is a broad-coverage starting point; Qwen3-ASR is another option within its documented coverage. | Add a language-specific candidate only when there is a concrete need and a usable local runtime. Unsupported languages and very low-quality audio may remain unresolved. |

Within the Whisper family, turbo favors turnaround; large-v3 is a useful quality-oriented comparison. An existing satisfactory engine need not be replaced for every task. A new language, poor sample, changed resource budget or demanding output is a reason to reconsider.

## What supports these choices

- **Qwen:** the official project documents 30 languages and 22 Chinese dialects, with dialect evaluations. That supports giving it priority for covered Chinese regional speech. The published evaluation and the exact local quantized build remain different evidence; neither proves superiority on every user's dialect. See [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR).
- **Japanese specialists:** Kotoba's authors publish a Japanese-distilled model and [their own GGML conversion](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-ggml), including Mac benchmarks. This is a concrete Whisper.cpp route, not a claimed local Auday benchmark. [Kotoba v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0) and [v2.2](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2) are different packaging choices: v2.2 adds a Transformers diarization/punctuation pipeline to the v2.0 base; it is not simply a newer GGML file. [ReazonSpeech K2](https://github.com/reazon-research/ReazonSpeech) is another Japanese-focused sherpa-onnx option; its Japanese/English development checkpoint must be evaluated separately.
- **English/French/German throughput:** [Parakeet TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) lists those languages among its 25 European languages. Use a compatible [FluidAudio](https://github.com/FluidInference/FluidAudio) or other verified Mac implementation; CUDA examples and GPU-server speed claims do not establish Mac results. This checkpoint does not cover Japanese, Chinese or Korean.
- **Broad coverage:** [Whisper's model guidance](https://github.com/openai/whisper) describes multilingual checkpoints, turbo's speed/quality tradeoff and varying quality across languages. Its `.en` variants are not the general multilingual choice. Check [supported language codes](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py); those labels do not promise equal accuracy.

## Choose for the actual recording

Use Auday metadata and the recording to identify language; the user's nationality, chat language, sales region or Mac UI locale is not sufficient. Known single-language audio can use an explicit language hint. Unknown or mixed-language audio should begin with detection and spot-checking. If a long recording changes languages, preserve offsets when processing language-homogeneous regions; never assume one detected language describes the entire file.

The bundled Whisper runner accepts `--language ja`, `zh`, `en`, `ko`, `fr`, `de`, or `auto` as appropriate. Other engines may expect full language names or different codes. Do not invent a `--language` value for every regional dialect. Native Japanese-only or English-only models are unsuitable when preserving substantial speech in other languages is required.

Preserve the spoken language in the source transcript. Translation and the language of the final brief are separate user choices. Do not silently translate Japanese or Korean speech to English or Chinese. Whisper turbo is not the choice for Whisper's speech-to-English translation task; see the upstream model guidance before using that separate capability.

## Adapt without making every task a benchmark

Reuse a known-good local setup for routine work. When selection is needed, compare one relevant challenger with the working baseline on a short representative sample, then process with the chosen route. A roughly 3–5 minute sample often works; its coverage matters more than a fixed duration. Do not install every model in the table or repeatedly compare unchanged setups.

Check the properties relevant to the language and the task: named entities, numbers, omissions, repetition, language switches, speaker changes, useful timestamps, elapsed time and memory pressure. For Japanese/Chinese use a documented CER normalization when quantitative evaluation is warranted; Korean and space-delimited languages also require explicit segmentation/spacing rules. Keep accents, diacritics and original text in delivered evidence. A translation score or another model's transcript is not an ASR gold standard.

Speaker diarization is an independent stage chosen for overlap, speaker count, microphones and resources. It need not change merely because transcription switches languages, but its quality still needs to fit the recording. Preserve `unknown` attribution and source timing. See [audio models and meeting workflow](mac-audio-models.md) for model setup, identity handling and output requirements.

Briefly explain the chosen route when it helps: for example, Japanese-focused Kotoba with the existing Whisper.cpp installation; Qwen for a documented Chinese dialect; or Parakeet for a large French archive. Record the working model revision, settings and sample result with the task so the next run can reuse them.
