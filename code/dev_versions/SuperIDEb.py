import sys
import os
import os.path
import json
import re
import html
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
                             QMenuBar, QAction, QFileDialog, QDockWidget, QListWidget, QMessageBox,
                             QInputDialog, QMenu, QFrame, QDialog, QDialogButtonBox, QTextBrowser)
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
from PyQt5.QtGui import QTextOption, QTextDocument, QFont, QPainter, QFontMetrics, QTextCursor, QIcon, QTextCharFormat, QColor, QImage
from PyQt5.QtCore import Qt, QUrl, QPoint, QTimer, QRect
from PyQt5.QtGui import QDesktopServices
from collections import deque
import uuid

# Helper function to get the base path for resources
def resource_path(relative_path):
    """Get absolute path to a resource, works for dev and PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    resolved_path = os.path.join(base_path, relative_path)
    return resolved_path

class LineNumberArea(QFrame):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setFixedWidth(40)
        self.marked_line = -1
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.clear_marker)
        self.original_number = None

    def set_marker(self, line_number):
        if self.marked_line != -1:
            self.clear_marker()
        self.marked_line = line_number
        self.original_number = str(line_number + 1)
        self.timer.start(self.editor.ide.settings["goto_marker_duration"] * 1000)
        self.update()

    def clear_marker(self):
        self.marked_line = -1
        self.original_number = None
        self.timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), Qt.lightGray if self.editor.ide.settings["theme"] == "light" else Qt.darkGray)
        doc = self.editor.document()
        cursor = self.editor.cursorForPosition(self.editor.viewport().pos())
        first_visible_block = doc.findBlock(cursor.position())
        block_number = first_visible_block.blockNumber()
        font = QFont("Arial", self.editor.ide.settings["font_size"])
        painter.setFont(font)
        fm = QFontMetrics(font)
        ascent = fm.ascent()
        block = first_visible_block
        viewport_top = self.editor.viewport().rect().top()
        while block.isValid() and block.layout().position().y() <= event.rect().bottom() + self.editor.verticalScrollBar().value():
            if block.isValid() and block.isVisible():
                number = str(block_number + 1)
                y_pos = block.layout().position().y() - self.editor.verticalScrollBar().value() + ascent
                if y_pos >= viewport_top:
                    if block_number == self.marked_line and self.marked_line != -1:
                        triangle_points = [
                            QPoint(self.width() - 10, int(y_pos - ascent + fm.height() / 2)),
                            QPoint(self.width() - 20, int(y_pos - ascent + fm.height() / 4)),
                            QPoint(self.width() - 20, int(y_pos - ascent + fm.height() * 3 / 4))
                        ]
                        painter.setBrush(Qt.red if self.editor.ide.settings["theme"] == "light" else Qt.yellow)
                        painter.setPen(Qt.black)
                        painter.drawPolygon(*triangle_points)
                    else:
                        painter.setPen(Qt.black if self.editor.ide.settings["theme"] == "light" else Qt.white)
                        painter.drawText(0, int(y_pos - ascent), self.width() - 5, fm.height(), Qt.AlignRight, number)
            block = block.next()
            block_number += 1

class CustomTextEdit(QTextEdit):
    def __init__(self, ide):
        super().__init__()
        self.ide = ide
        self.line_number_area = LineNumberArea(self)
        self.document().blockCountChanged.connect(self.update_line_number_area_width)
        self.cursorPositionChanged.connect(self.update_line_number_area)
        self.update_line_number_area_width()

    def line_number_area_width(self):
        return 40 if self.ide.settings["line_numbers"] else 0

    def update_line_number_area_width(self):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)
        self.line_number_area.setVisible(self.ide.settings["line_numbers"])
        self.update_line_number_area()

    def update_line_number_area(self):
        self.line_number_area.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(cr.left(), cr.top(), self.line_number_area_width(), cr.height())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Tab:
            self.ide.indent()
            return
        elif event.key() == Qt.Key_Backtab:
            self.ide.dedent()
            return
        super().keyPressEvent(event)

class TerminalWindow(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.itemClicked.connect(self.handle_item_clicked)
        self.user_scrolled = False
        self.verticalScrollBar().valueChanged.connect(self.on_scroll)

    def on_scroll(self, value):
        max_value = self.verticalScrollBar().maximum()
        if value < max_value:
            self.user_scrolled = True
        else:
            self.user_scrolled = False

    def log(self, message, level="INFO"):
        if (level == "INFO" and self.parent().parent().settings["show_info"]) or \
           (level == "ERROR" and self.parent().parent().settings["show_errors"]):
            self.addItem(f"[{level}] {message}")
            if not self.user_scrolled or self.verticalScrollBar().value() == self.verticalScrollBar().maximum():
                self.scrollToBottom()
                self.user_scrolled = False

    def show_context_menu(self, position):
        menu = QMenu()
        copy_line = menu.addAction("Copy Line")
        copy_all = menu.addAction("Copy All")
        clear = menu.addAction("Clear Terminal")
        action = menu.exec_(self.mapToGlobal(position))
        if action == copy_line:
            current_item = self.currentItem()
            if current_item:
                QApplication.clipboard().setText(current_item.text())
        elif action == copy_all:
            text = "\n".join([self.item(i).text() for i in range(self.count())])
            QApplication.clipboard().setText(text)
        elif action == clear:
            self.clear()
            self.parent().parent().terminal.log("Terminal cleared", "INFO")

    def handle_item_clicked(self, item):
        text = item.text()
        if "http://" in text or "https://" in text:
            url = QUrl(text.split()[-1])
            if url.isValid():
                QDesktopServices.openUrl(url)

class LicenseDialog(QDialog):
    def __init__(self, license_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("License")
        self.setMinimumSize(600, 400)
        layout = QVBoxLayout()
        self.text_browser = QTextBrowser()
        self.text_browser.setReadOnly(True)
        self.text_browser.setOpenExternalLinks(False)
        self.text_browser.anchorClicked.connect(self.open_url)
        self.text_browser.setHtml(self.convert_urls_to_html(license_text))
        self.text_browser.setFont(QFont("Arial", parent.settings["font_size"]))
        layout.addWidget(self.text_browser)
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        self.setLayout(layout)

    def convert_urls_to_html(self, text):
        url_pattern = r'(https?://[^\s<>"]+|www\.[^\s<>"]+)'
        parts = re.split(url_pattern, text)
        html_text = ""
        for part in parts:
            if re.match(url_pattern, part):
                html_text += f'<a href="{part}" style="color: blue; text-decoration: underline;">{part}</a>'
            else:
                html_text += html.escape(part).replace('\n', '<br>')
        return f'<div style="font-family: Arial; font-size: {self.parent().settings["font_size"]}pt;">{html_text}</div>'

    def open_url(self, url):
        qurl = QUrl(url)
        if qurl.isValid():
            QDesktopServices.openUrl(qurl)
            self.parent().terminal.log(f"Opened URL: {url.toString()}", "INFO")
        else:
            self.parent().terminal.log(f"Invalid URL clicked: {url.toString()}", "ERROR")

class BackgroundWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.background_image = None
        self.image_path = resource_path("GCstudio.png")
        self.parent_terminal = parent.terminal
        if os.path.exists(self.image_path):
            self.background_image = QImage(self.image_path)
            if self.background_image.isNull():
                self.parent_terminal.log(f"Failed to load image content from {self.image_path}: QImage is null", "ERROR")
        else:
            self.parent_terminal.log(f"Background image not found at {self.image_path}, using fallback text", "ERROR")
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Check if any tabs (files) are open
        if self.parent().tabs.count() == 0:  # No files open, show the image
            if self.background_image and not self.background_image.isNull():
                # Scale the image to fit while maintaining aspect ratio
                scaled_image = self.background_image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                # Center the image in the widget
                image_rect = scaled_image.rect()
                image_rect.moveCenter(self.rect().center())
                painter.drawImage(image_rect, scaled_image)
            else:
                # Fallback text if image fails to load
                painter.fillRect(self.rect(), Qt.white)
                font = QFont("Arial", 48, QFont.Bold)
                painter.setFont(font)
                text = "GCBASIC Essential IDE"
                text_rect = painter.fontMetrics().boundingRect(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, text)
                text_rect.moveCenter(self.rect().center())
                painter.setPen(Qt.black if self.parent().settings["theme"] == "light" else Qt.white)
                painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, text)
        else:  # Files are open, show a solid background
            bg_color = Qt.white if self.parent().settings["theme"] == "light" else Qt.darkGray
            painter.fillRect(self.rect(), bg_color)

        painter.end()

class IDE(QMainWindow):
    def __init__(self, filename=None):
        super().__init__()
        self.setWindowTitle("GCBASIC Essential IDE")
        self.settings = {
            "theme": "light",
            "line_numbers": True,
            "word_wrap": False,
            "show_info": True,
            "show_errors": True,
            "save_confirmation": True,
            "window_size": [800, 600],
            "window_position": [0, 0],
            "font_size": 12,
            "indent_size": 4,
            "recent_files": [],
            "goto_marker_duration": 3,
            "show_terminal": True,
            "terminal_size_percentage": 30
        }
        self.recent_files = []
        self.file_cache = {}
        self.history = {}
        self.file_states = {}
        self.file_menu = None
        self.last_search = None

        # Initialize terminal first
        self.terminal = TerminalWindow()
        self.dock = QDockWidget("Terminal", self)
        self.dock.setWidget(self.terminal)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)

        # Initialize background widget after terminal
        self.background_widget = BackgroundWidget(self)
        self.setCentralWidget(self.background_widget)
        self.central_layout = QVBoxLayout(self.background_widget)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)

        # Initialize tabs
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.setStyleSheet("QTabWidget::pane { background: transparent; border: 0; } "
                               "QTabBar::tab { background: transparent; } "
                               "QTabWidget > QWidget > QWidget { background: transparent; } "
                               "QTextEdit { background: transparent; }")
        self.central_layout.addWidget(self.tabs, 1)

        # Connect tab count changes to update background
        self.tabs.tabBar().tabBarClicked.connect(self.update_background)
        self.tabs.tabCloseRequested.connect(self.update_background_after_close)

        icon_path = resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            self.terminal.log(f"Application icon not found at {icon_path}", "ERROR")

        self.init_ui()
        self.load_settings()
        self.apply_theme()
        self.apply_terminal_settings()
        if filename and os.path.exists(filename):
            self.open_file_by_path(filename)

        # Force a repaint of the background widget
        self.background_widget.update()

    def update_background(self, index=None):
        self.background_widget.update()

    def update_background_after_close(self, index):
        self.close_tab(index)
        self.background_widget.update()

    def normalize_path(self, path):
        return os.path.normpath(os.path.abspath(path)).lower()

    def init_ui(self):
        menubar = self.menuBar()
        self.file_menu = menubar.addMenu("&File")
        edit_menu = menubar.addMenu("&Edit")
        settings_menu = menubar.addMenu("&IDE Settings")
        help_menu = menubar.addMenu("&Help")

        # File Menu
        new_action = QAction("&New", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.new_file)
        self.file_menu.addAction(new_action)

        open_action = QAction("&Open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file)
        self.file_menu.addAction(open_action)

        save_action = QAction("&Save", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_file)
        self.file_menu.addAction(save_action)

        save_all_action = QAction("Save &All", self)
        save_all_action.setShortcut("Ctrl+Shift+S")
        save_all_action.triggered.connect(self.save_all)
        self.file_menu.addAction(save_all_action)

        print_action = QAction("&Print", self)
        print_action.setShortcut("Ctrl+P")
        print_action.triggered.connect(self.print_file)
        self.file_menu.addAction(print_action)

        close_action = QAction("&Close File", self)
        close_action.setShortcut("Ctrl+W")
        close_action.triggered.connect(self.close_current_file)
        self.file_menu.addAction(close_action)

        recent_action = QAction("&Recent Files", self)
        recent_action.setShortcut("Ctrl+R")
        recent_action.triggered.connect(self.show_recent_files)
        self.file_menu.addAction(recent_action)

        exit_action = QAction("&Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        self.file_menu.addAction(exit_action)

        # Edit Menu
        undo_action = QAction("&Undo", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self.undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut("Ctrl+Y")
        redo_action.triggered.connect(self.redo)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        cut_action = QAction("Cu&t", self)
        cut_action.setShortcut("Ctrl+X")
        cut_action.triggered.connect(self.cut)
        edit_menu.addAction(cut_action)

        copy_action = QAction("&Copy", self)
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(self.copy)
        edit_menu.addAction(copy_action)

        paste_action = QAction("&Paste", self)
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(self.paste)
        edit_menu.addAction(paste_action)

        edit_menu.addSeparator()

        find_action = QAction("&Find", self)
        find_action.setShortcut("Ctrl+F")
        find_action.triggered.connect(self.find)
        edit_menu.addAction(find_action)

        find_next_action = QAction("Find &Next", self)
        find_next_action.setShortcut("F3")
        find_next_action.triggered.connect(self.find_next)
        edit_menu.addAction(find_next_action)

        find_previous_action = QAction("Find &Previous", self)
        find_previous_action.setShortcut("Shift+F3")
        find_previous_action.triggered.connect(self.find_previous)
        edit_menu.addAction(find_previous_action)

        search_replace_action = QAction("&Search and Replace", self)
        search_replace_action.setShortcut("Ctrl+H")
        search_replace_action.triggered.connect(self.search_and_replace)
        edit_menu.addAction(search_replace_action)

        edit_menu.addSeparator()

        case_action = QAction("Toggle &Case", self)
        case_action.triggered.connect(self.toggle_case)
        edit_menu.addAction(case_action)

        goto_action = QAction("&Go to Line", self)
        goto_action.setShortcut("Ctrl+G")
        goto_action.triggered.connect(self.goto_line)
        edit_menu.addAction(goto_action)

        comment_action = QAction("Toggle Co&mment", self)
        comment_action.setShortcut("Ctrl+/")
        comment_action.triggered.connect(self.toggle_comment)
        edit_menu.addAction(comment_action)

        # Settings Menu
        appearance_menu = settings_menu.addMenu("&Appearance")
        editor_menu = settings_menu.addMenu("&Editor")
        logging_menu = settings_menu.addMenu("&Logging")
        recent_files_menu = settings_menu.addMenu("&Recent Files")

        # Appearance Submenu
        theme_action = QAction("&Theme", self)
        theme_action.triggered.connect(self.set_theme)
        appearance_menu.addAction(theme_action)

        font_action = QAction("&Font Size", self)
        font_action.triggered.connect(self.set_font_size)
        appearance_menu.addAction(font_action)

        screen_size_action = QAction("&Screen Size and Position", self)
        screen_size_action.triggered.connect(self.set_screen_size_and_position)
        appearance_menu.addAction(screen_size_action)

        # Editor Submenu
        indent_size_action = QAction("&Indent Size", self)
        indent_size_action.triggered.connect(self.set_indent_size)
        editor_menu.addAction(indent_size_action)

        line_numbers_action = QAction("Show &Line Numbers", self)
        line_numbers_action.setCheckable(True)
        line_numbers_action.setChecked(self.settings["line_numbers"])
        line_numbers_action.triggered.connect(self.toggle_line_numbers)
        editor_menu.addAction(line_numbers_action)

        word_wrap_action = QAction("&Word Wrap", self)
        word_wrap_action.setCheckable(True)
        word_wrap_action.setChecked(self.settings["word_wrap"])
        word_wrap_action.triggered.connect(self.toggle_word_wrap)
        editor_menu.addAction(word_wrap_action)

        save_conf_action = QAction("Save &Confirmation", self)
        save_conf_action.setCheckable(True)
        save_conf_action.setChecked(self.settings["save_confirmation"])
        save_conf_action.triggered.connect(self.toggle_save_confirmation)
        editor_menu.addAction(save_conf_action)

        marker_duration_action = QAction("Goto Marker Duration", self)
        marker_duration_action.triggered.connect(self.set_goto_marker_duration)
        editor_menu.addAction(marker_duration_action)

        # Logging Submenu
        info_action = QAction("Toggle Info Logs", self)
        info_action.setCheckable(False)
        info_action.triggered.connect(self.toggle_info_logs)
        logging_menu.addAction(info_action)

        error_action = QAction("Toggle Error Logs", self)
        error_action.setCheckable(False)
        error_action.triggered.connect(self.toggle_error_logs)
        logging_menu.addAction(error_action)

        show_terminal_action = QAction("Toggle Terminal", self)
        show_terminal_action.setCheckable(False)
        show_terminal_action.triggered.connect(self.toggle_terminal)
        logging_menu.addAction(show_terminal_action)

        terminal_size_action = QAction("Terminal Size", self)
        terminal_size_action.triggered.connect(self.set_terminal_size)
        logging_menu.addAction(terminal_size_action)

        # Recent Files Submenu
        recent_files_menu.addSeparator()
        clear_recent_action = QAction("&Clear Recent Files", self)
        clear_recent_action.triggered.connect(self.clear_recent_files)
        recent_files_menu.addAction(clear_recent_action)

        # Help Menu
        about_action = QAction("&About", self)
        about_action.triggered.connect(lambda: QMessageBox.information(self, "About", "GCBASIC Essential IDE v1.0"))
        help_menu.addAction(about_action)

        license_action = QAction("&License", self)
        license_action.triggered.connect(self.show_license)
        help_menu.addAction(license_action)

        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self.show_tab_context_menu)
        self.tabs.installEventFilter(self)

    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)

    def show_tab_context_menu(self, position):
        menu = QMenu()
        copy_path = menu.addAction("Copy Path")
        action = menu.exec_(self.tabs.mapToGlobal(position))
        if action == copy_path:
            current_tab = self.tabs.currentWidget()
            if current_tab and hasattr(current_tab, "file_path"):
                if current_tab.file_path.startswith("untitled_"):
                    pass
                else:
                    full_path = os.path.normpath(os.path.abspath(current_tab.file_path))
                    QApplication.clipboard().setText(full_path)

    def show_license(self):
        license_file = "license.txt"
        license_text = None
        encodings = ["utf-8", "windows-1252", "latin1"]
        if os.path.exists(license_file):
            for encoding in encodings:
                try:
                    with open(license_file, "r", encoding=encoding) as f:
                        license_text = f.read()
                    break
                except UnicodeDecodeError as e:
                    self.terminal.log(f"Failed to read license file with {encoding} encoding: {str(e)}", "ERROR")
                except Exception as e:
                    self.terminal.log(f"Unexpected error reading license file: {str(e)}", "ERROR")
                    license_text = f"Error reading license file: {str(e)}"
                    break
            if license_text is None:
                license_text = "Error: Unable to decode license file. Please ensure it is in a compatible text format."
                self.terminal.log("All encoding attempts failed for license file", "ERROR")
        else:
            license_text = "GPL License. Evan R. Venn 2025"
        dialog = LicenseDialog(license_text, self)
        dialog.exec_()

    def new_file(self):
        text_edit = CustomTextEdit(self)
        gcbasic_header = "/*\n    A GCBASIC source program\n*/\n\n#CHIP {specific your chip, removing the braces}\n#OPTION EXPLICIT\n\n  Do\n    PulseOut PORTB.5, 100 ms\n    Wait 100 ms\n  Loop\n"
        text_edit.setDocument(QTextDocument(gcbasic_header))
        text_edit.file_path = f"untitled_{uuid.uuid4().hex[:8]}.gcb"
        text_edit.textChanged.connect(lambda: self.record_history(text_edit))
        self.tabs.addTab(text_edit, "untitled.gcb")
        self.tabs.setCurrentWidget(text_edit)
        self.apply_text_settings(text_edit)
        self.background_widget.update()  # Update background when a new file is opened

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "GCB Files (*.GCB);;Text Files (*.txt);;CSV Files (*.csv)")
        if file_path:
            self.open_file_by_path(file_path)

    def save_file(self):
        current_tab = self.tabs.currentWidget()
        if current_tab and hasattr(current_tab, "file_path"):
            if current_tab.file_path.startswith("untitled_"):
                file_path, _ = QFileDialog.getSaveFileName(self, "Save File", "", "GCB Files (*.GCB);;Text Files (*.txt)")
                if not file_path:
                    return
                current_tab.file_path = os.path.abspath(file_path)
            try:
                with open(current_tab.file_path, "w") as f:
                    f.write(current_tab.toPlainText())
                current_tab.document().setModified(False)
                self.file_cache[current_tab.file_path] = current_tab.toPlainText()
                self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(current_tab.file_path))
                normalized_path = self.normalize_path(current_tab.file_path)
                if normalized_path in [self.normalize_path(p) for p in self.recent_files]:
                    self.recent_files = [p for p in self.recent_files if self.normalize_path(p) != normalized_path]
                self.recent_files.insert(0, current_tab.file_path)
                if len(self.recent_files) > 10:
                    self.recent_files.pop()
                self.save_settings()
            except Exception as e:
                self.terminal.log(f"Error saving {current_tab.file_path}: {str(e)}", "ERROR")

    def save_all(self):
        saved_count = 0
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if tab and hasattr(tab, "file_path") and tab.document().isModified():
                self.tabs.setCurrentWidget(tab)
                self.save_file()
                if not tab.document().isModified():
                    saved_count += 1

    def print_file(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            self.terminal.log("No file open to print", "ERROR")
            return
        printer = QPrinter()
        print_dialog = QPrintDialog(printer, self)
        if print_dialog.exec_() == QPrintDialog.Accepted:
            current_tab.document().print_(printer)

    def close_tab(self, index):
        tab = self.tabs.widget(index)
        if tab and hasattr(tab, "file_path") and self.settings["save_confirmation"] and tab.document().isModified():
            self.terminal.log(f"Prompting save for {tab.file_path} (modified: {tab.document().isModified()})", "INFO")
            reply = QMessageBox.question(self, "Save File", f"Save {tab.file_path} before closing?",
                                         QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if reply == QMessageBox.Yes:
                self.save_file()
            elif reply == QMessageBox.Cancel:
                return
        self.tabs.removeTab(index)
        self.background_widget.update()  # Update background when a tab is closed

    def close_current_file(self):
        current_index = self.tabs.currentIndex()
        if current_index != -1:
            self.close_tab(current_index)

    def check_file_changes(self, text_edit):
        if not hasattr(text_edit, "file_path") or text_edit.file_path.startswith("untitled_"):
            return
        file_path = text_edit.file_path
        try:
            current_mtime = os.path.getmtime(file_path)
            if file_path in self.file_states:
                last_mtime, user_choice = self.file_states[file_path]
                if current_mtime > last_mtime and text_edit.document().isModified():
                    reply = QMessageBox.question(self, "File Changed",
                                                f"{file_path} has been modified externally. Reload?",
                                                QMessageBox.Yes | QMessageBox.No)
                    if reply == QMessageBox.Yes or user_choice == "reload":
                        with open(file_path, "r") as f:
                            content = f.read()
                        text_edit.setPlainText(content)
                        text_edit.document().setModified(False)
                        self.file_cache[file_path] = content
                    self.file_states[file_path] = (current_mtime, user_choice or ("reload" if reply == QMessageBox.Yes else "ignore"))
                else:
                    self.file_states[file_path] = (current_mtime, user_choice)
            else:
                self.file_states[file_path] = (current_mtime, None)
        except Exception as e:
            self.terminal.log(f"Error checking {file_path}: {str(e)}", "ERROR")

    def record_history(self, text_edit):
        if not hasattr(text_edit, "file_path"):
            return
        file_path = text_edit.file_path
        if file_path not in self.history:
            self.history[file_path] = deque(maxlen=100)
        self.history[file_path].append(text_edit.toPlainText())

    def show_recent_files(self):
        menu = QMenu()
        menu.setFont(QFont("Arial", self.settings["font_size"]))
        seen = set()
        for index, file in enumerate(self.recent_files[:10], 1):
            normalized_file = self.normalize_path(file)
            if normalized_file not in seen and os.path.exists(file):
                action = menu.addAction(f"{index}. {os.path.basename(file)}")
                action.setFont(QFont("Arial", self.settings["font_size"]))
                action.setToolTip(file)
                action.triggered.connect(lambda checked, f=file: self.open_file_by_path(f))
                seen.add(normalized_file)
        if not seen:
            action = menu.addAction("No recent files")
            action.setFont(QFont("Arial", self.settings["font_size"]))
            action.setEnabled(False)
        pos = self.mapToGlobal(QPoint(0, self.menuBar().height()))
        menu.exec_(pos)

    def show_recent_files_in_settings(self):
        menu = QMenu()
        menu.setFont(QFont("Arial", self.settings["font_size"]))
        seen = set()
        for index, file in enumerate(self.recent_files[:10], 1):
            normalized_file = self.normalize_path(file)
            if normalized_file not in seen and os.path.exists(file):
                action = menu.addAction(f"{index}. {os.path.basename(file)}")
                action.setFont(QFont("Arial", self.settings["font_size"]))
                action.setToolTip(file)
                action.triggered.connect(lambda checked, f=file: self.open_file_by_path(f))
                seen.add(normalized_file)
        if not seen:
            action = menu.addAction("No recent files")
            action.setFont(QFont("Arial", self.settings["font_size"]))
            action.setEnabled(False)
        pos = self.sender().mapToGlobal(QPoint(0, self.sender().height()))
        menu.exec_(pos)

    def open_file_by_path(self, file_path):
        if not os.path.exists(file_path):
            self.terminal.log(f"File {file_path} does not exist", "ERROR")
            normalized_path = self.normalize_path(file_path)
            if normalized_path in [self.normalize_path(p) for p in self.recent_files]:
                self.recent_files = [p for p in self.recent_files if self.normalize_path(p) != normalized_path]
                self.save_settings()
            return
        normalized_path = self.normalize_path(file_path)
        for i in range(self.tabs.count()):
            if self.normalize_path(self.tabs.widget(i).file_path) == normalized_path:
                self.tabs.setCurrentWidget(self.tabs.widget(i))
                return
        if file_path in self.file_cache:
            content = self.file_cache[file_path]
        else:
            try:
                with open(file_path, "r") as f:
                    content = f.read()
                self.file_cache[file_path] = content
            except Exception as e:
                self.terminal.log(f"Error opening {file_path}: {str(e)}", "ERROR")
                return
        text_edit = CustomTextEdit(self)
        text_edit.setDocument(QTextDocument(content))
        text_edit.file_path = os.path.abspath(file_path)
        text_edit.textChanged.connect(lambda: self.record_history(text_edit))
        self.check_file_changes(text_edit)
        text_edit.document().setModified(False)
        self.tabs.addTab(text_edit, os.path.basename(file_path))
        self.tabs.setCurrentWidget(text_edit)
        self.apply_text_settings(text_edit)
        if normalized_path in [self.normalize_path(p) for p in self.recent_files]:
            self.recent_files = [p for p in self.recent_files if self.normalize_path(p) != normalized_path]
        self.recent_files.insert(0, file_path)
        if len(self.recent_files) > 10:
            self.recent_files.pop()
        self.save_settings()
        self.background_widget.update()  # Update background when a file is opened

    def clear_recent_files(self):
        self.recent_files.clear()
        self.save_settings()

    def undo(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.undo()

    def redo(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.redo()

    def cut(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.cut()

    def copy(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.copy()

    def paste(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.paste()

    def find(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            self.terminal.log("No file open for find", "ERROR")
            return
        search, ok = QInputDialog.getText(self, "Find", "Search for:", text=self.last_search or "")
        if ok and search:
            self.last_search = search
            cursor = current_tab.textCursor()
            cursor.clearSelection()
            current_tab.setTextCursor(cursor)
            if cursor.document().find(search, cursor).hasSelection():
                current_tab.setTextCursor(cursor.document().find(search, cursor))
            else:
                pass

    def find_next(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab or not self.last_search:
            self.terminal.log("No search term or file open for Find Next", "ERROR")
            return
        cursor = current_tab.textCursor()
        next_cursor = cursor.document().find(self.last_search, cursor)
        if next_cursor.hasSelection():
            current_tab.setTextCursor(next_cursor)
        else:
            self.terminal.log(f"No more occurrences of '{self.last_search}' found", "INFO")

    def find_previous(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab or not self.last_search:
            self.terminal.log("No search term or file open for Find Previous", "ERROR")
            return
        cursor = current_tab.textCursor()
        prev_cursor = cursor.document().find(self.last_search, cursor, QTextDocument.FindBackward)
        if prev_cursor.hasSelection():
            current_tab.setTextCursor(prev_cursor)
        else:
            self.terminal.log(f"No previous occurrences of '{self.last_search}' found", "INFO")

    def search_and_replace(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            self.terminal.log("No file open for search and replace", "ERROR")
            return
        search, ok = QInputDialog.getText(self, "Search and Replace", "Search for:")
        if ok and search:
            replace, ok = QInputDialog.getText(self, "Search and Replace", "Replace with:")
            if ok:
                content = current_tab.toPlainText()
                new_content = content.replace(search, replace)
                if new_content != content:
                    current_tab.setPlainText(new_content)
                else:
                    pass

    def toggle_case(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            cursor = current_tab.textCursor()
            if cursor.hasSelection():
                text = cursor.selectedText()
                if text.startswith("// "):
                    new_text = text[3:]
                    cursor.insertText(new_text)
                else:
                    if text.isupper():
                        cursor.insertText(text.lower())
                    else:
                        cursor.insertText(text.upper())
            else:
                pass

    def goto_line(self):
        if self.settings["word_wrap"]:
            self.terminal.log("Go to Line is disabled when word wrap is enabled", "INFO")
            return
        current_tab = self.tabs.currentWidget()
        if current_tab:
            doc = current_tab.document()
            total_lines = doc.blockCount()
            line, ok = QInputDialog.getInt(self, "Go to Line", f"Line number (1-{total_lines}):", 1, 1, total_lines)
            if ok:
                block = doc.findBlockByLineNumber(line - 1)
                if block.isValid():
                    cursor = current_tab.textCursor()
                    cursor.setPosition(block.position())
                    current_tab.setTextCursor(cursor)
                    current_tab.ensureCursorVisible()
                    current_tab.line_number_area.set_marker(line - 1)
                else:
                    self.terminal.log(f"Line {line} is out of range", "ERROR")

    def toggle_comment(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            return
        cursor = current_tab.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            start_pos = cursor.selectionStart()
            end_pos = cursor.selectionEnd()
            cursor.setPosition(start_pos)
            cursor.setPosition(end_pos, cursor.KeepAnchor)
            selected_text = cursor.selectedText()
            lines = selected_text.split('\n')
            if len(lines) > 1 or (len(lines) == 1 and selected_text.startswith("/* ") and selected_text.endswith(" */")):
                if selected_text.startswith("/* ") and selected_text.endswith(" */"):
                    new_text = selected_text[3:-3]
                else:
                    new_text = "/* " + selected_text + "*/"
                cursor.insertText(new_text)
            else:
                if selected_text.startswith("/* ") and selected_text.endswith(" */"):
                    cursor.setPosition(start_pos)
                    cursor.movePosition(cursor.Right, cursor.KeepAnchor, 3)
                    cursor.removeSelectedText()
                    cursor.setPosition(end_pos - 3)
                    cursor.movePosition(cursor.Right, cursor.KeepAnchor, 3)
                    cursor.removeSelectedText()
                else:
                    cursor.setPosition(start_pos)
                    cursor.insertText("/* ")
                    cursor.setPosition(end_pos + 3)
                    cursor.insertText("*/")
        else:
            cursor.movePosition(cursor.StartOfBlock)
            line_start = cursor.position()
            cursor.movePosition(cursor.EndOfBlock, cursor.KeepAnchor)
            line_text = cursor.selectedText()
            stripped = line_text.lstrip()
            leading_spaces = len(line_text) - len(stripped)
            if stripped.startswith("//"):
                comment_start = line_start + leading_spaces
                cursor.setPosition(comment_start)
                cursor.movePosition(cursor.Right, cursor.KeepAnchor, 3)
                cursor.removeSelectedText()
            else:
                cursor.setPosition(line_start + leading_spaces)
                cursor.insertText("// ")
        cursor.endEditBlock()

    def indent(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            return
        cursor = current_tab.textCursor()
        indent_size = self.settings["indent_size"]
        cursor.beginEditBlock()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            cursor.setPosition(start)
            start_block = cursor.block()
            cursor.setPosition(end)
            end_block = cursor.block()
            cursor.setPosition(start)
            while cursor.block() != end_block.next():
                cursor.movePosition(cursor.StartOfLine)
                cursor.insertText(" " * indent_size)
                if not cursor.movePosition(cursor.NextBlock):
                    break
                end += indent_size
            cursor.setPosition(start)
            cursor.setPosition(end + indent_size, cursor.KeepAnchor)
        else:
            cursor.insertText(" " * indent_size)
        cursor.endEditBlock()

    def dedent(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            return
        cursor = current_tab.textCursor()
        indent_size = self.settings["indent_size"]
        cursor.beginEditBlock()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            cursor.setPosition(start)
            start_block = cursor.block()
            cursor.setPosition(end)
            end_block = cursor.block()
            cursor.setPosition(start)
            while cursor.block() != end_block.next():
                cursor.movePosition(cursor.StartOfLine)
                line_start = cursor.position()
                line_text = cursor.block().text()
                spaces_to_remove = 0
                for char in line_text[:indent_size]:
                    if char == " ":
                        spaces_to_remove += 1
                    else:
                        break
                cursor.setPosition(line_start)
                for _ in range(spaces_to_remove):
                    cursor.deleteChar()
                if not cursor.movePosition(cursor.NextBlock):
                    break
        else:
            cursor.movePosition(cursor.StartOfLine)
            line_start = cursor.position()
            line_text = cursor.block().text()
            spaces_to_remove = 0
            for char in line_text[:indent_size]:
                if char == " ":
                    spaces_to_remove += 1
                else:
                    break
            cursor.setPosition(line_start)
            for _ in range(spaces_to_remove):
                cursor.deleteChar()
        cursor.endEditBlock()

    def set_theme(self):
        themes = ["dark", "light"]
        theme, ok = QInputDialog.getItem(self, "Select Theme", "Theme:", themes, themes.index(self.settings["theme"]), False)
        if ok:
            self.settings["theme"] = theme
            self.apply_theme()
            self.background_widget.update()
            self.save_settings()

    def set_indent_size(self):
        sizes = ["2", "4", "8"]
        size, ok = QInputDialog.getItem(self, "Select Indent Size", "Spaces:", sizes, sizes.index(str(self.settings["indent_size"])), False)
        if ok:
            self.settings["indent_size"] = int(size)
            self.save_settings()

    def set_screen_size_and_position(self):
        screen = QApplication.primaryScreen().availableGeometry()
        max_width = screen.width()
        max_height = screen.height()
        width, ok_width = QInputDialog.getInt(self, "Screen Size", "Width (pixels):", self.settings["window_size"][0], 100, max_width)
        if ok_width:
            height, ok_height = QInputDialog.getInt(self, "Screen Size", "Height (pixels):", self.settings["window_size"][1], 100, max_height)
            if ok_height:
                x_pos, ok_x = QInputDialog.getInt(self, "Window Position", "X Position (pixels):", self.settings["window_position"][0], 0, max_width - width)
                if ok_x:
                    y_pos, ok_y = QInputDialog.getInt(self, "Window Position", "Y Position (pixels):", self.settings["window_position"][1], 0, max_height - height)
                    if ok_y:
                        self.settings["window_size"] = [width, height]
                        self.settings["window_position"] = [x_pos, y_pos]
                        self.apply_screen_size_and_position()
                        self.save_settings()

    def set_goto_marker_duration(self):
        duration, ok = QInputDialog.getInt(self, "Goto Marker Duration", "Duration in seconds (1-10):", self.settings["goto_marker_duration"], 1, 10)
        if ok:
            self.settings["goto_marker_duration"] = duration
            self.save_settings()

    def apply_screen_size_and_position(self):
        width, height = self.settings["window_size"]
        x_pos, y_pos = self.settings["window_position"]
        screen = QApplication.primaryScreen().availableGeometry()
        max_x = screen.width() - width
        max_y = screen.height() - height
        x_pos = max(0, min(x_pos, max_x))
        y_pos = max(0, min(y_pos, max_y))
        self.resize(width, height)
        self.move(x_pos, y_pos)
        self.apply_terminal_settings()

    def apply_theme(self):
        if self.settings["theme"] == "dark":
            fg_color = "#FFFFFF"
            ln_color = "#888888"
            menu_border = "#AAAAAA"
            menu_bg = "#3A3A3A"
        else:
            fg_color = "#000000"
            ln_color = "#888888"
            menu_border = "#666666"
            menu_bg = "#F0F0F0"
        self.setStyleSheet(f"color: {fg_color};")
        menu_style = f"QMenu {{ background-color: {menu_bg}; color: {fg_color}; border: 1px solid {menu_border}; }} QMenu::item:selected {{ background-color: {ln_color}; }}"
        for i in range(self.tabs.count()):
            text_edit = self.tabs.widget(i)
            text_edit.setStyleSheet(f"background: transparent; color: {fg_color};")
            text_edit.line_number_area.update()
        self.terminal.setStyleSheet(f"background-color: {menu_bg}; color: {fg_color};")
        self.dock.setStyleSheet(f"background-color: {menu_bg}; color: {fg_color};")
        QApplication.instance().setStyleSheet(menu_style)
        self.background_widget.update()

    def set_font_size(self):
        size, ok = QInputDialog.getInt(self, "Font Size", "Enter font size (8-24):", self.settings["font_size"], 8, 24)
        if ok:
            self.settings["font_size"] = size
            self.apply_text_settings()
            self.save_settings()

    def toggle_line_numbers(self):
        self.settings["line_numbers"] = not self.settings["line_numbers"]
        self.apply_text_settings()
        self.save_settings()

    def toggle_word_wrap(self):
        self.settings["word_wrap"] = not self.settings["word_wrap"]
        self.apply_text_settings()
        self.save_settings()

    def toggle_save_confirmation(self):
        self.settings["save_confirmation"] = not self.settings["save_confirmation"]
        self.save_settings()

    def toggle_info_logs(self):
        self.settings["show_info"] = not self.settings["show_info"]
        self.save_settings()

    def toggle_error_logs(self):
        self.settings["show_errors"] = not self.settings["show_errors"]
        self.save_settings()

    def toggle_terminal(self):
        self.settings["show_terminal"] = not self.settings["show_terminal"]
        self.apply_terminal_settings()
        self.save_settings()

    def set_terminal_size(self):
        size, ok = QInputDialog.getInt(self, "Terminal Size", "Height percentage (10-90):", self.settings["terminal_size_percentage"], 10, 90)
        if ok:
            self.settings["terminal_size_percentage"] = size
            self.apply_terminal_settings()
            self.save_settings()

    def apply_terminal_settings(self):
        if self.settings["show_terminal"]:
            self.dock.show()
            window_height = self.height()
            terminal_height = int(window_height * (self.settings["terminal_size_percentage"] / 100.0))
            self.resizeDocks([self.dock], [terminal_height], Qt.Vertical)
        else:
            self.dock.hide()

    def apply_text_settings(self, text_edit=None):
        font = QFont("Arial", self.settings["font_size"])
        fg_color = "#FFFFFF" if self.settings["theme"] == "dark" else "#000000"
        QApplication.instance().setFont(font)
        self.menuBar().setFont(font)
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu:
                menu.setFont(font)
                for sub_action in menu.actions():
                    sub_menu = sub_action.menu()
                    if sub_menu:
                        sub_menu.setFont(font)
        self.tabs.setFont(font)
        self.terminal.setFont(font)
        if text_edit is None:
            for i in range(self.tabs.count()):
                self.apply_text_settings(self.tabs.widget(i))
        else:
            text_edit.setFont(font)
            if self.settings["word_wrap"]:
                text_edit.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            else:
                text_edit.setWordWrapMode(QTextOption.NoWrap)
            text_edit.update_line_number_area_width()
            text_edit.setStyleSheet(f"background: transparent; color: {fg_color};")
            text_edit.line_number_area.update()

    def get_settings_path(self):
        config_dir = os.path.expanduser("~/.superide")
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        return os.path.join(config_dir, "ide_settings.json")

    def load_settings(self):
        settings_path = self.get_settings_path()
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                loaded_settings = json.load(f)
                # Ensure all settings are loaded, including logging options
                for key in self.settings.keys():
                    if key in loaded_settings:
                        self.settings[key] = loaded_settings[key]
                # Handle recent files
                raw_recent_files = self.settings.get("recent_files", [])
                normalized_files = []
                seen = set()
                for file in raw_recent_files:
                    normalized = self.normalize_path(file)
                    if normalized not in seen and os.path.exists(file):
                        normalized_files.append(file)
                        seen.add(normalized)
                self.recent_files = normalized_files[:10]
                self.settings["recent_files"] = self.recent_files
                # Apply all settings
                self.apply_screen_size_and_position()
                self.apply_text_settings()
                self.apply_theme()
                self.apply_terminal_settings()
        except FileNotFoundError:
            self.set_default_geometry()
        except json.JSONDecodeError:
            self.terminal.log("Invalid settings file, using defaults", "ERROR")
            self.set_default_geometry()
        except Exception as e:
            self.terminal.log(f"Error loading settings: {str(e)}", "ERROR")
            self.set_default_geometry()

    def set_default_geometry(self):
        screen = QApplication.primaryScreen().availableGeometry()
        width = int(screen.width() * 0.75)
        height = int(screen.height() * 0.75)
        self.settings["window_size"] = [width, height]
        self.settings["window_position"] = [0, 0]
        self.resize(width, height)
        self.move(0, 0)

    def save_settings(self):
        # Ensure all settings are saved, including recent files
        self.settings["recent_files"] = self.recent_files
        self.settings["window_size"] = [self.width(), self.height()]
        self.settings["window_position"] = [self.pos().x(), self.pos().y()]
        settings_path = self.get_settings_path()
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
            os.chmod(settings_path, 0o600)
        except Exception as e:
            self.terminal.log(f"Error saving settings: {str(e)}", "ERROR")

    def closeEvent(self, event):
        if self.settings["save_confirmation"]:
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if tab.document().isModified():
                    self.terminal.log(f"Prompting save for {tab.file_path} (modified: {tab.document().isModified()})", "INFO")
                    reply = QMessageBox.question(self, "Save File", f"Save {tab.file_path} before closing?",
                                                 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
                    if reply == QMessageBox.Yes:
                        self.save_file()
                    elif reply == QMessageBox.Cancel:
                        event.ignore()
                        return
                else:
                    self.terminal.log(f"No save prompt for {tab.file_path} (modified: {tab.document().isModified()})", "INFO")
        self.save_settings()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    filename = sys.argv[1] if len(sys.argv) > 1 else None
    ide = IDE(filename)
    ide.show()
    sys.exit(app.exec_())