from typing import NamedTuple
from PyQt6.QtGui import QPixmap, QColor

# Maximum size stored in the thumbnail cache on disk
CACHE_THUMB_SIZE = 256

# Prefetch / eviction margins (in rows)
_PREFETCH_MARGIN = 40
_EVICT_MARGIN = 80

# Size tiers: (max_count, thumb_px)
_SIZE_TIERS = [
    (4,    300),
    (12,   240),
    (30,   180),
    (80,   140),
    (200,  110),
    (500,   85),
    (700,   78),
]
_MIN_THUMB_SIZE = 70


class _DensityConfig(NamedTuple):
    factor: float
    spacing: int


_DENSITY_CONFIG: dict[str, _DensityConfig] = {
    "compact":     _DensityConfig(factor=0.65, spacing=4),
    "comfortable": _DensityConfig(factor=1.0,  spacing=8),
    "spacious":    _DensityConfig(factor=1.40, spacing=12),
}

# Pagination
PAGE_SIZE = 200

# Drag-and-drop MIME type for image ID payloads
_MIME_IMAGE_IDS = "application/x-imagemanager-ids"


def _compute_thumb_size(count: int) -> int:
    for max_count, size in _SIZE_TIERS:
        if count <= max_count:
            return size
    return _MIN_THUMB_SIZE


# Reusable grey placeholder, created lazily
_placeholder_cache: dict[int, QPixmap] = {}


def _get_placeholder(size: int) -> QPixmap:
    if size not in _placeholder_cache:
        pix = QPixmap(size, size)
        pix.fill(QColor(220, 220, 220))
        _placeholder_cache[size] = pix
    return _placeholder_cache[size]
