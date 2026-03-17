from transformers import pipeline
from PIL import Image

MODEL_ID = "google/vit-base-patch16-224"
TOP_K = 3
_pipe = None


def _get_pipe():
    global _pipe
    if _pipe is None:
        _pipe = pipeline("image-classification", model=MODEL_ID, top_k=TOP_K)
    return _pipe


def classify(image_path: str) -> list[tuple[str, float]]:
    """Returns list of (label, confidence) for top-K predictions."""
    pipe = _get_pipe()
    with Image.open(image_path).convert("RGB") as img:
        results = pipe(img)
    # Clean up ImageNet label format: "281: tabby, tabby cat" -> "tabby cat"
    cleaned = []
    for r in results:
        label = r["label"]
        if ":" in label:
            label = label.split(":", 1)[1].strip()
        # Take the last part if comma-separated synonyms
        label = label.split(",")[0].strip()
        cleaned.append((label, r["score"]))
    return cleaned
