from wdtagger import Tagger

GENERAL_THRESHOLD = 0.35
CHARACTER_THRESHOLD = 0.9
_tagger: Tagger | None = None


def _get_tagger() -> Tagger:
    global _tagger
    if _tagger is None:
        # Default model: SmilingWolf/wd-swinv2-tagger-v3
        _tagger = Tagger()
    return _tagger


def classify(image_path: str) -> list[tuple[str, float]]:
    """Returns [(tag, confidence), ...] sorted by confidence descending.

    Includes general content tags, character tags, and the top rating label.
    Thresholds are applied by wdtagger (general=0.35, character=0.9).
    """
    tagger = _get_tagger()
    result = tagger.tag(
        image_path,
        general_threshold=GENERAL_THRESHOLD,
        character_threshold=CHARACTER_THRESHOLD,
    )

    tags: list[tuple[str, float]] = []

    # general_tag_data and character_tag_data are dict[str, float]
    tags.extend(result.general_tag_data.items())
    tags.extend(result.character_tag_data.items())

    # rating is the top rating label string (e.g. "general", "sensitive", "explicit")
    if result.rating:
        tags.append((f"rating:{result.rating}", result.rating_data[result.rating]))

    tags.sort(key=lambda x: x[1], reverse=True)
    return tags
