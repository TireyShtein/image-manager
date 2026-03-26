import math
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QFrame,
    QLabel, QSpinBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt


def _format_eta(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds <= 0:
        return "estimating…"
    if seconds < 60:
        return "<1m"
    if seconds < 3600:
        return f"~{int(seconds // 60)}m"
    h, rem = divmod(int(seconds), 3600)
    return f"~{h}h {rem // 60}m"


class WD14FolderTagDialog(QDialog):
    """Pre-launch dialog for 'Tag All in Folder' — shows folder stats and lets the
    user choose how many images to process in this batch."""

    def __init__(self, folder: str, total: int, already_tagged: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tag All in Folder")
        self.setMinimumWidth(380)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        untagged = total - already_tagged

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        form.addRow("Folder:", QLabel(os.path.basename(folder) or folder))
        form.addRow("Total images:", QLabel(f"{total:,}"))
        form.addRow("Already tagged:", QLabel(f"{already_tagged:,}"))
        form.addRow("Untagged:", QLabel(f"{untagged:,}"))
        outer.addLayout(form)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        self._spinbox = QSpinBox()
        self._spinbox.setRange(1, untagged)
        self._spinbox.setValue(min(untagged, 10_000))
        self._spinbox.setSingleStep(1_000)
        self._spinbox.setGroupSeparatorShown(True)

        batch_form = QFormLayout()
        batch_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        batch_form.setHorizontalSpacing(12)
        batch_form.addRow("Images to tag in this batch:", self._spinbox)
        outer.addLayout(batch_form)

        self._estimate_label = QLabel()
        self._estimate_label.setStyleSheet("color: #aaa; font-size: 11px;")
        outer.addWidget(self._estimate_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._spinbox.valueChanged.connect(self._update_estimate)
        self._update_estimate(self._spinbox.value())

    def _update_estimate(self, value: int) -> None:
        eta = _format_eta(value * 2)
        self._estimate_label.setText(f"Estimated time: {eta}  (CPU, ~2s/image)")

    @property
    def batch_size(self) -> int:
        return self._spinbox.value()
