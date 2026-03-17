import os
import hashlib
from PIL import Image, ImageDraw, ImageFont

THUMB_SIZE = (256, 256)
CACHE_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'ImageManager', 'thumbs')

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.wmv', '.flv', '.m4v'}


def get_thumbnail_path(image_path: str) -> str:
    key = hashlib.md5(image_path.encode()).hexdigest()
    return os.path.join(CACHE_DIR, key[:2], key + '.jpg')


def get_or_create_thumbnail(image_path: str) -> str | None:
    thumb_path = get_thumbnail_path(image_path)
    if os.path.exists(thumb_path):
        return thumb_path
    ext = os.path.splitext(image_path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return _generate_video_placeholder(image_path, thumb_path)
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


def _generate_video_placeholder(image_path: str, thumb_path: str) -> str | None:
    try:
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        img = Image.new('RGB', THUMB_SIZE, color=(30, 30, 30))
        draw = ImageDraw.Draw(img)
        # Draw a play triangle in the centre
        cx, cy = THUMB_SIZE[0] // 2, THUMB_SIZE[1] // 2
        size = 40
        triangle = [(cx - size, cy - size), (cx - size, cy + size), (cx + size, cy)]
        draw.polygon(triangle, fill=(180, 180, 180))
        # Draw extension label at the bottom
        ext = os.path.splitext(image_path)[1].upper()
        draw.text((8, THUMB_SIZE[1] - 20), ext, fill=(120, 120, 120))
        img.save(thumb_path, 'JPEG', quality=85)
        return thumb_path
    except Exception:
        return None


def delete_thumbnail(image_path: str):
    thumb_path = get_thumbnail_path(image_path)
    if os.path.exists(thumb_path):
        os.remove(thumb_path)
