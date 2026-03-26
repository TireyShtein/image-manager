import os
import sys
from PyQt6.QtCore import QSettings


def get_settings() -> QSettings:
    """Return a QSettings backed by an INI file next to the executable (dist)
    or the project root (dev). This makes settings portable — they travel with
    the app folder instead of being stored in the Windows registry."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: put the INI next to the .exe
        base_dir = os.path.dirname(sys.executable)
    else:
        # Dev: project root is two levels above src/utils/
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ini_path = os.path.join(base_dir, "ImageManager.ini")
    return QSettings(ini_path, QSettings.Format.IniFormat)
