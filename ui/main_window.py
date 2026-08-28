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


class EmailListModel(QAbstractTableModel):
    _HEADERS = ("", "Sender", "Subject", "Size", "Date")
    #: Columns the header context-menu lets the user hide (Subject stays put).
    OPTIONAL_COLUMNS = (COL_ATTACH, COL_SENDER, COL_SIZE, COL_DATE)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[MessageStub] = []

    # -- Qt overrides -------------------------------------------------- #
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section]
        if role == Qt.ItemDataRole.ToolTipRole and section == COL_ATTACH:
            return "Has attachments"
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
                return "Has attachments"
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
    return format_datetime(dt, with_tz=False)


class MailFilterProxy(QSortFilterProxyModel):
    """Filters the message list on sender + subject; sorts via ``_SORT_ROLE``."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setSortRole(_SORT_ROLE)
        self.setDynamicSortFilter(True)
        self._needle = ""

    def set_needle(self, text: str) -> None:
        self._needle = (text or "").strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        for col in (COL_SENDER, COL_SUBJECT):
            value = model.index(row, col, parent).data(Qt.ItemDataRole.DisplayRole)
            if value and self._needle in str(value).lower():
                return True
        return False


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
        painter.setBrush(color if False else Qt.BrushStyle.NoBrush)
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

        self._build_ui()
        self._build_menus()
        self._restore_settings()

        if QSettings().value("updates/checkOnStartup", False, type=bool):
            QTimer.singleShot(1500, lambda: self._check_updates(manual=False))

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

        # The message list only makes sense for a PST/OST folder; for a single
        # .eml/.msg it is dead space, so it starts hidden and is shown on demand.
        self._list_panel = QWidget()
        list_lay = QVBoxLayout(self._list_panel)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(2)
        list_lay.addWidget(self.filter_edit)
        list_lay.addWidget(self.table)
        self._list_panel.hide()

        self.viewer = ViewerWidget()

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self._list_panel)
        right_split.addWidget(self.viewer)
        right_split.setStretchFactor(0, 0)
        right_split.setStretchFactor(1, 1)
        right_split.setSizes([240, 620])
        self._right_split = right_split

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(self.tree)
        main_split.addWidget(right_split)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([240, 940])
        self._main_split = main_split

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
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
        msg_menu.addSeparator()
        self._act_plain = msg_menu.addAction(self.tr("Show Plain &Text"))
        self._act_plain.setCheckable(True)
        self._act_plain.toggled.connect(self.viewer.set_plain_text_mode)
        self._act_source = msg_menu.addAction(self.tr("Show &Headers / Source"))
        self._act_source.setCheckable(True)
        self._act_source.toggled.connect(self.viewer.set_source_mode)

        view_menu = mb.addMenu(self.tr("&View"))
        theme_menu = view_menu.addMenu(self.tr("&Theme"))
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

    def open_path(self, path: str | Path) -> None:
        path = str(Path(path))
        if path in self._open_paths:
            self._select_top_level_by_path(path)
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
        elif isinstance(result, PstDocument):
            self._pst_docs.append(result)
            root_item = QTreeWidgetItem([result.display_name])
            root_item.setToolTip(0, path)
            root_item.setData(0, _ITEM_ROLE, {"kind": "pstroot", "doc": result, "path": path})
            self._add_folder_items(root_item, result, result.root, folder_path=result.root.name)
            self.tree.addTopLevelItem(root_item)
            root_item.setExpanded(True)
            self.tree.setCurrentItem(root_item)
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
        task = FnRunnable(export_folder, doc.backend, node, dest, recursive=True, pass_cancel=True)
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
            label = EmailListModel._HEADERS[col] or self.tr("Attachment")
            act = menu.addAction(label)
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
        menu = QMenu(self)
        act_close = menu.addAction(self.tr("Close"))
        if menu.exec(self.tree.viewport().mapToGlobal(pos)) == act_close:
            self._close_item(item)

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
        self.progress.show()
        self._cancel_btn.show()

    def _clear_busy(self) -> None:
        self.progress.hide()
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
            self.tr("Mail files (*.eml *.msg *.pst *.ost);;All files (*)"),
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

    def _check_updates(self, *, manual: bool) -> None:
        from utils.updates import UpdateChecker

        self._update_checker = UpdateChecker(self)  # keep a reference
        self._update_checker.check(
            lambda latest, newer, url: self._on_update_result(latest, newer, url, manual)
        )

    def _on_update_result(self, latest: str | None, newer: bool, url: str, manual: bool) -> None:
        QSettings().setValue("updates/lastChecked", latest or "")
        if newer:
            box = QMessageBox(self)
            box.setWindowTitle(self.tr("Updates"))
            box.setText(self.tr("A new version is available: %s") % latest)
            open_btn = box.addButton(self.tr("Open Download Page"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_btn:
                QDesktopServices.openUrl(QUrl(url))
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
        self._persist_settings()
        super().closeEvent(event)
