from transformers import pipeline
from PIL import Image

MODEL_ID = "Falconsai/nsfw_image_detection"
_pipe = None


def _get_pipe():
    global _pipe
    if _pipe is None:
        _pipe = pipeline("image-classification", model=MODEL_ID)
    return _pipe


def classify(image_path: str) -> tuple[str, float]:
    """Returns (label, confidence). Label is 'normal' or 'nsfw'."""
    pipe = _get_pipe()
    with Image.open(image_path).convert("RGB") as img:
        results = pipe(img)
    top = results[0]
    return top["label"], top["score"]
