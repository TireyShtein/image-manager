"""Dialog for reviewing and resolving duplicate images."""
from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from src.core import file_ops, thumbnail_cache


# ── Async thumbnail loader ────────────────────────────────────────────────────

class _ThumbSignals(QObject):
    ready = pyqtSignal(str, QPixmap)   # (image_path, pixmap)


class _ThumbLoader(QRunnable):
    def __init__(self, image_path: str):
        super().__init__()
        self.image_path = image_path
        self.signals = _ThumbSignals()

    def run(self):
        thumb_path = thumbnail_cache.get_or_create_thumbnail(self.image_path)
        if thumb_path:
            pix = QPixmap(thumb_path).scaled(
                160, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.signals.ready.emit(self.image_path, pix)


# ── State model ───────────────────────────────────────────────────────────────

@dataclass
class _CardState:
    image_id: int
    path: str
    widget: QFrame
    radio: QRadioButton
    thumb_label: QLabel


@dataclass
class _GroupState:
    content_hash: str
    cards: list[_CardState] = field(default_factory=list)
    button_group: QButtonGroup = field(default_factory=QButtonGroup)
    container: QWidget = None


# ── Dialog ────────────────────────────────────────────────────────────────────

class DuplicatesDialog(QDialog):
    duplicates_resolved = pyqtSignal(list)   # list[int] of deleted image_ids

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicate Images")
        self.resize(1000, 650)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._groups: list[_GroupState] = []
        # Maps image_path → thumb_label so async loader can update the right label
        self._thumb_labels: dict[str, QLabel] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._header_label = QLabel("")
        self._header_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(self._header_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_content = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(4)
        self._scroll_layout.addStretch()
        self._scroll.setWidget(self._scroll_content)
        layout.addWidget(self._scroll, 1)

        btn_bar = QHBoxLayout()
        self._btn_trash = QPushButton("Send to Trash")
        self._btn_delete = QPushButton("Delete Permanently")
        self._btn_delete.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        )
        self._lbl_status = QLabel("")
        self._lbl_status.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        btn_close = QPushButton("Close")
        self._btn_trash.clicked.connect(lambda: self._execute_delete(use_trash=True))
        self._btn_delete.clicked.connect(lambda: self._execute_delete(use_trash=False))
        btn_close.clicked.connect(self.close)
        btn_bar.addWidget(self._btn_trash)
        btn_bar.addWidget(self._btn_delete)
        btn_bar.addWidget(self._lbl_status)
        btn_bar.addWidget(btn_close)
        layout.addLayout(btn_bar)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_groups(self, groups: list[list[dict]]):
        """Replace dialog content with a new scan result."""
        self._groups.clear()
        self._thumb_labels.clear()

        # Remove all widgets from scroll layout except trailing stretch
        while self._scroll_layout.count() > 1:
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._btn_trash.setEnabled(bool(groups))
        self._btn_delete.setEnabled(bool(groups))

        if not groups:
            lbl = QLabel("No duplicate images found.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888; font-size: 13px; padding: 40px;")
            self._scroll_layout.insertWidget(0, lbl)
            self._header_label.setText("No duplicates found.")
            return

        for group_rows in groups:
            self._add_group_widget(group_rows)

        self._update_header()
        self._start_thumb_loaders()

    # ── Group / card builders ─────────────────────────────────────────────────

    def _add_group_widget(self, rows: list[dict]):
        state = _GroupState(content_hash=rows[0]["content_hash"])
        state.button_group = QButtonGroup(self)

        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(4, 4, 4, 4)
        outer_layout.setSpacing(4)

        # Group header row
        hdr = QHBoxLayout()
        group_num = len(self._groups) + 1
        lbl = QLabel(f"Group {group_num}  —  {len(rows)} identical images")
        lbl.setStyleSheet("font-weight: bold; color: #ccc;")
        btn_oldest = QPushButton("Keep Oldest")
        btn_newest = QPushButton("Keep Newest")
        btn_oldest.setFixedHeight(22)
        btn_newest.setFixedHeight(22)
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(btn_oldest)
        hdr.addWidget(btn_newest)
        outer_layout.addLayout(hdr)

        # Card strip
        cards_widget = QWidget()
        cards_layout = QHBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(8)
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        outer_layout.addWidget(cards_widget)

        for i, row in enumerate(rows):
            card_state = self._build_card(row, state.button_group, i)
            state.cards.append(card_state)
            cards_layout.addWidget(card_state.widget)

        cards_layout.addStretch()

        state.container = outer

        # Default: keep oldest (lowest id = first card, rows sorted by id ASC)
        state.button_group.button(0).setChecked(True)

        btn_oldest.clicked.connect(
            lambda _, s=state: s.button_group.button(0).setChecked(True)
        )
        btn_newest.clicked.connect(
            lambda _, s=state: s.button_group.button(len(s.cards) - 1).setChecked(True)
        )

        # idClicked fires once per selection change (idToggled fires twice)
        state.button_group.idClicked.connect(
            lambda _bid, s=state: self._update_card_highlight(s)
        )
        self._update_card_highlight(state)

        # Insert before trailing stretch, followed by a separator
        insert_pos = self._scroll_layout.count() - 1
        self._scroll_layout.insertWidget(insert_pos, outer)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(255,255,255,0.08);")
        self._scroll_layout.insertWidget(insert_pos + 1, sep)

        self._groups.append(state)

    def _build_card(self, row: dict, btn_group: QButtonGroup, btn_id: int) -> _CardState:
        card = QFrame()
        card.setFixedWidth(185)
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            "QFrame { background: #1e1e1e; border: 1px solid rgba(255,255,255,0.12);"
            " border-radius: 6px; }"
        )

        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        # Thumbnail placeholder (filled asynchronously)
        thumb_label = QLabel("Loading…")
        thumb_label.setFixedSize(160, 160)
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setStyleSheet(
            "background: #111; border-radius: 3px; color: #555; font-size: 11px;"
        )
        layout.addWidget(thumb_label)
        self._thumb_labels[row["path"]] = thumb_label

        # Filename
        fn_label = QLabel(row["filename"])
        fn_label.setWordWrap(True)
        fn_label.setMaximumHeight(36)
        fn_label.setStyleSheet("font-size: 11px; color: #ddd;")
        layout.addWidget(fn_label)

        # Path (truncated, full path in tooltip)
        path = row["path"]
        display_path = ("…" + path[-27:]) if len(path) > 29 else path
        path_label = QLabel(display_path)
        path_label.setToolTip(path)
        path_label.setStyleSheet("font-size: 10px; color: #666;")
        layout.addWidget(path_label)

        # File size
        size_bytes = row.get("file_size") or 0
        if size_bytes >= 1_048_576:
            size_str = f"{size_bytes / 1_048_576:.1f} MB"
        elif size_bytes >= 1024:
            size_str = f"{size_bytes / 1024:.0f} KB"
        else:
            size_str = f"{size_bytes} B" if size_bytes else "—"
        size_label = QLabel(size_str)
        size_label.setStyleSheet("font-size: 10px; color: #888;")
        layout.addWidget(size_label)

        # Dimensions
        w, h = row.get("width"), row.get("height")
        if w and h:
            dim_label = QLabel(f"{w} × {h}")
            dim_label.setStyleSheet("font-size: 10px; color: #888;")
            layout.addWidget(dim_label)

        # Radio button
        radio = QRadioButton("Keep this")
        btn_group.addButton(radio, btn_id)
        layout.addWidget(radio)

        return _CardState(
            image_id=row["id"],
            path=path,
            widget=card,
            radio=radio,
            thumb_label=thumb_label,
        )

    # ── Thumbnail async loading ───────────────────────────────────────────────

    def _start_thumb_loaders(self):
        pool = QThreadPool.globalInstance()
        for path in list(self._thumb_labels.keys()):
            loader = _ThumbLoader(path)
            loader.signals.ready.connect(self._on_thumb_ready)
            pool.start(loader)

    def _on_thumb_ready(self, image_path: str, pix: QPixmap):
        label = self._thumb_labels.get(image_path)
        if label and not pix.isNull():
            label.setPixmap(pix)
            label.setText("")

    # ── Highlight ─────────────────────────────────────────────────────────────

    def _update_card_highlight(self, state: _GroupState):
        kept_id = state.button_group.checkedId()
        for i, card_state in enumerate(state.cards):
            if i == kept_id:
                card_state.widget.setStyleSheet(
                    "QFrame { background: #1e2a40; border: 2px solid #5a8aff;"
                    " border-radius: 6px; }"
                )
            else:
                card_state.widget.setStyleSheet(
                    "QFrame { background: #1e1e1e;"
                    " border: 1px solid rgba(255,255,255,0.12);"
                    " border-radius: 6px; }"
                )

    # ── Header ────────────────────────────────────────────────────────────────

    def _update_header(self):
        total_images = sum(len(g.cards) for g in self._groups)
        total_to_delete = sum(len(g.cards) - 1 for g in self._groups)
        self._header_label.setText(
            f"Found {len(self._groups)} duplicate group(s)  ·  "
            f"{total_images} total images  ·  "
            f"{total_to_delete} image(s) marked for removal"
        )

    # ── Delete ────────────────────────────────────────────────────────────────

    def _execute_delete(self, use_trash: bool):
        # Collect non-kept image_ids across all groups
        to_delete: list[tuple[int, _GroupState]] = []
        for state in self._groups:
            kept_idx = state.button_group.checkedId()
            for i, card in enumerate(state.cards):
                if i != kept_idx:
                    to_delete.append((card.image_id, state))

        if not to_delete:
            QMessageBox.information(
                self, "Nothing to Delete",
                "All groups have a 'keep' selection — nothing to remove."
            )
            return

        action = "trash" if use_trash else "permanently delete"
        reply = QMessageBox.warning(
            self, "Confirm Delete",
            f"About to {action} {len(to_delete)} image(s).\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted: list[int] = []
        errors: list[str] = []

        for image_id, state in to_delete:
            try:
                file_ops.delete_image(image_id, use_trash=use_trash)
                deleted.append(image_id)
                # Remove card from state and UI immediately (prevents double-delete)
                card_state = next((c for c in state.cards if c.image_id == image_id), None)
                if card_state:
                    state.cards.remove(card_state)
                    self._thumb_labels.pop(card_state.path, None)
                    card_state.widget.deleteLater()
            except ValueError:
                # Already deleted externally (race with gallery) — treat as done
                deleted.append(image_id)
                card_state = next((c for c in state.cards if c.image_id == image_id), None)
                if card_state:
                    state.cards.remove(card_state)
                    self._thumb_labels.pop(card_state.path, None)
                    card_state.widget.deleteLater()
            except Exception as e:
                errors.append(f"ID {image_id}: {e}")

        # Remove groups that now have ≤1 card (duplicate resolved)
        for state in [s for s in self._groups if len(s.cards) <= 1]:
            idx = self._scroll_layout.indexOf(state.container)
            if idx >= 0:
                # Remove the separator that follows the group widget
                sep_item = self._scroll_layout.itemAt(idx + 1)
                if sep_item and sep_item.widget():
                    sep_item.widget().deleteLater()
                    self._scroll_layout.removeItem(sep_item)
                self._scroll_layout.removeWidget(state.container)
                state.container.deleteLater()
            self._groups.remove(state)

        if errors:
            QMessageBox.warning(self, "Delete Errors", "\n".join(errors))

        if deleted:
            self._lbl_status.setText(f"Removed {len(deleted)} image(s).")
            self.duplicates_resolved.emit(deleted)

        if self._groups:
            self._update_header()
        else:
            self._header_label.setText("All duplicates resolved.")
            self._btn_trash.setEnabled(False)
            self._btn_delete.setEnabled(False)
