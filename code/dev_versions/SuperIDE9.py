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
from PyQt5.QtGui import QTextOption, QTextDocument, QFont, QPainter, QFontMetrics, QTextCursor, QIcon, QTextCharFormat, QColor
from PyQt5.QtCore import Qt, QUrl, QPoint, QTimer
from PyQt5.QtGui import QDesktopServices
from collections import deque
import uuid

# Helper function to get the base path for resources
def resource_path(relative_path):
    """Get the absolute path to a resource, works for dev and PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        # When running as a PyInstaller bundle, use the temp directory
        return os.path.join(sys._MEIPASS, relative_path)
    else:
        # When running as a script, use the script's directory
        return os.path.join(os.path.dirname(__file__), relative_path)

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
        self.timer.start(self.editor.ide.settings["goto_marker_duration"] * 1000)  # Convert seconds to milliseconds
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
                        # Draw triangle marker (pointing east)
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

        # Scrollable text area for license with HTML support
        self.text_browser = QTextBrowser()
        self.text_browser.setReadOnly(True)
        self.text_browser.setOpenExternalLinks(False)  # Handle links manually
        self.text_browser.anchorClicked.connect(self.open_url)
        self.text_browser.setHtml(self.convert_urls_to_html(license_text))
        self.text_browser.setFont(QFont("Arial", parent.settings["font_size"]))
        layout.addWidget(self.text_browser)

        # Close button
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

    def convert_urls_to_html(self, text):
        # Regex to match URLs starting with http:// or https://
        url_pattern = r'(https?://[^\s<>"]+|www\.[^\s<>"]+)'
        # Escape non-URL text to prevent HTML injection
        parts = re.split(url_pattern, text)
        html_text = ""
        for part in parts:
            if re.match(url_pattern, part):
                # Convert URL to clickable link
                html_text += f'<a href="{part}" style="color: blue; text-decoration: underline;">{part}</a>'
            else:
                # Escape non-URL text
                html_text += html.escape(part).replace('\n', '<br>')
        return f'<div style="font-family: Arial; font-size: {self.parent().settings["font_size"]}pt;">{html_text}</div>'

    def open_url(self, url):
        qurl = QUrl(url)
        if qurl.isValid():
            QDesktopServices.openUrl(qurl)
            self.parent().terminal.log(f"Opened URL: {url.toString()}", "INFO")
        else:
            self.parent().terminal.log(f"Invalid URL clicked: {url.toString()}", "ERROR")

class IDE(QMainWindow):
    def __init__(self, filename=None):
        super().__init__()
        self.setWindowTitle("GCBASIC Essential IDE")

        # Initialize settings first to ensure they are available
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
            "goto_marker_duration": 3  # New setting for marker duration in seconds
        }
        self.recent_files = []
        self.file_cache = {}
        self.history = {}
        self.file_states = {}
        self.file_menu = None
        self.last_search = None  # Store last search term for Find Next/Previous

        # Initialize the terminal and dock widget after settings
        self.terminal = TerminalWindow()
        self.dock = QDockWidget("Terminal", self)
        self.dock.setWidget(self.terminal)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)

        # Set the window icon using the resource path
        icon_path = resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            # self.setWindowIcon(QIcon(icon_path))
            self.terminal.log(f"Application icon loading skipped: {icon_path}", "INFO")
        else:
            self.terminal.log(f"Application icon not found at {icon_path}", "ERROR")

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)

        self.init_ui()
        self.load_settings()
        self.apply_theme()
        if filename and os.path.exists(filename):
            self.open_file_by_path(filename)

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

        # Settings Menu with Submenus
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

        # Add setting for goto marker duration
        marker_duration_action = QAction("Goto Marker Duration", self)
        marker_duration_action.triggered.connect(self.set_goto_marker_duration)
        editor_menu.addAction(marker_duration_action)

        # Logging Submenu
        info_action = QAction("Show &Info Logs", self)
        info_action.setCheckable(True)
        info_action.setChecked(self.settings["show_info"])
        info_action.triggered.connect(self.toggle_info_logs)
        logging_menu.addAction(info_action)

        error_action = QAction("Show &Error Logs", self)
        error_action.setCheckable(True)
        error_action.setChecked(self.settings["show_errors"])
        error_action.triggered.connect(self.toggle_error_logs)
        logging_menu.addAction(error_action)

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
                    self.terminal.log("Cannot copy full path for unsaved file", "INFO")
                    QApplication.clipboard().setText(current_tab.file_path)
                else:
                    full_path = os.path.normpath(os.path.abspath(current_tab.file_path))
                    QApplication.clipboard().setText(full_path)
                    self.terminal.log(f"Copied path: {full_path}", "INFO")

    def show_license(self):
        license_file = "license.txt"
        license_text = None
        encodings = ["utf-8", "windows-1252", "latin1"]
        
        if os.path.exists(license_file):
            for encoding in encodings:
                try:
                    with open(license_file, "r", encoding=encoding) as f:
                        license_text = f.read()
                    self.terminal.log(f"Successfully read license file with {encoding} encoding", "INFO")
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
            self.terminal.log("License file not found, using default GPL text", "INFO")

        dialog = LicenseDialog(license_text, self)
        dialog.exec_()
        self.terminal.log("Displayed license dialog", "INFO")

    def new_file(self):
        text_edit = CustomTextEdit(self)
        gcbasic_header = "/*\n    A GCBASIC source program\n*/\n\n#CHIP {specific your chip, removing the braces}\n#OPTION EXPLICIT\n\n  Do\n    PulseOut PORTB.5, 100 ms\n    Wait 100 ms\n  Loop\n"
        text_edit.setDocument(QTextDocument(gcbasic_header))
        text_edit.file_path = f"untitled_{uuid.uuid4().hex[:8]}.gcb"
        text_edit.textChanged.connect(lambda: self.record_history(text_edit))
        self.tabs.addTab(text_edit, "untitled.gcb")
        self.tabs.setCurrentWidget(text_edit)
        self.apply_text_settings(text_edit)
        self.terminal.log("Created new GCBASIC file", "INFO")

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "GCB Files (*.GCB);;Text Files (*.txt);;CSV Files (*.csv)")
        if file_path:
            if file_path in [self.tabs.widget(i).file_path for i in range(self.tabs.count())]:
                self.terminal.log(f"File {file_path} is already open", "INFO")
                self.tabs.setCurrentWidget(self.tabs.widget([self.tabs.widget(i).file_path for i in range(self.tabs.count())].index(file_path)))
                return
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
                self.terminal.log(f"Saved {current_tab.file_path}", "INFO")
                self.file_cache[current_tab.file_path] = current_tab.toPlainText()
                self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(current_tab.file_path))
                if current_tab.file_path in self.recent_files:
                    self.recent_files.remove(current_tab.file_path)
                if current_tab.file_path not in self.recent_files:
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
                if not tab.document().isModified():  # Confirm save was successful
                    saved_count += 1
        if saved_count > 0:
            self.terminal.log(f"Saved {saved_count} modified file(s)", "INFO")
        else:
            self.terminal.log("No files needed saving", "INFO")

    def print_file(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            self.terminal.log("No file open to print", "ERROR")
            return
        printer = QPrinter()
        print_dialog = QPrintDialog(printer, self)
        if print_dialog.exec_() == QPrintDialog.Accepted:
            current_tab.document().print_(printer)
            self.terminal.log(f"Printed {current_tab.file_path}", "INFO")

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
        self.terminal.log(f"Closed file {tab.file_path}", "INFO")

    def close_current_file(self):
        current_index = self.tabs.currentIndex()
        if current_index != -1:  # Check if there is a current tab
            self.close_tab(current_index)
        else:
            self.terminal.log("No file open to close", "INFO")

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
                        self.terminal.log(f"Reloaded {file_path}", "INFO")
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
        for index, file in enumerate(self.recent_files[:10], 1):
            action = menu.addAction(f"{index}. {os.path.basename(file)}")
            action.setToolTip(file)
            action.triggered.connect(lambda checked, f=file: self.open_file_by_path(f))
        pos = self.mapToGlobal(QPoint(0, self.menuBar().height()))
        menu.exec_(pos)

    def show_recent_files_in_settings(self):
        menu = QMenu()
        if not self.recent_files:
            menu.addAction("No recent files").setEnabled(False)
        else:
            for index, file in enumerate(self.recent_files[:10], 1):
                action = menu.addAction(f"{index}. {os.path.basename(file)}")
                action.setToolTip(file)
                action.triggered.connect(lambda checked, f=file: self.open_file_by_path(f))
        pos = self.sender().mapToGlobal(QPoint(0, self.sender().height()))
        menu.exec_(pos)

    def open_file_by_path(self, file_path):
        if not os.path.exists(file_path):
            self.terminal.log(f"File {file_path} does not exist", "ERROR")
            if file_path in self.recent_files:
                self.recent_files.remove(file_path)
                self.save_settings()
            return
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).file_path == file_path:
                self.tabs.setCurrentWidget(self.tabs.widget(i))
                self.terminal.log(f"Switched to open file {file_path}", "INFO")
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
        self.terminal.log(f"Opened {file_path}", "INFO")
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
        if file_path not in self.recent_files:
            self.recent_files.insert(0, file_path)
        if len(self.recent_files) > 10:
            self.recent_files.pop()
        self.save_settings()

    def clear_recent_files(self):
        self.recent_files.clear()
        self.save_settings()
        self.terminal.log("Cleared recent files list", "INFO")

    def undo(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.undo()
            self.terminal.log("Undo performed", "INFO")

    def redo(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.redo()
            self.terminal.log("Redo performed", "INFO")

    def cut(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.cut()
            self.terminal.log("Text cut to clipboard", "INFO")

    def copy(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.copy()
            self.terminal.log("Text copied to clipboard", "INFO")

    def paste(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.paste()
            self.terminal.log("Text pasted from clipboard", "INFO")

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
                self.terminal.log(f"Found first occurrence of '{search}'", "INFO")
            else:
                self.terminal.log(f"No occurrences of '{search}' found", "INFO")

    def find_next(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab or not self.last_search:
            self.terminal.log("No search term or file open for Find Next", "ERROR")
            return
        cursor = current_tab.textCursor()
        next_cursor = cursor.document().find(self.last_search, cursor)
        if next_cursor.hasSelection():
            current_tab.setTextCursor(next_cursor)
            self.terminal.log(f"Found next occurrence of '{self.last_search}'", "INFO")
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
            self.terminal.log(f"Found previous occurrence of '{self.last_search}'", "INFO")
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
                    self.terminal.log(f"Replaced all '{search}' with '{replace}'", "INFO")
                else:
                    self.terminal.log(f"No occurrences of '{search}' found for replacement", "INFO")

    def toggle_case(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            cursor = current_tab.textCursor()
            if cursor.hasSelection():
                text = cursor.selectedText()
                # Check if the selected text starts with "// "
                if text.startswith("// "):
                    # Remove the "// " (3 characters) from the start
                    new_text = text[3:]
                    cursor.insertText(new_text)
                    self.terminal.log("Removed '// ' from selected text", "INFO")
                else:
                    # If no "// ", proceed with case toggle
                    if text.isupper():
                        cursor.insertText(text.lower())
                        self.terminal.log("Converted selected text to lowercase", "INFO")
                    else:
                        cursor.insertText(text.upper())
                        self.terminal.log("Converted selected text to uppercase", "INFO")
            else:
                self.terminal.log("No text selected for Toggle Case", "INFO")

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
                    current_tab.line_number_area.set_marker(line - 1)  # Set marker for the line (0-based index)
                    self.terminal.log(f"Navigated to line {line}", "INFO")
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
                # Remove the full "// " (3 characters) including the space
                comment_start = line_start + leading_spaces
                cursor.setPosition(comment_start)
                cursor.movePosition(cursor.Right, cursor.KeepAnchor, 3)  # Move 3 characters for "// "
                cursor.removeSelectedText()
                self.terminal.log("Removed '// ' comment from line", "INFO")
            else:
                cursor.setPosition(line_start + leading_spaces)
                cursor.insertText("// ")
                self.terminal.log("Added '// ' comment to line", "INFO")
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
            self.terminal.log(f"Indented selection by {indent_size} spaces", "INFO")
        else:
            cursor.insertText(" " * indent_size)
            self.terminal.log(f"Indented line by {indent_size} spaces", "INFO")
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
            self.terminal.log(f"Dedented selection by up to {indent_size} spaces", "INFO")
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
            self.terminal.log(f"Dedented line by {spaces_to_remove} spaces", "INFO")
        cursor.endEditBlock()

    def set_theme(self):
        themes = ["dark", "light"]
        theme, ok = QInputDialog.getItem(self, "Select Theme", "Theme:", themes, themes.index(self.settings["theme"]), False)
        if ok:
            self.settings["theme"] = theme
            self.apply_theme()
            self.save_settings()
            self.terminal.log(f"Theme set to {theme}", "INFO")

    def set_indent_size(self):
        sizes = ["2", "4", "8"]
        size, ok = QInputDialog.getItem(self, "Select Indent Size", "Spaces:", sizes, sizes.index(str(self.settings["indent_size"])), False)
        if ok:
            self.settings["indent_size"] = int(size)
            self.save_settings()
            self.terminal.log(f"Indent size set to {size} spaces", "INFO")

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
                        self.terminal.log(f"Screen size set to {width}x{height}, position set to ({x_pos}, {y_pos})", "INFO")

    def set_goto_marker_duration(self):
        duration, ok = QInputDialog.getInt(self, "Goto Marker Duration", "Duration in seconds (1-10):", self.settings["goto_marker_duration"], 1, 10)
        if ok:
            self.settings["goto_marker_duration"] = duration
            self.save_settings()
            self.terminal.log(f"Goto marker duration set to {duration} seconds", "INFO")

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
        self.terminal.log(f"Applied geometry: position=({x_pos}, {y_pos}), size={width}x{height}", "INFO")

    def apply_theme(self):
        if self.settings["theme"] == "dark":
            bg_color = "#2E2E2E"
            fg_color = "#FFFFFF"
            ln_color = "#888888"
            menu_border = "#AAAAAA"
            menu_bg = "#3A3A3A"
        else:
            bg_color = "#FFFFFF"
            fg_color = "#000000"
            ln_color = "#888888"
            menu_border = "#666666"
            menu_bg = "#F0F0F0"
        self.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
        menu_style = f"QMenu {{ background-color: {menu_bg}; color: {fg_color}; border: 1px solid {menu_border}; }} QMenu::item:selected {{ background-color: {ln_color}; }}"
        for i in range(self.tabs.count()):
            text_edit = self.tabs.widget(i)
            text_edit.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
            text_edit.line_number_area.update()
        self.terminal.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
        QApplication.instance().setStyleSheet(menu_style)

    def set_font_size(self):
        size, ok = QInputDialog.getInt(self, "Font Size", "Enter font size (8-24):", self.settings["font_size"], 8, 24)
        if ok:
            self.settings["font_size"] = size
            self.apply_text_settings()
            self.save_settings()
            self.terminal.log(f"Font size set to {size}", "INFO")

    def toggle_line_numbers(self):
        self.settings["line_numbers"] = not self.settings["line_numbers"]
        self.apply_text_settings()
        self.save_settings()
        self.terminal.log(f"Line numbers {'enabled' if self.settings['line_numbers'] else 'disabled'}", "INFO")

    def toggle_word_wrap(self):
        self.settings["word_wrap"] = not self.settings["word_wrap"]
        self.apply_text_settings()
        self.save_settings()
        self.terminal.log(f"Word wrap {'enabled' if self.settings['word_wrap'] else 'disabled'}", "INFO")

    def toggle_save_confirmation(self):
        self.settings["save_confirmation"] = not self.settings["save_confirmation"]
        self.save_settings()
        self.terminal.log(f"Save confirmation {'enabled' if self.settings['save_confirmation'] else 'disabled'}", "INFO")

    def toggle_info_logs(self):
        self.settings["show_info"] = not self.settings["show_info"]
        self.save_settings()
        self.terminal.log(f"Info logs {'enabled' if self.settings['show_info'] else 'disabled'}", "INFO")

    def toggle_error_logs(self):
        self.settings["show_errors"] = not self.settings["show_errors"]
        self.save_settings()
        self.terminal.log(f"Error logs {'enabled' if self.settings['show_errors'] else 'disabled'}", "INFO")

    def apply_text_settings(self, text_edit=None):
        font = QFont("Arial", self.settings["font_size"])
        QApplication.instance().setFont(font)
        self.menuBar().setFont(font)
        # Apply font to all actions in the menu bar
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
            bg_color = "#2E2E2E" if self.settings["theme"] == "dark" else "#FFFFFF"
            fg_color = "#FFFFFF" if self.settings["theme"] == "dark" else "#000000"
            text_edit.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
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
                for key, value in self.settings.items():
                    if key in loaded_settings:
                        self.settings[key] = loaded_settings[key]
                self.recent_files = self.settings.get("recent_files", [])
                self.apply_screen_size_and_position()
                self.apply_text_settings()
                self.terminal.log("Settings loaded successfully", "INFO")
        except FileNotFoundError:
            self.terminal.log("No settings file found, using defaults", "INFO")
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
        self.terminal.log("Applied default geometry", "INFO")

    def save_settings(self):
        self.settings["recent_files"] = self.recent_files
        self.settings["window_size"] = [self.width(), self.height()]
        self.settings["window_position"] = [self.pos().x(), self.pos().y()]
        settings_path = self.get_settings_path()
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
            os.chmod(settings_path, 0o600)
            self.terminal.log(f"Settings saved to {settings_path}", "INFO")
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
    ide.terminal.log(f"Initialized window at position ({ide.settings['window_position'][0]}, {ide.settings['window_position'][1]})", "INFO")
    sys.exit(app.exec_())