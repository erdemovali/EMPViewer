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
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QEvent,
    QRect,
    QSettings,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextCursor, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
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

from parsers.export import to_eml_bytes
from parsers.models import Attachment, EmailMessage
from utils.helpers import format_datetime, human_size, open_with_os, safe_filename, write_temp_attachment


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
        self.allowed_senders = set()
        self.sender_key = ""
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

    #: Normalised e-mail address of the current message's sender, and the set of
    #: senders the user has chosen to always load remote content from.
    sender_key: str = ""
    allowed_senders: set[str]

    def _remote_ok(self) -> bool:
        return self.allow_remote or (
            bool(self.sender_key) and self.sender_key in getattr(self, "allowed_senders", set())
        )

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
            if self._remote_ok():
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
        self._is_message = attachment.attach_kind == "message" and attachment.embedded is not None
        self.setObjectName("AttachmentChip")
        prefix = "✉ " if self._is_message else ""
        self.setText(f"{prefix}{attachment.filename}  ·  {human_size(attachment.size)}")
        self.setToolTip(f"{attachment.filename}\n{attachment.mime_type} — {human_size(attachment.size)}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.clicked.connect(self._open_message if self._is_message else self.open_with_os)

    # -- actions --------------------------------------------------------- #
    def _open_message(self) -> None:
        """Show the embedded message in a modal viewer."""

        from PySide6.QtWidgets import QDialog, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle(self._att.embedded.display_name)
        dlg.resize(820, 620)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        viewer = ViewerWidget(dlg)
        viewer.set_message(self._att.embedded)
        lay.addWidget(viewer)
        dlg.exec()

    def open_with_os(self) -> None:
        try:
            path = write_temp_attachment(self._att.filename, self._att.data)
        except OSError as exc:
            QMessageBox.warning(self, self.tr("Attachment"),
                                self.tr("Could not write a temporary copy:") + f"\n{exc}")
            return
        if not open_with_os(path):
            QMessageBox.information(
                self,
                self.tr("Attachment"),
                self.tr("No application is registered to open this file.\nA copy was saved to:")
                + f"\n{path}",
            )

    def save_as(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, self.tr("Save attachment"), self._att.filename)
        if not target:
            return
        try:
            with open(target, "wb") as fh:
                fh.write(self._att.data)
        except OSError as exc:
            QMessageBox.warning(self, self.tr("Attachment"), self.tr("Could not save:") + f"\n{exc}")

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        act_msg = menu.addAction(self.tr("Open Message")) if self._is_message else None
        act_open = menu.addAction(self.tr("Open"))
        act_save = menu.addAction(self.tr("Save As…"))
        chosen = menu.exec(self.mapToGlobal(pos))
        if act_msg is not None and chosen == act_msg:
            self._open_message()
        elif chosen == act_open:
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
        self._source_mode = False
        self._force_text = False
        self._zoom = 0
        self._prefer_text = False
        self._load_prefs()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_remote_banner())
        self.browser = RemoteBlockingBrowser(self)
        # The message body is always shown on a light "sheet" - HTML mail carries
        # its own colours that assume a white background, so following a dark app
        # theme here makes dark-on-dark text unreadable.
        self.browser.setObjectName("MessageBody")
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
        msg = QLabel(self.tr("This message contains remote content that was blocked to protect your privacy."))
        msg.setWordWrap(True)
        btn = QPushButton(self.tr("Load remote content"))
        btn.clicked.connect(self._load_remote)
        self._remote_trust_btn = QPushButton(self.tr("Always allow this sender"))
        self._remote_trust_btn.clicked.connect(self._trust_sender)
        lay.addWidget(msg, 1)
        lay.addWidget(self._remote_trust_btn)
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
        self.find_input.setPlaceholderText(self.tr("Find in message…"))
        self.find_input.setClearButtonEnabled(True)
        self.find_input.textChanged.connect(lambda: self._find(forward=True, incremental=True))
        self.find_input.returnPressed.connect(lambda: self._find(forward=True))
        self.find_input.installEventFilter(self)

        self._find_status = QLabel("")
        self._find_status.setMinimumWidth(72)

        btn_prev = QToolButton()
        btn_prev.setText("▲")
        btn_prev.setAutoRaise(True)
        btn_prev.setToolTip(self.tr("Previous match"))
        btn_prev.clicked.connect(lambda: self._find(forward=False))
        btn_next = QToolButton()
        btn_next.setText("▼")
        btn_next.setAutoRaise(True)
        btn_next.setToolTip(self.tr("Next match"))
        btn_next.clicked.connect(lambda: self._find(forward=True))
        btn_close = QToolButton()
        btn_close.setText("✕")
        btn_close.setAutoRaise(True)
        btn_close.setToolTip(self.tr("Close (Esc)"))
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
        line1 = self.tr("Open an .eml, .msg, .pst or .ost file to read it here.")
        line2 = self.tr(
            "Use File > Open, drag a file onto the window, or set EMPViewer "
            "as the default handler for these file types."
        )
        self._set_body_html(
            "<div style='color:#5f6368;padding:40px 28px;font-family:sans-serif;line-height:1.6'>"
            f"<p style='font-size:15px'>{_esc(line1)}</p>"
            f"<p>{_esc(line2)}</p>"
            "</div>"
        )
        self.remote_banner.hide()
        self.close_find()
        self._clear_attachments()

    def _load_prefs(self) -> None:
        s = QSettings()
        self._zoom = int(s.value("viewer/fontDelta", 0) or 0)
        self._prefer_text = bool(s.value("viewer/preferPlainText", False, type=bool))

    def apply_prefs(self) -> None:
        """Re-read Preferences and re-render (called from the settings dialog)."""
        self._load_prefs()
        if self._message is not None:
            self.lbl_meta.setText(self._meta_html(self._message))
            self._render_body(self._message)

    def _set_body_html(self, html: str) -> None:
        self.browser.setHtml(html)
        if self._zoom:
            self.browser.zoomIn(self._zoom)

    def set_message(self, message: EmailMessage) -> None:
        self._message = message
        self._had_remote = False
        self._source_mode = False
        self._force_text = False
        self.browser.allow_remote = bool(
            QSettings().value("viewer/autoLoadRemote", False, type=bool)
        )
        self.browser.sender_key = _sender_key(message.sender)
        raw = QSettings().value("viewer/remoteAllowSenders", []) or []
        self.browser.allowed_senders = {str(x).lower() for x in raw}
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
        T = QCoreApplication.translate
        style = str(QSettings().value("appearance/dateFormat", "local"))
        rows: list[str] = []
        if m.sender:
            rows.append(f"<b>{T('ViewerWidget', 'From')}:</b> {_esc(m.sender)}")
        if m.to:
            rows.append(f"<b>{T('ViewerWidget', 'To')}:</b> {_esc(', '.join(m.to))}")
        if m.cc:
            rows.append(f"<b>{T('ViewerWidget', 'Cc')}:</b> {_esc(', '.join(m.cc))}")
        if m.bcc:
            rows.append(f"<b>{T('ViewerWidget', 'Bcc')}:</b> {_esc(', '.join(m.bcc))}")
        if m.date:
            rows.append(f"<b>{T('ViewerWidget', 'Date')}:</b> {_esc(format_datetime(m.date, style=style))}")
        if m.folder_path:
            rows.append(f"<b>{T('ViewerWidget', 'Folder')}:</b> {_esc(m.folder_path)}")
        badges = []
        if m.is_signed:
            badges.append(T("ViewerWidget", "signed"))
        if m.is_encrypted:
            badges.append(T("ViewerWidget", "encrypted"))
        if badges:
            rows.append(f"<b>{T('ViewerWidget', 'Security')}:</b> \U0001F512 {_esc(', '.join(badges))}")
        return "<br>".join(rows)

    _PRE = "white-space:pre-wrap;word-wrap:break-word;padding:12px;color:#1b1d21"

    def _render_body(self, m: EmailMessage) -> None:
        if self._source_mode:
            if m.raw_source:
                body = m.raw_source.decode("utf-8", "replace")
            else:
                body = _headers_dump(m)
            self._set_body_html(
                f"<pre style='{self._PRE};font-family:monospace;font-size:12px'>"
                + _esc(body) + "</pre>"
            )
            return

        want_text = self._force_text or self._prefer_text
        if m.body_html and not want_text:
            # Bake embedded images straight into the HTML as data: URIs. This is
            # far more reliable across QTextBrowser versions than resolving
            # "cid:" through loadResource(), and it also covers images referenced
            # from CSS (background / url()).
            self._set_body_html(_inline_cid_images(m.body_html, m.inline_by_cid))
        elif m.body_text or (want_text and m.body_html):
            text = m.body_text or _html_to_text(m.body_html or "")
            self._set_body_html(
                f"<pre style='{self._PRE};font-family:sans-serif'>" + _esc(text) + "</pre>"
            )
        else:
            self._set_body_html(
                "<div style='color:#5f6368;padding:24px;font-family:sans-serif'>"
                + _esc(self.tr("(This message has no readable body.)")) + "</div>"
            )

    # -- remote content ------------------------------------------- #
    def _on_remote_blocked(self) -> None:
        self._had_remote = True
        if self._message is not None and not self.browser._remote_ok():
            self._remote_trust_btn.setVisible(bool(self.browser.sender_key))
            self.remote_banner.show()

    def _load_remote(self) -> None:
        self.browser.allow_remote = True
        self.remote_banner.hide()
        if self._message is not None:
            self._render_body(self._message)

    def _trust_sender(self) -> None:
        key = self.browser.sender_key
        if not key:
            return
        s = QSettings()
        current = {str(x).lower() for x in (s.value("viewer/remoteAllowSenders", []) or [])}
        current.add(key)
        s.setValue("viewer/remoteAllowSenders", sorted(current))
        self.browser.allowed_senders = current
        self.remote_banner.hide()
        if self._message is not None:
            self._render_body(self._message)

    # -- zoom / raw source -------------------------------------- #
    def zoom_by(self, delta: int) -> None:
        self._zoom = max(-6, min(16, self._zoom + delta))
        QSettings().setValue("viewer/fontDelta", self._zoom)
        if self._message is not None:
            self._render_body(self._message)

    def zoom_reset(self) -> None:
        self._zoom = 0
        QSettings().setValue("viewer/fontDelta", 0)
        if self._message is not None:
            self._render_body(self._message)

    def copy_raw_source(self) -> None:
        m = self._message
        if m is None:
            return
        text = m.raw_source.decode("utf-8", "replace") if m.raw_source else _headers_dump(m)
        QGuiApplication.clipboard().setText(text)

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
        self._find_status.setText("" if found else self.tr("No matches"))

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

    # -- view toggles ----------------------------------------- #
    def set_source_mode(self, on: bool) -> None:
        self._source_mode = bool(on)
        if self._message is not None:
            self._render_body(self._message)

    def set_plain_text_mode(self, on: bool) -> None:
        self._force_text = bool(on)
        if self._message is not None:
            self._render_body(self._message)

    def has_message(self) -> bool:
        return self._message is not None

    # -- export / print / copy ------------------------------- #
    def _default_name(self, ext: str) -> str:
        base = safe_filename(self._message.display_name if self._message else "message")
        return f"{base or 'message'}{ext}"

    def _message_html(self) -> str:
        m = self._message
        assert m is not None
        if m.body_html:
            return _inline_cid_images(m.body_html, m.inline_by_cid)
        return (
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{_esc(m.display_name)}</title>"
            f"<pre style='white-space:pre-wrap;word-wrap:break-word'>{_esc(m.body_text or '')}</pre>"
        )

    def _write_pdf(self, path: str) -> None:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        self.browser.document().print_(printer)

    def save_message(self) -> None:
        if self._message is None:
            QMessageBox.information(self, self.tr("Save Message"), self.tr("No message is open."))
            return
        by_filter = {
            self.tr("Mail message (*.eml)"): ".eml",
            self.tr("PDF document (*.pdf)"): ".pdf",
            self.tr("Web page (*.html)"): ".html",
            self.tr("Plain text (*.txt)"): ".txt",
        }
        target, chosen = QFileDialog.getSaveFileName(
            self, self.tr("Save message as"), self._default_name(".eml"),
            ";;".join(by_filter),
        )
        if not target:
            return
        ext = Path(target).suffix.lower()
        if not ext:
            ext = by_filter.get(chosen, ".eml")
            target += ext
        try:
            if ext == ".pdf":
                self._write_pdf(target)
            elif ext in (".html", ".htm"):
                Path(target).write_text(self._message_html(), encoding="utf-8")
            elif ext == ".txt":
                Path(target).write_text(self.browser.document().toPlainText(), encoding="utf-8")
            else:
                Path(target).write_bytes(to_eml_bytes(self._message))
        except OSError as exc:
            QMessageBox.warning(self, self.tr("Save Message"), self.tr("Could not save:") + f"\n{exc}")

    def print_message(self) -> None:
        if self._message is None:
            QMessageBox.information(self, self.tr("Print"), self.tr("No message is open."))
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(printer, self).exec():
            self.browser.document().print_(printer)

    def _body_plain_text(self) -> str:
        """Best plain-text rendering of the current body.

        ``QTextBrowser.toPlainText()`` substitutes U+FFFC (object replacement)
        for every inline image, so an image-heavy HTML mail copies as a run of
        invisible characters - which reads as "nothing was copied". Strip those,
        and fall back to the message model when what's left is empty.
        """

        m = self._message
        if m is None or (not m.body_text and not m.body_html):
            return ""
        # In source / headers view the document holds the dump, not the body.
        if not self._source_mode:
            text = _strip_objects(self.browser.document().toPlainText())
            if text.strip():
                return text
        if m.body_text and m.body_text.strip():
            return m.body_text
        if m.body_html:
            return _strip_objects(_html_to_text(m.body_html))
        return ""

    def copy_body(self) -> None:
        if self._message is None:
            return
        text = self._body_plain_text()
        if text.strip():
            QGuiApplication.clipboard().setText(text)
        else:
            self._flash(self.tr("Nothing to copy"))

    def copy_headers(self) -> None:
        if self._message is not None:
            QGuiApplication.clipboard().setText(_headers_dump(self._message))

    def _flash(self, text: str) -> None:
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(text, 2500)
            except Exception:  # noqa: BLE001
                pass

    # -- misc ------------------------------------------------- #
    def save_all_attachments(self) -> None:
        if not self._message or not self._message.visible_attachments:
            QMessageBox.information(self, self.tr("Attachments"),
                                   self.tr("This message has no attachments."))
            return
        folder = QFileDialog.getExistingDirectory(self, self.tr("Save all attachments to…"))
        if not folder:
            return

        saved = 0
        for att in self._message.visible_attachments:
            try:
                (Path(folder) / safe_filename(att.filename)).write_bytes(att.data)
                saved += 1
            except OSError:
                continue
        QMessageBox.information(
            self, self.tr("Attachments"),
            self.tr("Saved %n attachment(s) to:", "", saved) + f"\n{folder}",
        )


def _esc(text: str) -> str:
    return _html.escape(text or "")


#: U+FFFC object replacement (inline images), U+200B zero-width space, and the
#: BOM - all of which QTextBrowser can leave in ``toPlainText()`` output.
_OBJECT_CHARS = str.maketrans("", "", "￼​﻿")


def _strip_objects(text: str) -> str:
    return (text or "").translate(_OBJECT_CHARS)


_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _sender_key(sender: str) -> str:
    """The bare e-mail address from a ``"Name <addr>"`` string, lower-cased."""

    m = _ADDR_RE.search(sender or "")
    return m.group(0).lower() if m else ""


def _headers_dump(m: EmailMessage) -> str:
    T = QCoreApplication.translate
    lines: list[str] = []

    def add(label: str, value: str) -> None:
        if value:
            lines.append(f"{label}: {value}")

    if m.headers:
        # Real transport headers exist - show them as-is (deduped, order kept).
        for key, value in m.headers.items():
            add(str(key), str(value))
    else:
        add(T("ViewerWidget", "From"), m.sender)
        add(T("ViewerWidget", "To"), ", ".join(m.to))
        add(T("ViewerWidget", "Cc"), ", ".join(m.cc))
        add(T("ViewerWidget", "Bcc"), ", ".join(m.bcc))
        add(T("ViewerWidget", "Date"), format_datetime(m.date) if m.date else "")
        add(T("ViewerWidget", "Subject"), m.subject)
    add(T("ViewerWidget", "Folder"), m.folder_path or "")
    return "\n".join(lines) or T("ViewerWidget", "(no headers)")


def _html_to_text(html: str) -> str:
    doc = QTextDocument()
    doc.setHtml(html)
    return doc.toPlainText()


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


#: Don't bake an inline image bigger than this into the HTML as a data: URI -
#: a handful of multi-MB images can make setHtml() crawl.
_MAX_INLINE_BYTES = 8 * 1024 * 1024


def _inline_cid_images(html: str, by_cid: dict[str, Attachment]) -> str:
    """Replace ``cid:`` references with self-contained ``data:`` URIs."""

    if not html or not by_cid or "cid:" not in html.lower():
        return html

    def repl(match: "re.Match[str]") -> str:
        att = _lookup_cid(by_cid, match.group(1))
        if att is None or not att.data or len(att.data) > _MAX_INLINE_BYTES:
            return match.group(0)
        mime = att.mime_type or "application/octet-stream"
        b64 = base64.b64encode(att.data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    return _CID_REF.sub(repl, html)
