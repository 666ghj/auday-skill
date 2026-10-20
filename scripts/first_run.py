"""First-use capability preparation without requiring an existing personal setup."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from progress import report
from setup_runtime import (
    command_line_tools_status, download_asset, file_hash, host_architecture,
    install_runtime, require_macos, runtime_asset,
)

MODEL_URL = (
    "https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/"
    "0f741b5a6585bd53aeb15cd1372c56f2a0f65e12/embeddinggemma-300M-Q8_0.gguf"
)
MODEL_BYTES = 333590944


def _profile(root, profile):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", profile):
        raise ValueError("invalid_profile_name")
    return Path(root).expanduser().resolve() / profile


def _load(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def _save(path, value):
    _save_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def _save_bytes(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def setup_status(root, profile="default"):
    root = Path(root).expanduser().resolve()
    selected = _profile(root, profile)
    state = _load(selected / "setup-state.json")
    try:
        require_macos("13.3")
        supported, unavailable_reason = True, None
    except ValueError as error:
        supported, unavailable_reason = False, str(error)
    config_path = selected / "semantic-config.json"
    config = _load(config_path)
    present = bool(config) and all(Path(config.get(key, "")).is_file()
                                   for key in ("modelPath", "serverPath"))
    verified = state.get("semanticVerification", {})
    verification_matches = bool(present and verified.get("configSHA256") == file_hash(config_path))
    integrity = False
    if verification_matches:
        from semantic_search import MODEL_SHA, _runtime_identity
        integrity = (file_hash(config["modelPath"]) == MODEL_SHA
                     and _runtime_identity(Path(config["serverPath"])) == config.get("runtimeSHA256"))
    semantic = {
        "configured": bool(config), "filesPresent": present,
        "verifiedBySetup": verification_matches, "currentFilesVerified": integrity,
        "verifiedAt": verified.get("at"),
        "choice": state.get("semanticChoice", "not_selected"),
        "modelDownloadBytes": MODEL_BYTES,
        "supported": supported, "unavailableReason": unavailable_reason,
        "runtimeArchiveBytes": runtime_asset("llama")["size"],
        "recommendedFreeDiskBytes": 1024**3,
    }
    audio = {"ready": False, "installation": "on_demand"}
    if (Path(__file__).parent / "audio_setup.py").is_file():
        from audio_setup import audio_status
        audio = audio_status(root)
    offer = supported and not present and semantic["choice"] == "not_selected"
    resume = supported and not integrity and semantic["choice"] in ("requested", "enabled")
    return {
        "ok": True, "core": {
            "pythonReady": True,
            "pythonManagedBySetup": Path(sys.executable).resolve().is_relative_to(root / "runtime/python"),
            "connectionToolchain": command_line_tools_status(),
        },
        "semantic": semantic, "audio": audio,
        "paired": (selected / "connection.json").is_file(),
        "libraryOnDisk": (selected / "library.sqlite").is_file(),
        "recommendSemantic": offer,
        "nextStep": "offer_semantic_now_or_later" if offer else
                    "resume_semantic_setup" if resume else "continue_the_users_task",
    }


def defer_semantic(root, profile="default"):
    path = _profile(root, profile) / "setup-state.json"
    state = _load(path)
    state["semanticChoice"] = "deferred"
    _save(path, state)
    return {"ok": True, "semanticChoice": "deferred", "automaticPrompts": False,
            "existingDataPreserved": True}


def _verify_semantic(config):
    from semantic_search import LocalEncoder, POLICY
    documents = [
        "Please bring a working prototype to the next design review. A slide presentation alone is not enough.",
        "录音和转写在设备上处理，私人谈话不会发给远程服务器。",
        "Water the tomatoes every morning and move the seedlings into sunlight.",
    ]
    queries = [
        ("下次评审要带可以实际操作的样品，不能只有幻灯片", 0),
        ("How do we keep confidential conversations off the cloud?", 1),
        ("What should I do to help the tomato plants grow?", 2),
    ]
    with LocalEncoder(config) as encoder:
        vectors = [
            encoder.encode(POLICY["documentFormat"].format(title="none", text=text))
            for text in documents
        ]
        for completed, (query, expected) in enumerate(queries, 1):
            vector = encoder.encode(POLICY["queryPrefix"] + query)
            scores = [sum(a * b for a, b in zip(vector, other)) for other in vectors]
            if max(range(len(scores)), key=scores.__getitem__) != expected:
                raise ValueError("semantic_setup_verification_failed")
            report("Verifying meaning-based retrieval", completed, len(queries), "checks")
    return {"checksPassed": len(queries), "syntheticDataOnly": True,
            "coverage": "small cross-language smoke test, not a general accuracy benchmark"}


def setup_semantic(root, profile="default"):
    from semantic_search import MODEL_SHA, configure
    require_macos("13.3")
    root = Path(root).expanduser().resolve()
    selected = _profile(root, profile)
    selected.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = selected / "setup-state.json"
    state = _load(state_path)
    if state.get("semanticChoice") != "enabled":
        state["semanticChoice"] = "requested"
        _save(state_path, state)
    report("Preparing local semantic search", force=True)
    server = install_runtime(root, "llama")
    model = download_asset(
        MODEL_URL, root / "models/embeddinggemma-300M-Q8_0.gguf", MODEL_SHA,
        MODEL_BYTES, label="Downloading semantic model",
    )
    config_path = selected / "semantic-config.json"
    previous = config_path.read_bytes() if config_path.is_file() else None
    try:
        configure(selected, model, server)
        verification = _verify_semantic(_load(config_path))
        path = selected / "setup-state.json"
        state = _load(path)
        state.update({
            "semanticChoice": "enabled",
            "semanticVerification": {
                "at": datetime.now(timezone.utc).isoformat(),
                "configSHA256": file_hash(config_path), **verification,
            },
        })
        _save(path, state)
    except BaseException:
        if previous is None:
            config_path.unlink(missing_ok=True)
        else:
            _save_bytes(config_path, previous)
        raise
    report("Semantic search setup verified", force=True)
    return {"ok": True, "semanticReady": True, "verification": verification,
            "modelPath": str(model), "runtimePath": str(server),
            "nextStep": "synchronize_phone_text_then_build_index",
            "phoneConnectedBySetup": False}


def recommended_audio_model():
    result = subprocess.run(
        ["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=10,
    )
    memory = int(result.stdout.strip()) if result.returncode == 0 else 0
    return "turbo" if memory >= 16 * 1024**3 else "small"
