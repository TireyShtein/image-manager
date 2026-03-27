"""WD14 SwinV2-v3 tagger backed by ONNX Runtime.

Model: SmilingWolf/wd-swinv2-tagger-v3
Downloaded on first use via huggingface_hub; cached in ~/.cache/huggingface/
"""
from __future__ import annotations

import csv
import logging
import threading
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

GENERAL_THRESHOLD: float = 0.35
CHARACTER_THRESHOLD: float = 0.90

_REPO_ID = "SmilingWolf/wd-swinv2-tagger-v3"
_MODEL_FILE = "model.onnx"
_TAGS_FILE = "selected_tags.csv"
_INPUT_SIZE = 448

# selected_tags.csv category values
_CAT_RATING = 9
_CAT_GENERAL = 0
_CAT_CHARACTER = 4

# Module-level singletons (lazy-loaded on first classify() call)
_init_lock = threading.Lock()
_session = None
_tags: list[tuple[str, int]] | None = None  # [(name, category), ...]
_input_name: str | None = None
_active_provider: str = "CPUExecutionProvider"  # set from session.get_providers()[0] after init

_PROVIDER_LABELS: dict[str, str] = {
    "CUDAExecutionProvider": "CUDA",
    "DmlExecutionProvider":  "DirectML",
    "CPUExecutionProvider":  "CPU",
}


def get_active_provider() -> str:
    return _active_provider


def get_active_provider_label() -> str:
    return _PROVIDER_LABELS[_active_provider]


def _download_file(filename: str) -> Path:
    import os
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled
    try:
        # Try local cache first — avoids HF network round-trip and token warning
        return Path(hf_hub_download(repo_id=_REPO_ID, filename=filename,
                                    local_files_only=True))
    except LocalEntryNotFoundError:
        pass  # Not cached yet — fall through to download
    except OfflineModeIsEnabled:
        pass  # Same — fall through, will fail below with clear message
    try:
        return Path(hf_hub_download(repo_id=_REPO_ID, filename=filename))
    except OfflineModeIsEnabled:
        raise RuntimeError(
            f"Cannot download {filename!r}: HF_HUB_OFFLINE is set and the "
            f"file is not cached. Disable offline mode or run once online."
        )
    except LocalEntryNotFoundError:
        raise RuntimeError(
            f"Cannot download {filename!r}: not cached and network unreachable. "
            f"Connect to the internet and retry."
        )
    except Exception as exc:
        print(f"[WD14] Download error for {filename!r}: {exc}")
        traceback.print_exc()
        raise RuntimeError(f"Failed to download {filename!r}: {exc}") from exc


def _load_tags() -> list[tuple[str, int]]:
    tags_path = _download_file(_TAGS_FILE)
    with open(tags_path, newline="", encoding="utf-8") as f:
        return [(row["name"], int(row["category"])) for row in csv.DictReader(f)]


def _select_providers(ort) -> list[str]:
    """Return priority-ordered provider list based on what the installed package ships."""
    available = set(ort.get_available_providers())
    priority = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    chosen = [p for p in priority if p in available]
    return chosen or ["CPUExecutionProvider"]


def _get_session():
    global _session, _tags, _input_name, _active_provider
    if _session is not None:
        return _session, _tags, _input_name
    with _init_lock:
        if _session is not None:  # double-checked locking
            return _session, _tags, _input_name
        import onnxruntime as ort
        model_path = _download_file(_MODEL_FILE)
        tags = _load_tags()

        providers = _select_providers(ort)
        candidate = providers[0]  # best candidate (may silently fall back to CPU)

        opts = ort.SessionOptions()
        if candidate == "CPUExecutionProvider":
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 4
        else:
            # GPU path: keep CPU threads low; GPU handles the heavy ops
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        session = ort.InferenceSession(
            model_path,
            sess_options=opts,
            providers=providers,
        )
        input_name = session.get_inputs()[0].name

        # Use actual active provider — ORT may silently fall back if init fails (e.g. CUDA without toolkit)
        actual = session.get_providers()[0]
        if actual != candidate:
            logger.warning("WD14: requested %s not available, fell back to %s", candidate, actual)
            print(f"[WD14] Warning: {candidate} not available, using {actual}")
        else:
            print(f"[WD14] Using provider: {actual}")

        # Assign atomically — _session last so it acts as the ready guard
        _active_provider = actual
        _tags = tags
        _input_name = input_name
        _session = session
        print(f"[WD14] ONNX session loaded (input={input_name!r}, tags={len(tags)})")
        logger.info("WD14 ONNX session loaded (input=%r, provider=%s)", input_name, actual)
    return _session, _tags, _input_name


def _preprocess(image_path: str) -> np.ndarray:
    """Open image → white-padded square → 448×448 → float32 BGR NHWC (1,448,448,3)."""
    with Image.open(Path(image_path)) as raw:
        raw.load()  # force full decode; raises clearly on corrupt/truncated files
        if raw.mode in ("RGBA", "LA") or (raw.mode == "P" and "transparency" in raw.info):
            canvas = Image.new("RGBA", raw.size, (255, 255, 255, 255))
            canvas.alpha_composite(raw.convert("RGBA"))
            img = canvas.convert("RGB")
        else:
            img = raw.convert("RGB")
    # img is a standalone in-memory image; file handle is closed above

    w, h = img.size
    max_dim = max(w, h)
    padded = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
    padded.paste(img, ((max_dim - w) // 2, (max_dim - h) // 2))
    padded = padded.resize((_INPUT_SIZE, _INPUT_SIZE), Image.BICUBIC)

    # Contiguous BGR array — avoids an extra copy at ONNX Runtime inference time
    arr = np.ascontiguousarray(np.array(padded, dtype=np.float32)[:, :, ::-1])
    return arr[np.newaxis]  # (1, 448, 448, 3) NHWC


def _sigmoid(x: np.ndarray) -> np.ndarray:
    # Numerically stable: avoids overflow warnings on extreme logits
    return np.where(
        x >= 0,
        1.0 / (1.0 + np.exp(-x)),
        np.exp(x) / (1.0 + np.exp(x)),
    )


def _postprocess(
    probs: np.ndarray,
    tags: list[tuple[str, int]],
) -> list[tuple[str, float]]:
    """Threshold general/character tags, argmax for rating.
    Model already outputs sigmoid probabilities in [0, 1] — no sigmoid needed.
    """
    scores = probs[0]  # shape (N,)

    collected: list[tuple[str, float]] = []
    best_rating: tuple[str, float] | None = None

    for idx, (name, category) in enumerate(tags):
        score = float(scores[idx])
        if category == _CAT_RATING:
            if best_rating is None or score > best_rating[1]:
                best_rating = (f"rating:{name}", score)
        elif category == _CAT_GENERAL and score >= GENERAL_THRESHOLD:
            collected.append((name, score))
        elif category == _CAT_CHARACTER and score >= CHARACTER_THRESHOLD:
            collected.append((name, score))

    if best_rating is not None:
        collected.append(best_rating)

    collected.sort(key=lambda x: x[1], reverse=True)
    return collected


def load_model() -> None:
    """Pre-initialize the ONNX session. Call once before tagging to surface errors early."""
    _get_session()


def release_session() -> None:
    """Release the ONNX session and free model memory (~100 MB)."""
    global _session, _tags, _input_name, _active_provider
    with _init_lock:
        _session = None
        _tags = None
        _input_name = None
        _active_provider = "CPUExecutionProvider"


def classify(image_path: str) -> list[tuple[str, float]]:
    """Return [(tag, confidence), ...] sorted by confidence descending.

    Includes general content tags, character tags, and the top rating label.
    API is identical to the previous wdtagger-backed implementation.
    """
    session, tags, input_name = _get_session()
    tensor = _preprocess(image_path)
    outputs = session.run(None, {input_name: tensor})
    return _postprocess(outputs[0], tags)  # outputs[0] is already sigmoid probabilities


def get_all_tags() -> list[str]:
    """Return sorted list of all tag names from selected_tags.csv."""
    if _tags is not None:
        return sorted(name for name, _ in _tags)
    tags_path = _download_file(_TAGS_FILE)
    with open(tags_path, newline="", encoding="utf-8") as f:
        return sorted(row["name"] for row in csv.DictReader(f))
