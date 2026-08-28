"""The message viewer: header block, HTML/text body, attachment bar.

Rendering uses :class:`QTextBrowser` (no Chromium) so the app stays small and
starts instantly. Remote content (tracking pixels, external images) is blocked
until the user opts in per message; inline ``cid:`` images always resolve from
the message's own attachments.
"""

from __future__ import annotations

import base64
import html as _html
import re
from typing import Iterable

from PySide6.QtCore import QByteArray, QEvent, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from parsers.models import Attachment, EmailMessage
from utils.helpers import human_size, open_with_os, write_temp_attachment


# --------------------------------------------------------------------------- #
# FlowLayout - wraps attachment chips onto multiple rows (Qt docs example port)
# --------------------------------------------------------------------------- #
class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, margin: int = 4, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing

    def __del__(self) -> None:  # pragma: no cover
        while self._items:
            self._items.pop()

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_height = 0
        right = rect.right() - m.right()

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > right and line_height > 0:
                x = rect.x() + m.left()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


# --------------------------------------------------------------------------- #
# Remote-content-blocking browser
# --------------------------------------------------------------------------- #
class RemoteBlockingBrowser(QTextBrowser):
    """A ``QTextBrowser`` that serves ``cid:`` images from the current message and
    refuses ``http(s)`` resources until :attr:`allow_remote` is set."""

    remoteContentBlocked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.allow_remote = False
        self._inline: dict[str, Attachment] = {}
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_anchor)

    def set_inline_resources(self, by_cid: dict[str, Attachment]) -> None:
        self._inline = by_cid or {}

    def _on_anchor(self, url: QUrl) -> None:
        # Never navigate the browser itself; open real links in the OS browser.
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)

    def loadResource(self, resource_type: int, url: QUrl):  # noqa: N802
        scheme = url.scheme().lower()

        if scheme == "cid":
            key = url.path() or url.toString()[4:]
            key = key.strip("<>").strip()
            att = self._inline.get(key)
            if att is None:
                # Some messages reference "cid:foo" but store "foo@host".
                for cid, candidate in self._inline.items():
                    if cid.split("@", 1)[0] == key.split("@", 1)[0]:
                        att = candidate
                        break
            if att is not None:
                return QByteArray(att.data)
            return QByteArray()

        if scheme in ("http", "https"):
            if self.allow_remote:
                return super().loadResource(resource_type, url)
            self.remoteContentBlocked.emit()
            return QByteArray()

        if scheme in ("", "file", "data", "qrc"):
            return super().loadResource(resource_type, url)

        return QByteArray()


# --------------------------------------------------------------------------- #
# Attachment chip
# --------------------------------------------------------------------------- #
class AttachmentChip(QPushButton):
    def __init__(self, attachment: Attachment, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._att = attachment
        self.setText(f"  {attachment.filename}  ·  {human_size(attachment.size)}")
        self.setToolTip(f"{attachment.filename}\n{attachment.mime_type} — {human_size(attachment.size)}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.clicked.connect(self.open_with_os)

    # -- actions --------------------------------------------------------- #
    def open_with_os(self) -> None:
        try:
            path = write_temp_attachment(self._att.filename, self._att.data)
        except OSError as exc:
            QMessageBox.warning(self, "Attachment", f"Could not write a temporary copy:\n{exc}")
            return
        if not open_with_os(path):
            QMessageBox.information(
                self,
                "Attachment",
                f"No application is registered to open this file.\nA copy was saved to:\n{path}",
            )

    def save_as(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "Save attachment", self._att.filename)
        if not target:
            return
        try:
            with open(target, "wb") as fh:
                fh.write(self._att.data)
        except OSError as exc:
            QMessageBox.warning(self, "Attachment", f"Could not save:\n{exc}")

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        act_open = menu.addAction("Open")
        act_save = menu.addAction("Save As…")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act_open:
            self.open_with_os()
        elif chosen == act_save:
            self.save_as()


# --------------------------------------------------------------------------- #
# Viewer widget
# --------------------------------------------------------------------------- #
class ViewerWidget(QWidget):
    """Renders one :class:`EmailMessage`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._message: EmailMessage | None = None
        self._had_remote = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_remote_banner())
        self.browser = RemoteBlockingBrowser(self)
        self.browser.remoteContentBlocked.connect(self._on_remote_blocked)
        root.addWidget(self._build_find_bar())
        root.addWidget(self.browser, 1)
        root.addWidget(self._build_attachment_bar())

        self.clear()

    # -- construction -------------------------------------------------- #
    def _build_header(self) -> QWidget:
        box = QFrame()
        box.setObjectName("HeaderBox")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)

        self.lbl_subject = QLabel()
        f = self.lbl_subject.font()
        f.setPointSizeF(f.pointSizeF() + 2)
        f.setBold(True)
        self.lbl_subject.setFont(f)
        self.lbl_subject.setWordWrap(True)
        self.lbl_subject.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.lbl_meta = QLabel()
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        lay.addWidget(self.lbl_subject)
        lay.addWidget(self.lbl_meta)
        return box

    def _build_remote_banner(self) -> QWidget:
        self.remote_banner = QFrame()
        self.remote_banner.setObjectName("RemoteBanner")
        lay = QHBoxLayout(self.remote_banner)
        lay.setContentsMargins(10, 6, 10, 6)
        msg = QLabel("This message contains remote content that was blocked to protect your privacy.")
        msg.setWordWrap(True)
        btn = QPushButton("Load remote content")
        btn.clicked.connect(self._load_remote)
        lay.addWidget(msg, 1)
        lay.addWidget(btn)
        self.remote_banner.hide()
        return self.remote_banner

    def _build_find_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("FindBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(4)

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in message…")
        self.find_input.setClearButtonEnabled(True)
        self.find_input.textChanged.connect(lambda: self._find(forward=True, incremental=True))
        self.find_input.returnPressed.connect(lambda: self._find(forward=True))
        self.find_input.installEventFilter(self)

        self._find_status = QLabel("")
        self._find_status.setMinimumWidth(72)

        btn_prev = QToolButton()
        btn_prev.setText("▲")
        btn_prev.setAutoRaise(True)
        btn_prev.setToolTip("Previous match")
        btn_prev.clicked.connect(lambda: self._find(forward=False))
        btn_next = QToolButton()
        btn_next.setText("▼")
        btn_next.setAutoRaise(True)
        btn_next.setToolTip("Next match")
        btn_next.clicked.connect(lambda: self._find(forward=True))
        btn_close = QToolButton()
        btn_close.setText("✕")
        btn_close.setAutoRaise(True)
        btn_close.setToolTip("Close (Esc)")
        btn_close.clicked.connect(self.close_find)

        lay.addWidget(self.find_input, 1)
        lay.addWidget(self._find_status)
        lay.addWidget(btn_prev)
        lay.addWidget(btn_next)
        lay.addWidget(btn_close)

        bar.hide()
        self.find_bar = bar
        return bar

    def _build_attachment_bar(self) -> QWidget:
        self.attach_area = QScrollArea()
        self.attach_area.setObjectName("AttachmentBar")
        self.attach_area.setWidgetResizable(True)
        self.attach_area.setFrameShape(QFrame.Shape.NoFrame)
        self.attach_area.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.attach_area.setMaximumHeight(96)

        self._attach_host = QWidget()
        self._attach_layout = FlowLayout(self._attach_host)
        self.attach_area.setWidget(self._attach_host)
        self.attach_area.hide()
        return self.attach_area

    # -- public API -------------------------------------------------- #
    def clear(self) -> None:
        self._message = None
        self.lbl_subject.setText("")
        self.lbl_meta.setText("")
        self.browser.setHtml(
            "<div style='color:gray;padding:24px;font-family:sans-serif'>"
            "Select a message to read it here.</div>"
        )
        self.remote_banner.hide()
        self.close_find()
        self._clear_attachments()

    def set_message(self, message: EmailMessage) -> None:
        self._message = message
        self._had_remote = False
        self.browser.allow_remote = False
        self.remote_banner.hide()
        self.close_find()

        self.lbl_subject.setText(_esc(message.display_name))
        self.lbl_meta.setText(self._meta_html(message))

        self.browser.set_inline_resources(message.inline_by_cid)
        self._render_body(message)
        self._populate_attachments(message.visible_attachments)

    # -- rendering -------------------------------------------------- #
    @staticmethod
    def _meta_html(m: EmailMessage) -> str:
        rows: list[str] = []
        if m.sender:
            rows.append(f"<b>From:</b> {_esc(m.sender)}")
        if m.to:
            rows.append(f"<b>To:</b> {_esc(', '.join(m.to))}")
        if m.cc:
            rows.append(f"<b>Cc:</b> {_esc(', '.join(m.cc))}")
        if m.date:
            rows.append(f"<b>Date:</b> {_esc(m.date.strftime('%Y-%m-%d %H:%M'))}")
        if m.folder_path:
            rows.append(f"<b>Folder:</b> {_esc(m.folder_path)}")
        return "<br>".join(rows)

    def _render_body(self, m: EmailMessage) -> None:
        if m.body_html:
            # Bake embedded images straight into the HTML as data: URIs. This is
            # far more reliable across QTextBrowser versions than resolving
            # "cid:" through loadResource(), and it also covers images referenced
            # from CSS (background / url()).
            html = _inline_cid_images(m.body_html, m.inline_by_cid)
            self.browser.setHtml(html)
        elif m.body_text:
            self.browser.setHtml(
                "<pre style='white-space:pre-wrap;word-wrap:break-word;"
                "font-family:sans-serif;padding:12px'>" + _esc(m.body_text) + "</pre>"
            )
        else:
            self.browser.setHtml(
                "<div style='color:gray;padding:24px;font-family:sans-serif'>"
                "(This message has no readable body.)</div>"
            )

    # -- remote content ------------------------------------------- #
    def _on_remote_blocked(self) -> None:
        self._had_remote = True
        if self._message is not None and not self.browser.allow_remote:
            self.remote_banner.show()

    def _load_remote(self) -> None:
        self.browser.allow_remote = True
        self.remote_banner.hide()
        if self._message is not None:
            self._render_body(self._message)

    # -- find in message ---------------------------------------- #
    def open_find(self) -> None:
        self.find_bar.show()
        self.find_input.setFocus()
        self.find_input.selectAll()

    def close_find(self) -> None:
        self.find_bar.hide()
        self._find_status.setText("")
        cursor = self.browser.textCursor()
        cursor.clearSelection()
        self.browser.setTextCursor(cursor)

    def _find(self, *, forward: bool = True, incremental: bool = False) -> None:
        text = self.find_input.text()
        if not text:
            self._find_status.setText("")
            cursor = self.browser.textCursor()
            cursor.clearSelection()
            self.browser.setTextCursor(cursor)
            return

        flags = QTextDocument.FindFlag(0)
        if not forward:
            flags |= QTextDocument.FindFlag.FindBackward
        if incremental:
            # Re-search from the start of the current match so each keystroke
            # keeps extending the same hit instead of skipping ahead.
            cursor = self.browser.textCursor()
            cursor.setPosition(cursor.selectionStart())
            self.browser.setTextCursor(cursor)

        found = self.browser.find(text, flags)
        if not found:
            cursor = self.browser.textCursor()
            cursor.movePosition(
                QTextCursor.MoveOperation.Start if forward else QTextCursor.MoveOperation.End
            )
            self.browser.setTextCursor(cursor)
            found = self.browser.find(text, flags)
        self._find_status.setText("" if found else "No matches")

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.find_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close_find()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self._find(forward=False)
                return True
        return super().eventFilter(obj, event)

    # -- attachments -------------------------------------------- #
    def _clear_attachments(self) -> None:
        while self._attach_layout.count():
            item = self._attach_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        self.attach_area.hide()

    def _populate_attachments(self, attachments: Iterable[Attachment]) -> None:
        self._clear_attachments()
        atts = list(attachments)
        if not atts:
            return
        for att in atts:
            self._attach_layout.addWidget(AttachmentChip(att, self._attach_host))
        self.attach_area.show()

    # -- misc ------------------------------------------------- #
    def save_all_attachments(self) -> None:
        if not self._message or not self._message.visible_attachments:
            QMessageBox.information(self, "Attachments", "This message has no attachments.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Save all attachments to…")
        if not folder:
            return
        from pathlib import Path

        from utils.helpers import safe_filename

        saved = 0
        for att in self._message.visible_attachments:
            try:
                (Path(folder) / safe_filename(att.filename)).write_bytes(att.data)
                saved += 1
            except OSError:
                continue
        QMessageBox.information(self, "Attachments", f"Saved {saved} attachment(s) to:\n{folder}")


def _esc(text: str) -> str:
    return _html.escape(text or "")


# Matches every "cid:<token>" occurrence - in <img src=...>, background=..., or
# CSS url(...). Tokens run until a quote, '>', ')' , whitespace or '#'.
_CID_REF = re.compile(r"cid:([^\"'()>\s#]+)", re.IGNORECASE)


def _lookup_cid(by_cid: dict[str, Attachment], key: str) -> Attachment | None:
    key = key.strip().strip("<>")
    att = by_cid.get(key)
    if att is not None:
        return att
    # Outlook often writes "cid:image001.png@01D..." while the part's
    # Content-ID is just "image001.png" (or vice-versa) - match on the stem.
    stem = key.split("@", 1)[0].lower()
    for cid, candidate in by_cid.items():
        if cid.split("@", 1)[0].lower() == stem:
            return candidate
    # Last resort: match by filename.
    for candidate in by_cid.values():
        if candidate.filename and candidate.filename.lower() == key.lower():
            return candidate
    return None


def _inline_cid_images(html: str, by_cid: dict[str, Attachment]) -> str:
    """Replace ``cid:`` references with self-contained ``data:`` URIs."""

    if not html or not by_cid or "cid:" not in html.lower():
        return html

    def repl(match: "re.Match[str]") -> str:
        att = _lookup_cid(by_cid, match.group(1))
        if att is None or not att.data:
            return match.group(0)
        mime = att.mime_type or "application/octet-stream"
        b64 = base64.b64encode(att.data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    return _CID_REF.sub(repl, html)
