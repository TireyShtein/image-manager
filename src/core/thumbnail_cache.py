import os
import hashlib
from PIL import Image

THUMB_SIZE = (256, 256)
CACHE_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'ImageManager', 'thumbs')


def get_thumbnail_path(image_path: str) -> str:
    key = hashlib.md5(image_path.encode()).hexdigest()
    return os.path.join(CACHE_DIR, key[:2], key + '.jpg')


def get_or_create_thumbnail(image_path: str) -> str | None:
    thumb_path = get_thumbnail_path(image_path)
    if os.path.exists(thumb_path):
        return thumb_path
    return _generate_thumbnail(image_path, thumb_path)


def _generate_thumbnail(image_path: str, thumb_path: str) -> str | None:
    try:
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        with Image.open(image_path) as img:
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            rgb = img.convert('RGB')
            rgb.save(thumb_path, 'JPEG', quality=85)
        return thumb_path
    except Exception:
        return None


def delete_thumbnail(image_path: str):
    thumb_path = get_thumbnail_path(image_path)
    if os.path.exists(thumb_path):
        os.remove(thumb_path)
