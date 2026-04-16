from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from PyQt6.QtGui import QAction, QIcon, QKeyEvent, QKeySequence, QShortcut

from konsole_tray.dbus_client import KonsoleTab, KonsoleWindow, get_all_tabs, activate_tab

_STYLE = """
QWidget#Spotlight {
    background-color: palette(window);
    border: 1px solid palette(mid);
    border-radius: 8px;
}
QLineEdit {
    padding: 10px;
    font-size: 16px;
    border: 1px solid palette(mid);
    border-radius: 5px;
    background: palette(base);
}
QListWidget {
    border: none;
    background: palette(base);
    font-size: 14px;
}
QListWidget::item {
    padding: 4px 8px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: palette(highlight);
    color: palette(highlighted-text);
}
QListWidget::item:hover {
    background-color: palette(highlight);
    color: palette(highlighted-text);
}
"""


class SpotlightWindow(QWidget):
    """Spotlight-style search window for Konsole tabs.

    Centered by KWin automatically (Dialog hint). Escape closes it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Spotlight")
        self.setWindowTitle("Konsole Tab Finder")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setFixedSize(700, 600)
        self.setStyleSheet(_STYLE)

        self._all_items: list[tuple[QListWidgetItem, KonsoleTab | None]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(8)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search Konsole tabs...")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search_changed)
        self._search_box.returnPressed.connect(self._activate_selected)
        layout.addWidget(self._search_box)

        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        # Bottom bar
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.hide)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(QApplication.quit)
        bottom.addWidget(close_btn)
        bottom.addStretch()
        bottom.addWidget(quit_btn)
        layout.addLayout(bottom)

        # Ctrl+Q to quit
        QShortcut(QKeySequence("Ctrl+Q"), self, QApplication.quit)

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
        self._search_box.clear()
        self._search_box.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.key() == Qt.Key.Key_Down:
            self._list.setFocus()
            if self._list.currentRow() < 0:
                self._select_next_tab(0)
        elif event.key() == Qt.Key.Key_Up:
            self._list.setFocus()
        else:
            super().keyPressEvent(event)

    def _refresh(self) -> None:
        self._list.clear()
        self._all_items = []

        windows = get_all_tabs()

        if not windows:
            item = QListWidgetItem("No Konsole instances found")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            self._all_items.append((item, None))
            return

        for win in windows:
            win_num = win.window_path.split("/")[-1]
            header = QListWidgetItem(f"Window {win_num}")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            self._list.addItem(header)
            self._all_items.append((header, None))

            for tab in win.tabs:
                label = self._format_tab_label(tab)
                item = QListWidgetItem(f"  {label}")
                self._list.addItem(item)
                self._all_items.append((item, tab))

    @staticmethod
    def _format_tab_label(tab: KonsoleTab) -> str:
        if tab.command and tab.command not in tab.title and tab.title not in tab.command:
            return f"{tab.title}  [{tab.command}]"
        return tab.title

    def _on_search_changed(self, text: str) -> None:
        query = text.lower()
        current_header: QListWidgetItem | None = None
        header_has_match: dict[int, bool] = {}

        for item, tab in self._all_items:
            if tab is None:
                current_header = item
                header_has_match[id(item)] = False
            else:
                matches = (
                    not query
                    or query in tab.title.lower()
                    or query in tab.command.lower()
                )
                item.setHidden(not matches)
                if matches and current_header is not None:
                    header_has_match[id(current_header)] = True

        for item, tab in self._all_items:
            if tab is None:
                has_match = header_has_match.get(id(item), False)
                item.setHidden(not has_match and bool(query))

        # Auto-select first visible tab
        self._select_next_tab(0)

    def _select_next_tab(self, from_row: int) -> None:
        """Select the next visible tab item starting from from_row."""
        for row in range(from_row, self._list.count()):
            item = self._list.item(row)
            if not item.isHidden():
                # Check it's a tab, not a header
                for list_item, tab in self._all_items:
                    if list_item is item and tab is not None:
                        self._list.setCurrentRow(row)
                        return

    def _activate_selected(self) -> None:
        """Activate the currently selected tab (Enter key)."""
        current = self._list.currentItem()
        if current:
            self._on_item_clicked(current)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        for list_item, tab in self._all_items:
            if list_item is item and tab is not None:
                self.hide()
                activate_tab(tab)
                return


class KonsoleTray:
    def __init__(self) -> None:
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(QIcon.fromTheme("utilities-terminal"))

        self._spotlight = SpotlightWindow()

        # Right-click: context menu (must use aboutToShow for KDE DBusMenu protocol)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._populate_menu)
        self.tray.setContextMenu(self.menu)

        # Left-click: spotlight
        self.tray.activated.connect(self._on_tray_activated)

    def show(self) -> None:
        self.tray.show()

    def _populate_menu(self) -> None:
        self.menu.clear()
        search_action = QAction("Search Tabs...", self.menu)
        search_action.triggered.connect(self._spotlight.toggle)
        self.menu.addAction(search_action)
        self.menu.addSeparator()
        quit_action = QAction("Quit", self.menu)
        quit_action.triggered.connect(QApplication.quit)
        self.menu.addAction(quit_action)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._spotlight.toggle()
