"""Main window: sidebar (open files / PST folder tree), message list, viewer.

Layout::

    +---------------------------------------------------------------+
    |  Menu                                                        |
    +----------------+--------------------------------------------+
    |  Library tree  |  Message list (Sender/Subject/Date)        |
    |  (open files   |    - shown only for a PST/OST folder;      |
    |   + PST/OST    +--------------------------------------------+
    |   folders;     |  ViewerWidget (header + body + attachments)|
    |   hover a row  |    - takes the whole pane for a single     |
    |   for an 'x')  |      .eml / .msg                           |
    +----------------+--------------------------------------------+
    |  Status bar  [progress]                                     |
    +---------------------------------------------------------------+
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QRect,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from parsers.errors import ParserError
from parsers.loader import load
from parsers.models import EmailMessage, MessageStub, PstDocument
from ui import theme
from ui.viewer_widget import ViewerWidget
from utils.branding import make_app_icon
from utils.helpers import filter_supported, format_datetime, human_size, is_supported_file
from utils.workers import (
    FnRunnable,
    GetMessageRunnable,
    ListMessagesRunnable,
    submit,
)

_ITEM_ROLE = Qt.ItemDataRole.UserRole
_SORT_ROLE = Qt.ItemDataRole.UserRole + 1

MAX_RECENT = 10


# --------------------------------------------------------------------------- #
# Message-list model
# --------------------------------------------------------------------------- #
#: Column indices for the message list.
COL_ATTACH, COL_SENDER, COL_SUBJECT, COL_SIZE, COL_DATE = range(5)
_COLUMN_COUNT = 5


class EmailListModel(QAbstractTableModel):
    #: Columns the header context-menu lets the user hide (Subject stays put).
    OPTIONAL_COLUMNS = (COL_ATTACH, COL_SENDER, COL_SIZE, COL_DATE)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[MessageStub] = []

    def column_title(self, col: int) -> str:
        return {
            COL_ATTACH: self.tr("Attachment"),
            COL_SENDER: self.tr("Sender"),
            COL_SUBJECT: self.tr("Subject"),
            COL_SIZE: self.tr("Size"),
            COL_DATE: self.tr("Date"),
        }.get(col, "")

    # -- Qt overrides -------------------------------------------------- #
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else _COLUMN_COUNT

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            # The attachment column shows only a glyph; no text header.
            return "" if section == COL_ATTACH else self.column_title(section)
        if role == Qt.ItemDataRole.ToolTipRole and section == COL_ATTACH:
            return self.tr("Has attachments")
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        stub = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_ATTACH:
                return "\U0001F4CE" if stub.has_attachments else ""
            if col == COL_SENDER:
                return stub.sender or "(unknown sender)"
            if col == COL_SUBJECT:
                return stub.display_subject
            if col == COL_SIZE:
                return human_size(stub.size) if stub.size else ""
            if col == COL_DATE:
                return _fmt_date(stub.date)

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == COL_ATTACH and stub.has_attachments:
                return self.tr("Has attachments")
            if col == COL_SENDER:
                return stub.sender or "(unknown sender)"
            if col == COL_SUBJECT:
                return stub.display_subject

        if role == Qt.ItemDataRole.TextAlignmentRole and col == COL_SIZE:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.FontRole and stub.unread:
            f = QFont()
            f.setBold(True)
            return f

        if role == _SORT_ROLE:
            if col == COL_ATTACH:
                return 1 if stub.has_attachments else 0
            if col == COL_SENDER:
                return (stub.sender or "").lower()
            if col == COL_SUBJECT:
                return stub.display_subject.lower()
            if col == COL_SIZE:
                return stub.size or -1
            if col == COL_DATE:
                return stub.date.timestamp() if stub.date else float("-inf")

        if role == _ITEM_ROLE:
            return stub
        return None

    # -- helpers ---------------------------------------------------- #
    def set_stubs(self, stubs: list[MessageStub]) -> None:
        self.beginResetModel()
        self._rows = list(stubs)
        self.endResetModel()

    def stub_at(self, row: int) -> MessageStub | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None


def _fmt_date(dt: datetime | None) -> str:
    style = str(QSettings().value("appearance/dateFormat", "local"))
    return format_datetime(dt, with_tz=False, style=style)


def _jsonable(value):
    """Coerce a backend_id (ints / tuples) into something JSON round-trips."""
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


_REPLY_PREFIX = re.compile(
    r"^\s*(re|aw|fwd?|wg|sv|vs|ynt|ilt|il|rv|ref|res)\s*(\[\d+\])?\s*:\s*", re.IGNORECASE
)


def thread_key(subject: str) -> str:
    """Normalised subject for grouping a conversation (strips Re:/Fwd:/… )."""

    s = subject or ""
    while True:
        stripped = _REPLY_PREFIX.sub("", s)
        if stripped == s:
            break
        s = stripped
    return s.strip().lower()


class MailFilterProxy(QSortFilterProxyModel):
    """Filters the message list on sender + subject; sorts via ``_SORT_ROLE``.

    In "group by conversation" mode the primary sort key becomes the normalised
    subject, so replies sit next to their originals (newest thread first,
    newest-in-thread first).
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setSortRole(_SORT_ROLE)
        self.setDynamicSortFilter(True)
        self._needle = ""
        self._group = False

    def set_needle(self, text: str) -> None:
        self._needle = (text or "").strip().lower()
        self.invalidateFilter()

    def set_group(self, on: bool) -> None:
        self._group = bool(on)
        self.invalidate()
        self.sort(self.sortColumn(), self.sortOrder())

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        for col in (COL_SENDER, COL_SUBJECT):
            value = model.index(row, col, parent).data(Qt.ItemDataRole.DisplayRole)
            if value and self._needle in str(value).lower():
                return True
        return False

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        if not self._group:
            return super().lessThan(left, right)
        ls = left.data(_ITEM_ROLE)
        rs = right.data(_ITEM_ROLE)
        if ls is None or rs is None:
            return super().lessThan(left, right)
        lk, rk = thread_key(ls.subject), thread_key(rs.subject)
        if lk != rk:
            return lk < rk  # keep a thread's messages contiguous
        # Within a thread: newest first.
        return (ls.date or datetime.min) > (rs.date or datetime.min)


# --------------------------------------------------------------------------- #
# Library-tree "close" affordance
# --------------------------------------------------------------------------- #
class CloseButtonDelegate(QStyledItemDelegate):
    """Draws a small ``x`` at the right edge of a hovered top-level tree row.

    Click detection lives in :meth:`MainWindow.eventFilter`; this class only
    paints the glyph and exposes its hit rectangle.
    """

    SIZE = 16
    MARGIN = 6

    @classmethod
    def close_rect(cls, row_rect: QRect) -> QRect:
        return QRect(
            row_rect.right() - cls.SIZE - cls.MARGIN,
            row_rect.top() + (row_rect.height() - cls.SIZE) // 2,
            cls.SIZE,
            cls.SIZE,
        )

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:  # noqa: D401
        # Reserve room on the right so long names elide instead of colliding.
        opt = option
        is_top = not index.parent().isValid()
        if is_top:
            opt = type(option)(option)
            opt.rect = QRect(option.rect)
            opt.rect.setRight(opt.rect.right() - self.SIZE - self.MARGIN)
        super().paint(painter, opt, index)

        if not is_top or not (option.state & QStyle.StateFlag.State_MouseOver):
            return

        r = self.close_rect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.WindowText
        color: QColor = option.palette.color(role)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.6))
        m = 4
        painter.drawLine(r.left() + m, r.top() + m, r.right() - m, r.bottom() - m)
        painter.drawLine(r.left() + m, r.bottom() - m, r.right() - m, r.top() + m)
        painter.restore()


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    #: Emitted whenever a load fails, so tests can observe it without a dialog.
    loadFailed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EMPViewer")
        self.setWindowIcon(make_app_icon())
        self.setAcceptDrops(True)
        self.resize(1180, 760)

        self._open_paths: set[str] = set()
        self._pst_docs: list[PstDocument] = []
        self._runnables: list[Any] = []
        self._active_backend = None  # backend of the currently selected PST folder
        self._pending_hit: dict | None = None  # search hit awaiting its folder to load

        from utils.helpers import session_temp_dir
        from utils.search_index import SearchIndex

        self.search_index = SearchIndex(str(session_temp_dir() / "search.sqlite"))

        self._build_ui()
        self._build_menus()
        self._restore_settings()

        if QSettings().value("updates/checkOnStartup", False, type=bool):
            QTimer.singleShot(1500, lambda: self._check_updates(manual=False))

    # ------------------------------------------------------------------ #
    # Viewer tabs
    # ------------------------------------------------------------------ #
    @property
    def viewer(self) -> ViewerWidget:
        """The ViewerWidget in the current tab (created on demand)."""
        w = self.viewer_tabs.currentWidget()
        if w is None:
            w = self._new_viewer_tab()
        return w

    def _new_viewer_tab(self, *, focus: bool = True) -> ViewerWidget:
        v = ViewerWidget()
        v.openMessageRequested.connect(lambda msg: self._open_message_in_tab(msg))
        v.messageChanged.connect(lambda vw=v: self._on_tab_message_changed(vw))
        idx = self.viewer_tabs.addTab(v, self.tr("(empty)"))
        if focus:
            self.viewer_tabs.setCurrentIndex(idx)
        return v

    def _close_tab(self, index: int) -> None:
        if self.viewer_tabs.count() <= 1:
            self.viewer.clear()
            self._on_tab_message_changed(self.viewer)
            return
        w = self.viewer_tabs.widget(index)
        self.viewer_tabs.removeTab(index)
        if w is not None:
            w.deleteLater()

    def _open_message_in_tab(self, message) -> None:
        v = self._new_viewer_tab()
        v.set_message(message)

    def _on_tab_message_changed(self, vw: ViewerWidget) -> None:
        idx = self.viewer_tabs.indexOf(vw)
        if idx >= 0:
            m = vw._message
            title = m.display_name if m is not None else self.tr("(empty)")
            self.viewer_tabs.setTabText(idx, (title[:28] + "…") if len(title) > 29 else title)
            self.viewer_tabs.setTabToolTip(idx, title)
        if vw is self.viewer_tabs.currentWidget():
            self._sync_nav_actions()

    def _sync_nav_actions(self) -> None:
        back = getattr(self, "_act_back", None)
        if back is None:
            return  # menus not built yet
        v = self.viewer_tabs.currentWidget()
        back.setEnabled(bool(v) and v.can_go_back())
        self._act_fwd.setEnabled(bool(v) and v.can_go_forward())

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel(self.tr("Library"))
        self.tree.setMinimumWidth(220)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setItemDelegate(CloseButtonDelegate(self.tree))
        self.tree.viewport().installEventFilter(self)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        self.tree.itemEntered.connect(lambda *_: self.tree.viewport().update())
        self.tree.currentItemChanged.connect(self._on_tree_selection)

        self.list_model = EmailListModel(self)
        self.proxy = MailFilterProxy(self)
        self.proxy.setSourceModel(self.list_model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_DATE, Qt.SortOrder.DescendingOrder)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(COL_ATTACH, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(COL_SENDER, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_SUBJECT, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_ATTACH, 26)
        self.table.setColumnWidth(COL_SENDER, 200)
        self.table.setColumnWidth(COL_SIZE, 80)
        self.table.setColumnWidth(COL_DATE, 130)
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._list_header_menu)
        self._restore_list_columns()
        self.table.selectionModel().currentRowChanged.connect(self._on_table_row)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(self.tr("Filter by sender or subject…"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.proxy.set_needle)
        _esc_filter = QShortcut(QKeySequence("Escape"), self.filter_edit)
        _esc_filter.setContext(Qt.ShortcutContext.WidgetShortcut)
        _esc_filter.activated.connect(self.filter_edit.clear)

        # The message list only makes sense for a PST/OST folder; for a single
        # .eml/.msg it is dead space, so it starts hidden and is shown on demand.
        self._list_panel = QWidget()
        list_lay = QVBoxLayout(self._list_panel)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(2)
        list_lay.addWidget(self.filter_edit)
        list_lay.addWidget(self.table)
        self._list_panel.hide()

        # -- search results panel (reuses the message-list model) --------- #
        self.results_model = EmailListModel(self)
        self.results_view = QTableView()
        self.results_view.setModel(self.results_model)
        self.results_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results_view.verticalHeader().setVisible(False)
        self.results_view.setAlternatingRowColors(True)
        self.results_view.horizontalHeader().setStretchLastSection(True)
        self.results_view.setColumnWidth(COL_SENDER, 200)
        self.results_view.setColumnHidden(COL_SIZE, True)
        self.results_view.doubleClicked.connect(self._open_result_row)
        self._results_label = QLabel()
        _rclose = QToolButton()
        _rclose.setText("✕")
        _rclose.setToolTip(self.tr("Close search results"))
        _rclose.clicked.connect(self._clear_search)
        _rhead = QHBoxLayout()
        _rhead.setContentsMargins(4, 2, 4, 2)
        _rhead.addWidget(self._results_label, 1)
        _rhead.addWidget(_rclose)
        self._results_panel = QWidget()
        _rlay = QVBoxLayout(self._results_panel)
        _rlay.setContentsMargins(0, 0, 0, 0)
        _rlay.setSpacing(2)
        _rlay.addLayout(_rhead)
        _rlay.addWidget(self.results_view)
        self._results_panel.hide()

        self.viewer_tabs = QTabWidget()
        self.viewer_tabs.setTabsClosable(True)
        self.viewer_tabs.setMovable(True)
        self.viewer_tabs.setDocumentMode(True)
        self.viewer_tabs.tabCloseRequested.connect(self._close_tab)
        self.viewer_tabs.currentChanged.connect(lambda _i: self._sync_nav_actions())
        self._new_viewer_tab()

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self._results_panel)
        right_split.addWidget(self._list_panel)
        right_split.addWidget(self.viewer_tabs)
        right_split.setStretchFactor(0, 0)
        right_split.setStretchFactor(1, 0)
        right_split.setStretchFactor(2, 1)
        right_split.setSizes([240, 240, 500])
        self._right_split = right_split

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(self.tree)
        main_split.addWidget(right_split)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([240, 940])
        self._main_split = main_split

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            self.tr("Search all open mail — terms, from:, to:, subject:, has:attach, after:, before:")
        )
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._run_search)
        self.search_edit.textChanged.connect(lambda t: self._clear_search() if not t else None)
        _s_esc = QShortcut(QKeySequence("Escape"), self.search_edit)
        _s_esc.setContext(Qt.ShortcutContext.WidgetShortcut)
        _s_esc.activated.connect(self._clear_search)
        QShortcut(QKeySequence("Ctrl+Shift+F"), self).activated.connect(self.search_edit.setFocus)

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(6, 4, 6, 0)
        lay.setSpacing(4)
        lay.addWidget(self.search_edit)
        lay.addWidget(main_split)
        self.setCentralWidget(container)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setMaximumWidth(160)
        self.progress.hide()
        self._cancel_btn = QToolButton()
        self._cancel_btn.setText(self.tr("Cancel"))
        self._cancel_btn.setToolTip(self.tr("Stop the current operation"))
        self._cancel_btn.hide()
        self._cancel_btn.clicked.connect(self._cancel_active)
        self._status_label = QLabel(self.tr("Ready"))
        self.statusBar().addWidget(self._status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self._cancel_btn)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu(self.tr("&File"))
        act_open = file_menu.addAction(self.tr("&Open…"))
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._choose_files)

        self._recent_menu = file_menu.addMenu(self.tr("Open &Recent"))
        self._rebuild_recent_menu()

        self._act_close = file_menu.addAction(self.tr("&Close Item"))
        self._act_close.setShortcut(QKeySequence("Ctrl+W"))
        self._act_close.triggered.connect(self._close_current)

        act_save_att = file_menu.addAction(self.tr("Save &All Attachments…"))
        act_save_att.triggered.connect(self.viewer.save_all_attachments)

        file_menu.addSeparator()
        self._act_export_folder = file_menu.addAction(self.tr("Export F&older…"))
        self._act_export_folder.triggered.connect(lambda: self._export(entire=False))
        self._act_export_folder.setEnabled(False)
        self._act_export_pst = file_menu.addAction(self.tr("Export Entire &PST…"))
        self._act_export_pst.triggered.connect(lambda: self._export(entire=True))
        self._act_export_pst.setEnabled(False)

        file_menu.addSeparator()
        act_prefs = file_menu.addAction(self.tr("&Preferences…"))
        act_prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        act_prefs.setShortcut(QKeySequence.StandardKey.Preferences)
        act_prefs.triggered.connect(self._open_settings)

        file_menu.addSeparator()
        act_quit = file_menu.addAction(self.tr("&Quit"))
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)

        msg_menu = mb.addMenu(self.tr("&Message"))
        msg_menu.addAction(self.tr("Save Message &As…")).triggered.connect(self.viewer.save_message)
        act_print = msg_menu.addAction(self.tr("&Print…"))
        act_print.setShortcut(QKeySequence.StandardKey.Print)
        act_print.triggered.connect(self.viewer.print_message)
        msg_menu.addSeparator()
        copy_menu = msg_menu.addMenu(self.tr("&Copy"))
        copy_menu.addAction(self.tr("Body Text")).triggered.connect(self.viewer.copy_body)
        copy_menu.addAction(self.tr("Headers")).triggered.connect(self.viewer.copy_headers)
        act_copy_raw = copy_menu.addAction(self.tr("Raw Source"))
        act_copy_raw.setShortcut(QKeySequence("Ctrl+U"))
        act_copy_raw.triggered.connect(self.viewer.copy_raw_source)
        msg_menu.addSeparator()
        self._act_plain = msg_menu.addAction(self.tr("Show Plain &Text"))
        self._act_plain.setCheckable(True)
        self._act_plain.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self._act_plain.toggled.connect(self.viewer.set_plain_text_mode)
        self._act_source = msg_menu.addAction(self.tr("Show &Headers / Source"))
        self._act_source.setCheckable(True)
        self._act_source.setShortcut(QKeySequence("Ctrl+Shift+U"))
        self._act_source.toggled.connect(self.viewer.set_source_mode)

        view_menu = mb.addMenu(self.tr("&View"))
        act_zin = view_menu.addAction(self.tr("Zoom &In"))
        act_zin.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        act_zin.triggered.connect(lambda: self.viewer.zoom_by(1))
        act_zout = view_menu.addAction(self.tr("Zoom &Out"))
        act_zout.setShortcut(QKeySequence("Ctrl+-"))
        act_zout.triggered.connect(lambda: self.viewer.zoom_by(-1))
        act_zreset = view_menu.addAction(self.tr("&Reset Zoom"))
        act_zreset.setShortcut(QKeySequence("Ctrl+0"))
        act_zreset.triggered.connect(self.viewer.zoom_reset)
        view_menu.addSeparator()
        self._act_group = view_menu.addAction(self.tr("Group by &Conversation"))
        self._act_group.setCheckable(True)
        self._act_group.toggled.connect(self.proxy.set_group)
        view_menu.addSeparator()
        self._act_back = view_menu.addAction(self.tr("&Back"))
        self._act_back.setShortcut(QKeySequence("Alt+Left"))
        self._act_back.triggered.connect(lambda: self.viewer.go_back())
        self._act_fwd = view_menu.addAction(self.tr("&Forward"))
        self._act_fwd.setShortcut(QKeySequence("Alt+Right"))
        self._act_fwd.triggered.connect(lambda: self.viewer.go_forward())
        act_newtab = view_menu.addAction(self.tr("New &Tab"))
        act_newtab.setShortcut(QKeySequence.StandardKey.AddTab)
        act_newtab.triggered.connect(lambda: self._new_viewer_tab())
        act_closetab = view_menu.addAction(self.tr("&Close Tab"))
        act_closetab.setShortcut(QKeySequence("Ctrl+F4"))
        act_closetab.triggered.connect(
            lambda: self._close_tab(self.viewer_tabs.currentIndex())
        )
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu(self.tr("&Theme"))
        self._sync_nav_actions()
        group = QActionGroup(self)
        group.setExclusive(True)
        current = theme.load_mode()
        self._theme_actions: dict[theme.ThemeMode, QAction] = {}
        for mode in theme.ThemeMode:
            act = QAction(mode.label, self, checkable=True)
            act.setChecked(mode is current)
            act.triggered.connect(lambda _checked, m=mode: self._set_theme(m))
            group.addAction(act)
            theme_menu.addAction(act)
            self._theme_actions[mode] = act

        help_menu = mb.addMenu(self.tr("&Help"))
        help_menu.addAction(self.tr("Check for &Updates…")).triggered.connect(
            lambda: self._check_updates(manual=True)
        )
        help_menu.addAction(self.tr("&About EMPViewer")).triggered.connect(self._about)

        # Keyboard shortcuts that are not tied to a menu item.
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self._focus_find)
        QShortcut(QKeySequence("Alt+Down"), self).activated.connect(lambda: self._step_message(1))
        QShortcut(QKeySequence("Alt+Up"), self).activated.connect(lambda: self._step_message(-1))

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #
    def _restore_settings(self) -> None:
        s = QSettings()
        geom = s.value("window/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        state = s.value("window/state")
        if state is not None:
            self.restoreState(state)
        sizes = s.value("window/mainSplit")
        if sizes:
            self._main_split.setSizes([int(x) for x in sizes])
        rsizes = s.value("window/rightSplit")
        if rsizes:
            self._right_split.setSizes([int(x) for x in rsizes])

    def _persist_settings(self) -> None:
        s = QSettings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/state", self.saveState())
        s.setValue("window/mainSplit", self._main_split.sizes())
        s.setValue("window/rightSplit", self._right_split.sizes())

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    def load_external_path(self, path: str) -> None:
        """Slot for the application's ``fileOpenRequested`` signal and CLI args."""

        if not path:
            return
        if not is_supported_file(path):
            self._warn(self.tr("Could not open file"),
                       self.tr("'%s' is not a supported mail file.") % path)
            return
        self.open_path(path)
        self.raise_()
        self.activateWindow()

    #: Confirm before opening a file bigger than this (MB), by extension group.
    _BIG_FILE_MB = {".pst": 512, ".ost": 512, ".eml": 64, ".msg": 64}

    def _too_big_to_open(self, path: str) -> bool:
        p = Path(path)
        limit_mb = QSettings().value("io/largeFileWarnMB", type=int) or self._BIG_FILE_MB.get(
            p.suffix.lower(), 128
        )
        try:
            size_mb = p.stat().st_size / (1024 * 1024)
        except OSError:
            return False
        if size_mb < limit_mb:
            return False
        answer = QMessageBox.question(
            self,
            self.tr("Large file"),
            self.tr("%s is %d MB. Opening it may take a while and use a lot of memory. Continue?")
            % (p.name, round(size_mb)),
        )
        return answer != QMessageBox.StandardButton.Yes

    def open_path(self, path: str | Path) -> None:
        path = str(Path(path))
        if path in self._open_paths:
            self._select_top_level_by_path(path)
            return
        if self._too_big_to_open(path):
            return

        self._set_busy(self.tr("Opening %s…") % Path(path).name)
        task = FnRunnable(load, path)
        task.signals.finished.connect(lambda result, p=path: self._on_loaded(p, result))
        task.signals.error.connect(lambda msg, p=path: self._on_load_error(p, msg))
        self._track(task)
        submit(task)

    # ------------------------------------------------------------------ #
    # Load results
    # ------------------------------------------------------------------ #
    def _on_loaded(self, path: str, result: Any) -> None:
        self._clear_busy()
        self._open_paths.add(path)
        self._push_recent(path)

        if isinstance(result, EmailMessage):
            item = QTreeWidgetItem([Path(path).name])
            item.setToolTip(0, path)
            item.setData(0, _ITEM_ROLE, {"kind": "file", "message": result, "path": path})
            self.tree.addTopLevelItem(item)
            self.tree.setCurrentItem(item)
            self._index_message({"kind": "file", "path": path}, path, result)
        elif isinstance(result, PstDocument):
            self._pst_docs.append(result)
            root_item = QTreeWidgetItem([result.display_name])
            root_item.setToolTip(0, path)
            root_item.setData(0, _ITEM_ROLE, {"kind": "pstroot", "doc": result, "path": path})
            self._add_folder_items(root_item, result, result.root, folder_path=result.root.name)
            self.tree.addTopLevelItem(root_item)
            root_item.setExpanded(True)
            self.tree.setCurrentItem(root_item)
            self._index_pst(result)
        else:  # pragma: no cover - loader contract guarantees the two types
            self._warn(self.tr("Open"), self.tr("Unknown result type from parser."))
            return

        self._status_label.setText(self.tr("Opened %s") % Path(path).name)

    def _add_folder_items(self, parent_item: QTreeWidgetItem, doc: PstDocument, node, folder_path: str) -> None:
        for child in node.children:
            child_path = f"{folder_path}/{child.name}"
            label = child.name + (f"  ({child.message_count})" if child.message_count else "")
            child_item = QTreeWidgetItem([label])
            child_item.setData(
                0,
                _ITEM_ROLE,
                {
                    "kind": "pstfolder",
                    "doc": doc,
                    "folder_id": child.backend_id,
                    "folder_path": child_path,
                },
            )
            parent_item.addChild(child_item)
            self._add_folder_items(child_item, doc, child, child_path)

    def _on_load_error(self, path: str, message: str) -> None:
        logging.getLogger("empviewer.ui").warning("open %r failed: %s", path, message)
        self._clear_busy()
        self.loadFailed.emit(message)
        self._warn(self.tr("Could not open file"), message)

    # ------------------------------------------------------------------ #
    # Tree / table selection
    # ------------------------------------------------------------------ #
    def _on_tree_selection(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        data = current.data(0, _ITEM_ROLE) or {}
        kind = data.get("kind")

        is_pst = kind in ("pstfolder", "pstroot")
        self._act_export_folder.setEnabled(kind == "pstfolder")
        self._act_export_pst.setEnabled(is_pst)

        if kind == "file":
            self.list_model.set_stubs([])
            self._active_backend = None
            self._show_message_list(False)
            self.viewer.set_message(data["message"])
            self._reset_view_toggles()
            self._update_title(data["message"].display_name)
        elif kind == "pstfolder":
            self._active_backend = data["doc"].backend
            self._show_message_list(True)
            self._load_folder(data["doc"], data["folder_id"], data["folder_path"])
            self._update_title(data.get("folder_path"))
        else:  # pstroot / anything else
            self.list_model.set_stubs([])
            self._active_backend = None
            self._show_message_list(False)
            self.viewer.clear()
            self._update_title(None)

    # -- bulk export ----------------------------------------------------- #
    @staticmethod
    def _find_folder_node(root, backend_id):
        if backend_id is None:
            return None
        if root.backend_id == backend_id:
            return root
        for node in root.iter_descendants():
            if node.backend_id == backend_id:
                return node
        return None

    def _export(self, *, entire: bool) -> None:
        item = self.tree.currentItem()
        data = item.data(0, _ITEM_ROLE) if item else {}
        doc = data.get("doc")
        if not isinstance(doc, PstDocument):
            return
        if entire:
            node, title = doc.root, self.tr("Export entire PST to…")
        else:
            node = self._find_folder_node(doc.root, data.get("folder_id"))
            title = self.tr("Export folder to…")
        if node is None:
            return

        dest = QFileDialog.getExistingDirectory(self, title)
        if not dest:
            return
        try:
            not_empty = any(Path(dest).iterdir())
        except OSError:
            not_empty = False
        if not_empty and QMessageBox.question(
            self, self.tr("Export"),
            self.tr("That folder is not empty. Existing files with the same name "
                    "will be left alone and new copies numbered. Continue?"),
        ) != QMessageBox.StandardButton.Yes:
            return

        from parsers.export import export_folder

        self._set_busy(self.tr("Exporting messages…"))
        task = FnRunnable(
            export_folder, doc.backend, node, dest,
            recursive=True, pass_cancel=True, pass_progress=True,
        )
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(lambda n: self._on_export_done(int(n), dest))
        task.signals.error.connect(self._on_export_error)
        self._track(task)
        submit(task)

    def _on_export_done(self, count: int, dest: str) -> None:
        self._clear_busy()
        QMessageBox.information(
            self, self.tr("Export"),
            self.tr("Exported %n message(s) to:", "", count) + f"\n{dest}",
        )

    def _on_export_error(self, message: str) -> None:
        self._clear_busy()
        self._warn(self.tr("Export"), message)

    # -- full-text search --------------------------------------------- #
    @staticmethod
    def _body_text_for_index(msg) -> str:
        from ui.viewer_widget import _html_to_text, _strip_objects

        if msg.body_text and msg.body_text.strip():
            return _strip_objects(msg.body_text)
        if msg.body_html:
            return _strip_objects(_html_to_text(msg.body_html))
        return ""

    def _index_message(self, target: dict, source: str, msg) -> None:
        self.search_index.add(
            target, source=source,
            sender=msg.sender, recipients=", ".join(msg.to + msg.cc + msg.bcc),
            subject=msg.subject, body=self._body_text_for_index(msg),
            folder=msg.folder_path or "", date=msg.date,
            has_attachments=bool(msg.visible_attachments),
        )
        self.search_index.commit()

    def _index_pst(self, doc: PstDocument) -> None:
        """Walk every folder of *doc* and index its stubs (no body fetch)."""

        def work(*, should_cancel):
            for node in [doc.root, *doc.root.iter_descendants()]:
                if should_cancel():
                    return
                fpath = self._folder_path_for(doc, node)
                try:
                    stubs = doc.backend.list_messages(node.backend_id, should_cancel=should_cancel)
                except Exception:  # noqa: BLE001
                    continue
                for st in stubs:
                    self.search_index.add(
                        {"kind": "pst", "path": doc.path, "folder": fpath,
                         "bid": _jsonable(st.backend_id)},
                        source=doc.path, sender=st.sender, subject=st.subject,
                        folder=fpath, date=st.date, has_attachments=st.has_attachments,
                    )
            self.search_index.commit()

        task = FnRunnable(work, pass_cancel=True)
        self._track(task)
        submit(task)

    @staticmethod
    def _folder_path_for(doc: PstDocument, node) -> str:
        # Best-effort: the tree items carry folder_path; fall back to the name.
        return getattr(node, "name", "") or ""

    def _run_search(self) -> None:
        text = self.search_edit.text().strip()
        if not text:
            self._clear_search()
            return
        hits = self.search_index.search(text)
        self.results_model.set_stubs([self._hit_stub(h) for h in hits])
        self._results_label.setText(self.tr("%n result(s)", "", len(hits)))
        self._results_hits = hits
        self._results_panel.show()

    def _hit_stub(self, hit) -> MessageStub:
        subject = f"[{hit.folder}] {hit.subject}" if hit.folder else hit.subject
        stub = MessageStub(
            backend_id=hit.target, sender=hit.sender, subject=subject,
            date=hit.date, has_attachments=hit.has_attach,
        )
        return stub

    def _clear_search(self) -> None:
        if self.search_edit.text():
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        self.results_model.set_stubs([])
        self._results_panel.hide()

    def _open_result_row(self, index: QModelIndex) -> None:
        stub = self.results_model.stub_at(index.row())
        if stub is not None:
            self._open_search_hit(stub.backend_id)

    def _open_search_hit(self, target: dict) -> None:
        if not isinstance(target, dict):
            return
        if target.get("kind") == "file":
            self._select_top_level_by_path(target.get("path", ""))
            return
        # PST hit: find the doc, select its folder tree item, queue the message.
        path = target.get("path")
        doc = next((d for d in self._pst_docs if d.path == path), None)
        if doc is None:
            return
        item = self._find_tree_item(
            lambda d: d.get("kind") == "pstfolder" and d.get("doc") is doc
            and self._folder_label(d) == target.get("folder")
        )
        self._pending_hit = target
        if item is not None:
            self.tree.setCurrentItem(item)
        else:
            # fall back to the PST root
            root = self._find_tree_item(lambda d: d.get("kind") == "pstroot" and d.get("doc") is doc)
            if root is not None:
                self.tree.setCurrentItem(root)

    @staticmethod
    def _folder_label(item_data: dict) -> str:
        fp = item_data.get("folder_path") or ""
        return fp.rsplit("/", 1)[-1] if fp else ""

    def _find_tree_item(self, pred) -> QTreeWidgetItem | None:
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            if pred(it.data(0, _ITEM_ROLE) or {}):
                return it
            stack.extend(it.child(i) for i in range(it.childCount()))
        return None

    # -- message-list columns ---------------------------------------- #
    def _restore_list_columns(self) -> None:
        raw = QSettings().value("list/hiddenColumns", []) or []
        try:
            hidden = {int(x) for x in raw}
        except (TypeError, ValueError):
            hidden = set()
        for col in EmailListModel.OPTIONAL_COLUMNS:
            self.table.setColumnHidden(col, col in hidden)

    def _list_header_menu(self, pos) -> None:
        menu = QMenu(self)
        for col in EmailListModel.OPTIONAL_COLUMNS:
            act = menu.addAction(self.list_model.column_title(col))
            act.setCheckable(True)
            act.setChecked(not self.table.isColumnHidden(col))
            act.toggled.connect(lambda shown, c=col: self._set_list_column(c, shown))
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _set_list_column(self, col: int, shown: bool) -> None:
        self.table.setColumnHidden(col, not shown)
        hidden = [c for c in EmailListModel.OPTIONAL_COLUMNS if self.table.isColumnHidden(c)]
        QSettings().setValue("list/hiddenColumns", hidden)

    def _show_message_list(self, visible: bool) -> None:
        if visible and not self._list_panel.isVisible():
            self._list_panel.show()
            if self._list_panel.height() < 60:
                self._right_split.setSizes([260, max(360, self._right_split.height() - 260)])
        elif not visible and self._list_panel.isVisible():
            self._list_panel.hide()

    def _load_folder(self, doc: PstDocument, folder_id, folder_path: str) -> None:
        self.list_model.set_stubs([])
        self.filter_edit.clear()
        self.viewer.clear()
        self._current_folder = (doc, folder_id, folder_path)
        self._set_busy(self.tr("Loading %s…") % folder_path)
        task = ListMessagesRunnable(doc.backend, folder_id)
        task.signals.finished.connect(lambda stubs: self._on_folder_loaded(stubs))
        task.signals.error.connect(lambda msg: self._on_folder_error(msg))
        self._track(task)
        submit(task)

    def _on_folder_loaded(self, stubs: list[MessageStub]) -> None:
        self._clear_busy()
        self.list_model.set_stubs(stubs)
        self._status_label.setText(self.tr("%n message(s)", "", len(stubs)))

        # A search hit is waiting for this folder -> select its row.
        hit, self._pending_hit = self._pending_hit, None
        if hit is not None:
            want = _jsonable(hit.get("bid"))
            for row, st in enumerate(stubs):
                if _jsonable(st.backend_id) == want:
                    idx = self.proxy.mapFromSource(self.list_model.index(row, 0))
                    self.table.selectionModel().setCurrentIndex(
                        idx, QItemSelectionModel.SelectionFlag.ClearAndSelect
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    self.table.scrollTo(idx)
                    break

    def _on_folder_error(self, message: str) -> None:
        self._clear_busy()
        self._warn(self.tr("Folder"), message)

    def _on_table_row(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid() or self._active_backend is None:
            return
        source = self.proxy.mapToSource(current)
        stub = self.list_model.stub_at(source.row())
        if stub is None:
            return
        self._set_busy(self.tr("Opening message…"))
        task = GetMessageRunnable(self._active_backend, stub.backend_id)
        task.signals.finished.connect(self._on_message_loaded)
        task.signals.error.connect(lambda msg: (self._clear_busy(), self._warn(self.tr("Message"), msg)))
        self._track(task)
        submit(task)

    def _on_message_loaded(self, message: EmailMessage) -> None:
        self._clear_busy()
        self.viewer.set_message(message)
        self._reset_view_toggles()
        self._update_title(message.display_name)

    # ------------------------------------------------------------------ #
    # Close / housekeeping
    # ------------------------------------------------------------------ #
    def _close_current(self) -> None:
        self._close_item(self.tree.currentItem())

    def _close_item(self, item: QTreeWidgetItem | None) -> None:
        """Remove a top-level Library entry (given any of its rows)."""

        if item is None:
            return
        top = item
        while top.parent() is not None:
            top = top.parent()
        data = top.data(0, _ITEM_ROLE) or {}
        path = data.get("path")
        if data.get("kind") == "pstroot":
            doc = data.get("doc")
            if isinstance(doc, PstDocument):
                try:
                    doc.backend.close()
                except Exception:
                    pass
                if doc in self._pst_docs:
                    self._pst_docs.remove(doc)
        if path:
            self._open_paths.discard(path)
            self.search_index.remove_source(path)
        self._clear_search()
        idx = self.tree.indexOfTopLevelItem(top)
        self.tree.takeTopLevelItem(idx)
        self.list_model.set_stubs([])
        self.viewer.clear()
        self._show_message_list(False)
        self._active_backend = None
        self._update_title(None)

    def _tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, _ITEM_ROLE) or {}
        menu = QMenu(self)
        act_reveal = None
        src = data.get("path")
        if data.get("kind") in ("file", "pstroot") and src:
            act_reveal = menu.addAction(self.tr("Open Containing Folder"))
        act_close = menu.addAction(self.tr("Close"))
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_close:
            self._close_item(item)
        elif act_reveal is not None and chosen == act_reveal:
            self._reveal_in_file_manager(src)

    @staticmethod
    def _reveal_in_file_manager(path: str) -> None:
        import subprocess
        import sys as _sys

        p = Path(path)
        try:
            if _sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(p)])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(p)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.tree.viewport():
            et = event.type()
            if et in (QEvent.Type.MouseMove, QEvent.Type.Leave, QEvent.Type.HoverLeave):
                self.tree.viewport().update()
            elif et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                pt = event.position().toPoint()
                item = self.tree.itemAt(pt)
                if item is not None and item.parent() is None:
                    rect = self.tree.visualItemRect(item)
                    if CloseButtonDelegate.close_rect(rect).contains(pt):
                        self._close_item(item)
                        return True
        return super().eventFilter(obj, event)

    def _select_top_level_by_path(self, path: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, _ITEM_ROLE) or {}
            if data.get("path") == path:
                self.tree.setCurrentItem(item)
                return

    # ------------------------------------------------------------------ #
    # Busy indicator
    # ------------------------------------------------------------------ #
    def _set_busy(self, text: str) -> None:
        self._status_label.setText(text)
        self.progress.setRange(0, 0)  # indeterminate until a percentage arrives
        self.progress.show()
        self._cancel_btn.show()

    def _on_progress(self, percent: int, _msg: str) -> None:
        if percent < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)

    def _clear_busy(self) -> None:
        self.progress.hide()
        self.progress.setRange(0, 0)
        self.progress.reset()
        self._cancel_btn.hide()
        self._status_label.setText(self.tr("Ready"))

    def _cancel_active(self) -> None:
        for r in list(self._runnables):
            try:
                r.cancel()
            except Exception:
                pass
        self._runnables.clear()
        self._clear_busy()
        self._status_label.setText(self.tr("Cancelled"))

    def _track(self, runnable: Any) -> None:
        self._runnables.append(runnable)
        for sig in ("finished", "error"):
            getattr(runnable.signals, sig).connect(lambda *_: self._untrack(runnable))

    def _untrack(self, runnable: Any) -> None:
        if runnable in self._runnables:
            self._runnables.remove(runnable)

    # ------------------------------------------------------------------ #
    # Menu actions
    # ------------------------------------------------------------------ #
    def _choose_files(self) -> None:
        s = QSettings()
        start_dir = str(s.value("io/lastDir", str(Path.home())))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("Open mail files"),
            start_dir,
            self.tr("Mail files (*.eml *.msg *.pst *.ost *.mbox *.ics *.vcf);;All files (*)"),
        )
        for p in filter_supported(paths):
            self.open_path(p)
        if paths:
            s.setValue("io/lastDir", str(Path(paths[0]).parent))

    # -- recent files ------------------------------------------------- #
    def _recent_paths(self) -> list[str]:
        raw = QSettings().value("io/recentFiles", [])
        if isinstance(raw, str):
            return [raw]
        return [str(p) for p in raw] if raw else []

    def _push_recent(self, path: str) -> None:
        path = str(Path(path))
        items = [p for p in self._recent_paths() if p != path]
        items.insert(0, path)
        del items[MAX_RECENT:]
        QSettings().setValue("io/recentFiles", items)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        menu = self._recent_menu
        menu.clear()
        paths = [p for p in self._recent_paths() if Path(p).exists()]
        if not paths:
            act = menu.addAction(self.tr("(no recent files)"))
            act.setEnabled(False)
            return
        for p in paths:
            act = menu.addAction(Path(p).name)
            act.setToolTip(p)
            act.triggered.connect(lambda _checked=False, path=p: self.open_path(path))
        menu.addSeparator()
        menu.addAction(self.tr("Clear Recent Files")).triggered.connect(self._clear_recent)

    def _clear_recent(self) -> None:
        QSettings().remove("io/recentFiles")
        self._rebuild_recent_menu()

    # -- title / navigation ---------------------------------------- #
    def _update_title(self, subject: str | None) -> None:
        self.setWindowTitle(f"{subject} - EMPViewer" if subject else "EMPViewer")

    def _reset_view_toggles(self) -> None:
        """Uncheck the plain-text / source toggles without re-rendering
        (the viewer already resets its own state in ``set_message``)."""
        for act in (self._act_plain, self._act_source):
            act.blockSignals(True)
            act.setChecked(False)
            act.blockSignals(False)

    def _step_message(self, delta: int) -> None:
        if not self._list_panel.isVisible():
            return
        rows = self.proxy.rowCount()
        if rows == 0:
            return
        sm = self.table.selectionModel()
        cur = sm.currentIndex()
        row = cur.row() if cur.isValid() else (-1 if delta > 0 else rows)
        row = max(0, min(rows - 1, row + delta))
        idx = self.proxy.index(row, 0)
        sm.setCurrentIndex(
            idx,
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        self.table.scrollTo(idx)

    def _focus_find(self) -> None:
        if self._list_panel.isVisible() and not self.viewer.browser.hasFocus():
            self.filter_edit.setFocus()
            self.filter_edit.selectAll()
        else:
            self.viewer.open_find()

    def _set_theme(self, mode: theme.ThemeMode) -> None:
        from PySide6.QtWidgets import QApplication

        theme.apply(QApplication.instance(), mode)
        theme.save_mode(mode)

    def sync_theme_menu(self, mode: theme.ThemeMode) -> None:
        """Keep the View > Theme radio group in step with the Preferences dialog."""
        act = self._theme_actions.get(mode)
        if act is not None:
            act.setChecked(True)

    def _open_settings(self) -> None:
        from ui.settings_dialog import SettingsDialog

        SettingsDialog(self).exec()

    def apply_viewer_prefs(self) -> None:
        """Called by the Preferences dialog on OK: push viewer / date-format
        changes without needing a restart."""
        self.viewer.apply_prefs()
        # The date-format change affects every row already in the list.
        top_left = self.list_model.index(0, 0)
        bottom_right = self.list_model.index(
            max(0, self.list_model.rowCount() - 1), _COLUMN_COUNT - 1
        )
        if top_left.isValid():
            self.list_model.dataChanged.emit(top_left, bottom_right)

    def _check_updates(self, *, manual: bool) -> None:
        from utils.updates import UpdateChecker

        self._update_checker = UpdateChecker(self)  # keep a reference
        self._update_checker.check(
            lambda latest, newer, url: self._on_update_result(latest, newer, url, manual)
        )

    def _on_update_result(self, latest: str | None, newer: bool, url: str, manual: bool) -> None:
        s = QSettings()
        s.setValue("updates/lastChecked", latest or "")
        if newer and not manual and latest == str(s.value("updates/skipVersion", "")):
            return  # user asked not to be nagged about this one
        if newer:
            box = QMessageBox(self)
            box.setWindowTitle(self.tr("Updates"))
            box.setText(self.tr("A new version is available: %s") % latest)
            dl_label = (self.tr("Download") if url != "https://github.com/erdemovali/EMPViewer/releases"
                        else self.tr("Open Download Page"))
            open_btn = box.addButton(dl_label, QMessageBox.ButtonRole.AcceptRole)
            skip_btn = box.addButton(self.tr("Skip This Version"), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_btn:
                QDesktopServices.openUrl(QUrl(url))
            elif box.clickedButton() is skip_btn:
                s.setValue("updates/skipVersion", latest or "")
        elif manual and latest:
            QMessageBox.information(self, self.tr("Updates"),
                                   self.tr("You are running the latest version."))
        elif manual:
            QMessageBox.information(self, self.tr("Updates"),
                                   self.tr("Could not check for updates."))

    def _about(self) -> None:
        from utils.branding import logo_pixmap
        from utils.updates import current_version

        box = QMessageBox(self)
        box.setWindowTitle(self.tr("About EMPViewer"))
        box.setIconPixmap(logo_pixmap(72))
        box.setText(
            f"<h3>EMPViewer {current_version()}</h3>"
            "<p>" + self.tr("A viewer for .eml, .msg, .pst and .ost mail files.") + "</p>"
            "<p>" + self.tr("Drag files onto the window, or set EMPViewer as the default "
                            "handler for these file types.") + "</p>"
            "<p>" + self.tr("EMPViewer is MIT-licensed. Packaged builds include GPLv3 and "
                            "LGPLv3 components — see Licenses.") + "</p>"
        )
        licenses_btn = box.addButton(self.tr("Licenses"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is licenses_btn:
            from ui.license_dialog import LicenseDialog

            LicenseDialog(self).exec()

    def _warn(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    # ------------------------------------------------------------------ #
    # Drag & drop
    # ------------------------------------------------------------------ #
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        md = event.mimeData()
        if md.hasUrls() and any(
            u.isLocalFile() and is_supported_file(u.toLocalFile()) for u in md.urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        supported = filter_supported(paths)
        if not supported:
            event.ignore()
            return
        event.acceptProposedAction()
        for p in supported:
            self.open_path(p)

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        for r in list(self._runnables):
            try:
                r.cancel()
            except Exception:
                pass
        QThreadPool.globalInstance().waitForDone(2000)
        for doc in self._pst_docs:
            try:
                doc.backend.close()
            except Exception:
                pass
        try:
            self.search_index.close()
        except Exception:
            pass
        self._persist_settings()
        super().closeEvent(event)
