import sys
import os
import json
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
                             QMenuBar, QAction, QFileDialog, QDockWidget, QListWidget, QMessageBox,
                             QInputDialog, QFontDialog, QMenu, QFrame)
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
from PyQt5.QtGui import QTextOption, QTextDocument, QFont, QPainter
from PyQt5.QtCore import Qt, QUrl, QPoint
from PyQt5.QtGui import QDesktopServices
from collections import deque
import uuid

class LineNumberArea(QFrame):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setFixedWidth(40)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), Qt.lightGray if self.editor.ide.settings["theme"] == "light" else Qt.darkGray)
        doc = self.editor.document()
        cursor = self.editor.cursorForPosition(self.editor.viewport().pos())
        first_visible_block = doc.findBlock(cursor.position())
        block_number = first_visible_block.blockNumber()
        top = self.editor.cursorRect(cursor).top()
        bottom = top + int(self.editor.fontMetrics().height())
        height = self.editor.fontMetrics().height()
        block = first_visible_block
        painter.setFont(QFont("Arial", self.editor.ide.settings["tab_font_size"]))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(Qt.black if self.editor.ide.settings["theme"] == "light" else Qt.white)
                painter.drawText(0, top, self.width() - 5, height, Qt.AlignRight, number)
            block = block.next()
            top = bottom
            bottom = top + int(self.editor.fontMetrics().height())
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

    def log(self, message, level="INFO"):
        if (level == "INFO" and self.parent().parent().settings["show_info"]) or \
           (level == "ERROR" and self.parent().parent().settings["show_errors"]):
            self.addItem(f"[{level}] {message}")

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

    def handle_item_clicked(self, item):
        text = item.text()
        if "http://" in text or "https://" in text:
            url = QUrl(text.split()[-1])
            if url.isValid():
                QDesktopServices.openUrl(url)

class IDE(QMainWindow):
    def __init__(self, filename=None):
        super().__init__()
        self.setWindowTitle("CHEP CLUB IDE")
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)
        self.terminal = TerminalWindow()
        self.dock = QDockWidget("Terminal", self)
        self.dock.setWidget(self.terminal)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
        # Initialize default settings
        self.settings = {
            "theme": "light",
            "line_numbers": True,
            "word_wrap": False,
            "show_info": True,
            "show_errors": True,
            "save_confirmation": True,
            "window_size": [800, 600],
            "window_position": [0, 0],
            "menu_font_size": 10,
            "recent_font_size": 10,
            "tab_font_size": 10,
            "indent_size": 4,
            "recent_files": [],
            "screen_width": 800,
            "screen_height": 600
        }
        self.recent_files = []
        self.file_cache = {}
        self.history = {}
        self.file_states = {}
        self.file_menu = None  # Initialize File menu reference
        self.init_ui()
        self.load_settings()
        self.apply_theme()
        # Open file from command line if provided
        if filename and os.path.exists(filename):
            self.open_file_by_path(filename)

    def init_ui(self):
        menubar = self.menuBar()
        self.file_menu = menubar.addMenu("&File")  # Store File menu reference
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

        print_action = QAction("&Print", self)
        print_action.setShortcut("Ctrl+P")
        print_action.triggered.connect(self.print_file)
        self.file_menu.addAction(print_action)

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

        find_action = QAction("&Find/Replace", self)
        find_action.setShortcut("Ctrl+F")
        find_action.triggered.connect(self.find_replace)
        edit_menu.addAction(find_action)

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
        clear_recent_action = QAction("&Clear Recent Files", self)
        clear_recent_action.triggered.connect(self.clear_recent_files)
        recent_files_menu.addAction(clear_recent_action)

        # Help Menu
        about_action = QAction("&About", self)
        about_action.triggered.connect(lambda: QMessageBox.information(self, "About", "Custom IDE v1.0"))
        help_menu.addAction(about_action)

        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self.show_tab_context_menu)
        self.tabs.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.tabs and event.type() == event.KeyPress:
            if event.key() == Qt.Key_Tab:
                self.indent()
                return True
            elif event.key() == Qt.Key_Backtab:
                self.dedent()
                return True
        return super().eventFilter(obj, event)

    def show_tab_context_menu(self, position):
        menu = QMenu()
        copy_path = menu.addAction("Copy Path")
        action = menu.exec_(self.tabs.mapToGlobal(position))
        if action == copy_path:
            current_tab = self.tabs.currentWidget()
            if current_tab and hasattr(current_tab, "file_path"):
                QApplication.clipboard().setText(current_tab.file_path)

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
                current_tab.file_path = file_path
            try:
                with open(current_tab.file_path, "w") as f:
                    f.write(current_tab.toPlainText())
                current_tab.document().setModified(False)  # Clear modified state
                self.terminal.log(f"Saved {current_tab.file_path}", "INFO")
                self.file_cache[current_tab.file_path] = current_tab.toPlainText()
                self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(current_tab.file_path))
                # Update recent files, no duplicates, max 10
                if current_tab.file_path in self.recent_files:
                    self.recent_files.remove(current_tab.file_path)
                self.recent_files.insert(0, current_tab.file_path)
                if len(self.recent_files) > 10:
                    self.recent_files.pop()
                self.save_settings()
            except Exception as e:
                self.terminal.log(f"Error saving {current_tab.file_path}: {str(e)}", "ERROR")

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
                        text_edit.document().setModified(False)  # Reset modified state after reload
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
        # Show up to 10 recent files with index numbers
        for index, file in enumerate(self.recent_files[:10], 1):
            action = menu.addAction(f"{index}. {os.path.basename(file)}")
            action.setToolTip(file)
            action.triggered.connect(lambda checked, f=file: self.open_file_by_path(f))
        pos = self.mapToGlobal(QPoint(50, 50))  # 50 pixels right, 50 pixels down from app's top-left
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
        text_edit.file_path = file_path
        text_edit.textChanged.connect(lambda: self.record_history(text_edit))
        self.check_file_changes(text_edit)
        text_edit.document().setModified(False)  # Ensure opened file is not marked modified
        self.tabs.addTab(text_edit, os.path.basename(file_path))
        self.tabs.setCurrentWidget(text_edit)
        self.apply_text_settings(text_edit)
        self.terminal.log(f"Opened {file_path}", "INFO")
        # Update recent files, no duplicates, max 10
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
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

    def find_replace(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            search, ok = QInputDialog.getText(self, "Find", "Search for:")
            if ok and search:
                replace, ok = QInputDialog.getText(self, "Replace", "Replace with:")
                if ok:
                    content = current_tab.toPlainText()
                    new_content = content.replace(search, replace)
                    current_tab.setPlainText(new_content)
                    self.terminal.log(f"Replaced '{search}' with '{replace}'", "INFO")

    def toggle_case(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            cursor = current_tab.textCursor()
            if cursor.hasSelection():
                text = cursor.selectedText()
                if text.isupper():
                    cursor.insertText(text.lower())
                else:
                    cursor.insertText(text.upper())
                self.terminal.log("Toggled case of selected text", "INFO")

    def goto_line(self):
        if not self.settings["word_wrap"]:
            current_tab = self.tabs.currentWidget()
            if current_tab:
                line, ok = QInputDialog.getInt(self, "Go to Line", "Line number:", 1, 1)
                if ok:
                    doc = current_tab.document()
                    block = doc.findBlockByLineNumber(line - 1)
                    if block.isValid():
                        cursor = current_tab.textCursor()
                        cursor.setPosition(block.position())
                        current_tab.setTextCursor(cursor)
                        current_tab.ensureCursorVisible()
                        self.terminal.log(f"Navigated to line {line}", "INFO")

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
                cursor.movePosition(cursor.Right, cursor.KeepAnchor, 2)
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
        width, ok_width = QInputDialog.getInt(self, "Screen Size", "Width (pixels):", self.settings["screen_width"], 100, max_width)
        if ok_width:
            height, ok_height = QInputDialog.getInt(self, "Screen Size", "Height (pixels):", self.settings["screen_height"], 100, max_height)
            if ok_height:
                x_pos, ok_x = QInputDialog.getInt(self, "Window Position", "X Position (pixels):", self.settings["window_position"][0], 0, max_width - width)
                if ok_x:
                    y_pos, ok_y = QInputDialog.getInt(self, "Window Position", "Y Position (pixels):", self.settings["window_position"][1], 0, max_height - height)
                    if ok_y:
                        self.settings["screen_width"] = width
                        self.settings["screen_height"] = height
                        self.settings["window_position"] = [x_pos, y_pos]
                        self.apply_screen_size_and_position()
                        self.save_settings()
                        self.terminal.log(f"Screen size set to {width}x{height}, position set to ({x_pos}, {y_pos})", "INFO")

    def apply_screen_size_and_position(self):
        width = self.settings["screen_width"]
        height = self.settings["screen_height"]
        x_pos = self.settings["window_position"][0]
        y_pos = self.settings["window_position"][1]
        # Ensure window stays fully on screen
        screen = QApplication.primaryScreen().availableGeometry()
        max_x = screen.width() - width
        max_y = screen.height() - height
        max_height = screen.height()
        x_pos = max(0, min(x_pos, max_x))  # Clamp X to keep window on screen
        y_pos = max(0, min(y_pos, max_y))  # Clamp Y to keep top visible and bottom on screen
        height = min(height, max_height)  # Clamp height to fit screen
        # Use move and resize for precise positioning
        self.resize(width, height)
        self.move(x_pos, y_pos)
        self.settings["window_size"] = [width, height]
        self.settings["window_position"] = [x_pos, y_pos]
        self.terminal.log(f"Applied geometry: position=({x_pos}, {y_pos}), size={width}x{height}", "INFO")

    def apply_theme(self):
        if self.settings["theme"] == "dark":
            bg_color = "#2E2E2E"
            fg_color = "#FFFFFF"
            ln_color = "#888888"
        else:
            bg_color = "#FFFFFF"
            fg_color = "#000000"
            ln_color = "#888888"
        self.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
        for i in range(self.tabs.count()):
            text_edit = self.tabs.widget(i)
            text_edit.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
            text_edit.line_number_area.update()
        self.terminal.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")

    def set_font_size(self):
        font, ok = QFontDialog.getFont(QFont("Arial", self.settings["tab_font_size"]), self)
        if ok:
            self.settings["tab_font_size"] = font.pointSize()
            self.apply_text_settings()
            self.save_settings()
            self.terminal.log(f"Font size set to {font.pointSize()}", "INFO")

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
        font = QFont("Arial", self.settings["tab_font_size"])
        self.menuBar().setFont(font)
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

    def load_settings(self):
        try:
            with open("ide_settings.json", "r") as f:
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
        self.settings["screen_width"] = width
        self.settings["screen_height"] = height
        self.settings["window_position"] = [0, 0]  # Default position, adjustable
        self.resize(width, height)
        self.move(0, 0)
        self.settings["window_size"] = [width, height]
        self.terminal.log("Applied default geometry", "INFO")

    def save_settings(self):
        self.settings["recent_files"] = self.recent_files
        self.settings["window_size"] = [self.settings["screen_width"], self.settings["screen_height"]]
        self.settings["window_position"] = [self.pos().x(), self.pos().y()]  # Save current position
        try:
            with open("ide_settings.json", "w") as f:
                json.dump(self.settings, f, indent=4)
            self.terminal.log(f"Settings saved with position ({self.pos().x()}, {self.pos().y()})", "INFO")
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