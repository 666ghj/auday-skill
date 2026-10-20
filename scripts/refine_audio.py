#!/usr/bin/env python3
"""Offline Whisper.cpp transcription with anonymous diarization enabled by default.
Weights must already exist locally. Results are derivatives; no phone write-back.
"""

import argparse, hashlib, json, math, os, shutil, subprocess, sys, time, wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from progress import report


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def attach_speakers(segments, turns):
    for segment in segments:
        duration = segment["end"] - segment["start"]
        scores = {}
        for turn in turns:
            overlap = max(
                0,
                min(segment["end"], turn["end"]) - max(segment["start"], turn["start"]),
            )
            if overlap:
                scores[turn["speaker"]] = (
                    scores.get(turn["speaker"], 0) + overlap / duration
                )
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        segment["speakerCandidates"] = [
            {"label": key, "coverage": round(min(value, 1), 4)} for key, value in ranked
        ]
        segment["speaker"] = "unknown"
        if ranked and ranked[0][1] >= 0.65 and (len(ranked) < 2 or ranked[1][1] < 0.15):
            segment["speaker"] = ranked[0][0]
        segment["speakerAttribution"] = (
            "single_dominant_turn"
            if segment["speaker"] != "unknown"
            else "ambiguous_or_uncovered"
        )
    return segments


def source_spans(start, end, timeline):
    spans = []
    for span in timeline:
        left = max(start, float(span["fileStart"]))
        right = min(end, float(span["fileEnd"]))
        if right <= left:
            continue
        origin = datetime.fromisoformat(span["recordedStart"].replace("Z", "+00:00"))
        if origin.tzinfo is None:
            raise ValueError("timeline_timezone_required")
        spans.append(
            {
                "start": (
                    origin + timedelta(seconds=left - span["fileStart"])
                ).isoformat(),
                "end": (
                    origin + timedelta(seconds=right - span["fileStart"])
                ).isoformat(),
            }
        )
    return spans


def run(args):
    os.umask(0o077)
    report("Preparing local audio analysis", force=True)
    audio = args.audio.expanduser().resolve()
    model = args.model.expanduser().resolve()
    out = args.output.expanduser().resolve()
    if not audio.is_file() or not model.is_file():
        raise ValueError("audio_and_local_model_required; no automatic downloads")
    if args.diarize:
        if not args.segmentation_model or not args.embedding_model:
            raise ValueError(
                "diarization_enabled_by_default: provide --segmentation-model and "
                "--embedding-model; use --no-diarize when speaker labels are unwanted"
            )
        segmentation = args.segmentation_model.expanduser().resolve()
        embedding = args.embedding_model.expanduser().resolve()
        if not segmentation.is_file() or not embedding.is_file():
            raise ValueError("local_diarization_weights_missing")
        import sherpa_onnx
        import soundfile as sf
    if args.speakers is not None and args.speakers < 1:
        raise ValueError("speaker_count_must_be_positive")
    if not 1 <= args.threads <= 32:
        raise ValueError("invalid_thread_count")
    if not shutil.which("ffmpeg") or not shutil.which("whisper-cli"):
        raise ValueError("ffmpeg_and_whisper_cli_required")
    out.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    fingerprint = digest(audio)
    asset_path = args.asset_metadata or audio.with_suffix(".asset.json")
    asset = json.loads(asset_path.read_text()) if asset_path.is_file() else {}
    if asset and asset.get("sha256") != fingerprint:
        raise ValueError("asset_source_hash_mismatch")
    wav = out / "prepared.wav"
    report("Preparing 16 kHz mono audio")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(audio),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        check=True,
    )
    with wave.open(str(wav)) as inp:
        duration = inp.getnframes() / inp.getframerate()
    if duration <= 0 or duration > 7200:
        raise ValueError(
            "use_individual_assets_up_to_two_hours; never silently truncate"
        )
    report("Audio preparation complete")
    report("Transcribing audio with Whisper.cpp")
    with (out / "engine.log").open("w") as log:
        subprocess.run(
            [
                "whisper-cli",
                "-m",
                str(model),
                "-f",
                str(wav),
                "-l",
                args.language,
                "-t",
                str(args.threads),
                "-ojf",
                "-of",
                str(out / "whisper"),
                "-np",
            ],
            stdout=log,
            stderr=log,
            check=True,
        )
    raw = json.loads((out / "whisper.json").read_text())
    segments = []
    for index, row in enumerate(raw.get("transcription", [])):
        left = float(row["offsets"]["from"]) / 1000
        right = float(row["offsets"]["to"]) / 1000
        if (
            not math.isfinite(left + right)
            or right <= left
            or left < 0
            or right > duration + 0.5
        ):
            raise ValueError("invalid_asr_timestamps")
        right = min(right, duration)
        if right <= left:
            continue
        segments.append(
            {
                "id": f"{fingerprint[:16]}-refined-{index:05d}",
                "start": left,
                "end": right,
                "text": row["text"].strip(),
                "sourceSpans": source_spans(left, right, asset.get("timeline", [])),
                "speaker": "unknown",
            }
        )
    report("Transcription complete")
    turns = []
    diarization = {"status": "not_requested"}
    if args.diarize:
        report("Identifying anonymous speaker turns")
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(segmentation)
                ),
                num_threads=args.threads,
                provider="cpu",
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(embedding), num_threads=args.threads, provider="cpu"
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=args.speakers or -1, threshold=0.5
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise ValueError("invalid_diarization_config")
        engine = sherpa_onnx.OfflineSpeakerDiarization(config)
        samples, rate = sf.read(wav, dtype="float32")
        if rate != engine.sample_rate:
            raise ValueError("diarization_sample_rate_mismatch")
        mapping = {}
        for row in engine.process(samples).sort_by_start_time():
            left = max(0, float(row.start))
            right = min(duration, float(row.end))
            if not math.isfinite(left + right) or right <= left:
                continue
            label = mapping.setdefault(row.speaker, f"speaker_{len(mapping) + 1:02d}")
            turns.append({"start": left, "end": right, "speaker": label})
        attach_speakers(segments, turns)
        diarization = {
            "status": "completed",
            "engine": "sherpa-onnx",
            "version": sherpa_onnx.__version__,
            "segmentationSHA256": digest(segmentation),
            "embeddingSHA256": digest(embedding),
            "speakerCount": len(mapping),
            "requestedSpeakerCount": args.speakers,
            "identityScope": "anonymous-within-this-asset",
            "turns": turns,
        }
        report("Speaker diarization complete")
    if digest(audio) != fingerprint:
        raise ValueError("source_audio_changed_during_job")
    report("Saving local analysis results")
    result = {
        "schemaVersion": 1,
        "kind": "derived_transcript",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "audioSHA256": fingerprint,
            "assetID": asset.get("id"),
            "timeline": asset.get("timeline", []),
            "durationSeconds": duration,
        },
        "engine": {
            "name": "whisper.cpp",
            "modelFile": model.name,
            "modelSHA256": digest(model),
            "language": args.language,
        },
        "diarization": diarization,
        "segments": segments,
        "elapsedSeconds": time.perf_counter() - started,
        "notes": [
            "Original audio and phone transcript unchanged.",
            "Speaker labels do not establish identity or separate overlapping voices.",
            "Model output is an alternative transcript, not verified ground truth.",
            "Timestamps are model estimates.",
        ],
    }
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# Auday · Local audio analysis",
        "",
        f"Source SHA-256: {fingerprint}",
        f"Model: {model.name}",
        "",
        "Derived result. Original phone records are unchanged. Speaker labels apply only within this asset.",
        "",
    ]
    for row in segments:
        stamp = f"{int(row['start'] // 60):02d}:{row['start'] % 60:05.2f}"
        speaker = f"{row['speaker']}：" if args.diarize else ""
        lines.append(f"- [{stamp}] {speaker}{row['text']}")
    (out / "transcript.md").write_text("\n".join(lines) + "\n")
    report("Local audio analysis complete")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(out),
                "segments": len(segments),
                "speakerCount": diarization.get("speakerCount"),
                "elapsedSeconds": result["elapsedSeconds"],
                "audioSHA256": fingerprint,
            }
        )
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audio", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--language", default="auto")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--asset-metadata", type=Path)
    p.add_argument(
        "--diarize",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include anonymous speaker labels by default; use --no-diarize for transcription only",
    )
    p.add_argument("--segmentation-model", type=Path)
    p.add_argument("--embedding-model", type=Path)
    p.add_argument("--speakers", type=int)
    args = p.parse_args()
    try:
        run(args)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
