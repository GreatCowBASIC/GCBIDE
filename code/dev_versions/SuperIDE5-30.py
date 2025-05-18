import os
import sys
import json5
import re
import subprocess
import time
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QPlainTextEdit, QAction, QFileDialog,
    QWidget, QMessageBox, QInputDialog, QDialog, QVBoxLayout, QTabWidget, 
    QCheckBox, QPushButton, QLabel, QMenu, QLineEdit, QDockWidget, QTextEdit, 
    QRadioButton, QButtonGroup, QSpinBox
)
from PyQt5.QtCore import Qt, QRect, QSize, QPoint, QUrl, QFileSystemWatcher
from PyQt5.QtGui import QFont, QColor, QPainter, QTextCharFormat, QTextCursor, QFontMetrics
from PyQt5.Qt import QSyntaxHighlighter, QDesktopServices

class UrlHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.url_format = QTextCharFormat()
        self.url_format.setForeground(Qt.blue)
        self.url_format.setFontUnderline(True)
        self.url_pattern = re.compile(r'(https?://[\w\-./:]+)')

    def highlightBlock(self, text):
        for match in self.url_pattern.finditer(text):
            start, end = match.span()
            self.setFormat(start, end - start, self.url_format)

class SpellChecker(QSyntaxHighlighter):
    def __init__(self, parent, word_list):
        super().__init__(parent)
        self.word_list = set(word.lower() for word in word_list)
        self.spell_format = QTextCharFormat()
        self.spell_format.setForeground(Qt.red)
        self.spell_format.setFontUnderline(True)
        self.spell_format.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        self.word_pattern = re.compile(r'\b\w+\b')

    def highlightBlock(self, text):
        for match in self.word_pattern.finditer(text):
            word = match.group(0).lower()
            if word not in self.word_list:
                start, end = match.span()
                self.setFormat(start, end - start, self.spell_format)

class TerminalTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ide = parent
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.url_pattern = re.compile(r'(https?://[\w\-./:]+)')

    def show_context_menu(self, pos):
        menu = QMenu(self)
        copy_line_action = QAction("Copy Line", self)
        copy_line_action.triggered.connect(self.copy_line)
        copy_line_action.setEnabled(self.textCursor().block().text().strip() != "")
        menu.addAction(copy_line_action)
        
        copy_all_action = QAction("Copy All", self)
        copy_all_action.triggered.connect(self.copy_all)
        copy_all_action.setEnabled(self.toPlainText().strip() != "")
        menu.addAction(copy_all_action)
        
        clear_action = QAction("Clear Terminal", self)
        clear_action.triggered.connect(self.ide.clear_terminal)
        menu.addAction(clear_action)
        
        menu.exec_(self.mapToGlobal(pos))

    def copy_line(self):
        cursor = self.cursorForPosition(self.mapFromGlobal(self.mapToGlobal(QPoint(0, 0))))
        cursor.select(QTextCursor.LineUnderCursor)
        self.setTextCursor(cursor)
        self.copy()

    def copy_all(self):
        self.selectAll()
        self.copy()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.modifiers() == Qt.ControlModifier:
            cursor = self.cursorForPosition(event.pos())
            block = cursor.block()
            text = block.text()
            char_pos = cursor.positionInBlock()
            if self.ide.show_info_logs:
                self.ide.log_to_terminal(f"Ctrl+click at char {char_pos}: {text}", "Info")
            for match in self.url_pattern.finditer(text):
                start, end = match.span()
                if start <= char_pos <= end:
                    url = match.group(0)
                    if self.ide.show_info_logs:
                        self.ide.log_to_terminal(f"Opening URL: {url}", "Info")
                    QDesktopServices.openUrl(QUrl(url))
                    return
        super().mousePressEvent(event)

class ColorScheme:
    SCHEMES = {
        "Light": {"background": "#FFFFFF", "foreground": "#000000", "line_numbers_bg": "#F0F0F0", "line_numbers_fg": "#000000"},
        "Dark": {"background": "#1E1E1E", "foreground": "#D4D4D4", "line_numbers_bg": "#2D2D2D", "line_numbers_fg": "#D4D4D4"}
    }

    def __init__(self, name="Light", custom_colors=None):
        self.name = name
        self.colors = custom_colors or self.SCHEMES.get(name, self.SCHEMES["Light"])

    def get_color(self, key):
        return QColor(self.colors[key])

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setFixedWidth(50)

    def sizeHint(self):
        return QSize(50, 0)

    def paintEvent(self, event):
        if not self.editor.show_line_numbers:
            return
        painter = QPainter(self)
        painter.fillRect(event.rect(), self.editor.color_scheme.get_color("line_numbers_bg"))
        block = self.editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top())
        bottom = top + int(self.editor.blockBoundingRect(block).height())
        height = self.editor.fontMetrics().height()
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(self.editor.color_scheme.get_color("line_numbers_fg"))
                painter.drawText(15, top, self.width() - 20, height, Qt.AlignRight, number)
            block = block.next()
            top = bottom
            bottom = top + int(self.editor.blockBoundingRect(block).height())
            block_number += 1
        
        painter.setPen(QColor("gray"))
        for start_line, end_line, level in self.editor.control_structures:
            if start_line < block_number - 10 or end_line > block_number + 10:
                continue
            start_block = self.editor.document().findBlockByLineNumber(start_line)
            end_block = self.editor.document().findBlockByLineNumber(end_line)
            if start_block.isValid() and end_block.isValid():
                start_top = int(self.editor.blockBoundingGeometry(start_block).translated(self.editor.contentOffset()).top())
                end_bottom = int(self.editor.blockBoundingGeometry(end_block).translated(self.editor.contentOffset()).top()) + int(self.editor.blockBoundingRect(end_block).height())
                x = 10 + level * 5
                painter.drawLine(x, start_top, x, end_bottom)

class CodeEditor(QPlainTextEdit):
    def __init__(self, color_scheme, file_path=None):
        super().__init__()
        self.color_scheme = color_scheme
        self.show_line_numbers = True
        self.file_path = file_path
        self.line_number_area = LineNumberArea(self)
        self.control_structures = []
        self.spell_check_enabled = False
        self.word_list = [
            "the", "be", "to", "of", "and", "in", "that", "have", "it", "for",
            "not", "on", "with", "he", "as", "you", "do", "at", "this", "but",
            "if", "else", "while", "for", "sub", "function", "end", "loop", "next"
        ]
        self.spell_checker = None
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.textChanged.connect(self.parse_control_structures)
        self.setFont(QFont("Courier New", 10))
        self.update_line_number_area_width(0)
        self.parse_control_structures()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, pos):
        menu = QMenu(self)
        if self.spell_check_enabled:
            cursor = self.cursorForPosition(pos)
            cursor.select(QTextCursor.WordUnderCursor)
            word = cursor.selectedText().lower()
            if word and word not in self.word_list:
                suggestions = self.get_suggestions(word)
                if suggestions:
                    suggest_menu = QMenu("Suggestions", self)
                    for suggestion in suggestions:
                        action = QAction(suggestion, self)
                        action.triggered.connect(lambda checked, s=suggestion: self.replace_word(s))
                        suggest_menu.addAction(action)
                    menu.addMenu(suggest_menu)
        menu.exec_(self.mapToGlobal(pos))

    def get_suggestions(self, word):
        suggestions = []
        for dict_word in self.word_list:
            if abs(len(dict_word) - len(word)) <= 2:
                if sum(a != b for a, b in zip(word, dict_word)) <= 2:
                    suggestions.append(dict_word)
        return suggestions[:3]

    def replace_word(self, new_word):
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.insertText(new_word)
            self.setTextCursor(cursor)

    def line_number_area_width(self):
        if not self.show_line_numbers:
            return 0
        digits = len(str(max(1, self.blockCount())))
        return 40 + self.fontMetrics().horizontalAdvance('9') * digits

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if not self.show_line_numbers:
            self.line_number_area.hide()
            return
        self.line_number_area.show()
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def toggle_line_numbers(self, show):
        self.show_line_numbers = show
        self.update_line_number_area_width(0)
        self.viewport().update()

    def parse_control_structures(self):
        self.control_structures = []
        lines = self.toPlainText().splitlines()
        stack = []
        control_pairs = {
            'DO': 'LOOP',
            'IF': 'END IF',
            'FOR': 'NEXT',
            'WHILE': 'END WHILE',
            'SUB': 'END SUB',
            'FUNCTION': 'END FUNCTION'
        }
        comment_pattern = re.compile(r'^\s*(?:\'|;|REM\s)')
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or comment_pattern.match(line):
                continue
            line_upper = line.upper()
            for start_key, end_key in control_pairs.items():
                if line_upper.startswith(start_key + ' ') or line_upper == start_key:
                    stack.append((start_key, i, len(stack)))
                    break
                if line_upper.startswith(end_key + ' ') or line_upper == end_key:
                    if stack and stack[-1][0] == start_key:
                        start_key, start_line, level = stack.pop()
                        self.control_structures.append((start_line, i, level))
                    break
        self.line_number_area.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Tab:
            self.indent_selection()
            return
        elif event.key() == Qt.Key_Backtab or (event.key() == Qt.Key_Tab and event.modifiers() == Qt.AltModifier):
            self.unindent_selection()
            return
        super().keyPressEvent(event)

    def indent_selection(self):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            start_block = self.document().findBlock(start)
            end_block = self.document().findBlock(end)
            current_block = start_block
            while current_block.isValid() and (current_block.position() <= end_block.position()):
                cursor.setPosition(current_block.position())
                cursor.insertText("    ")
                current_block = current_block.next()
        else:
            cursor.insertText("    ")
        cursor.endEditBlock()

    def unindent_selection(self):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            start_block = self.document().findBlock(start)
            end_block = self.document().findBlock(end)
            current_block = start_block
            while current_block.isValid() and current_block.position() <= end_block.position():
                cursor.setPosition(current_block.position())
                line_text = current_block.text()
                if line_text.startswith("    "):
                    for _ in range(4):
                        cursor.deleteChar()
                current_block = current_block.next()
        cursor.endEditBlock()

    def toggle_comment(self):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            start_block = self.document().findBlock(start)
            end_block = self.document().findBlock(end)
            current_block = start_block
            all_commented = True
            while current_block.isValid() and current_block.position() <= end_block.position():
                text = current_block.text()
                stripped = text.strip()
                if stripped and not stripped.startswith("// "):
                    all_commented = False
                    break
                current_block = current_block.next()
            
            current_block = start_block
            while current_block.isValid() and current_block.position() <= end_block.position():
                cursor.setPosition(current_block.position())
                text = current_block.text()
                leading_whitespace = len(text) - len(text.lstrip())
                if all_commented:
                    if text.strip().startswith("// "):
                        cursor.setPosition(current_block.position() + leading_whitespace)
                        if text[leading_whitespace:leading_whitespace+3] == "// ":
                            cursor.deleteChar()
                            cursor.deleteChar()
                            cursor.deleteChar()
                else:
                    if text.strip():
                        cursor.setPosition(current_block.position() + leading_whitespace)
                        cursor.insertText("// ")
                current_block = current_block.next()
        else:
            block = cursor.block()
            text = block.text()
            leading_whitespace = len(text) - len(text.lstrip())
            cursor.setPosition(block.position() + leading_whitespace)
            if text.strip().startswith("// "):
                if text[leading_whitespace:leading_whitespace+3] == "// ":
                    cursor.deleteChar()
                    cursor.deleteChar()
                    cursor.deleteChar()
            else:
                cursor.insertText("// ")
        cursor.endEditBlock()

    def change_case(self, case_type):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.LineUnderCursor)
        
        selected_text = cursor.selectedText()
        if not selected_text:
            cursor.endEditBlock()
            return
        
        words = selected_text.split()
        if case_type == "upper":
            new_text = selected_text.upper()
        elif case_type == "lower":
            new_text = selected_text.lower()
        elif case_type == "camel":
            new_text = ""
            for i, word in enumerate(words):
                if i == 0:
                    new_text += word.lower()
                else:
                    new_text += word.capitalize()
        elif case_type == "sentence":
            new_text = " ".join(words).lower()
            if new_text:
                new_text = new_text[0].upper() + new_text[1:]
        elif case_type == "title":
            new_text = " ".join(word.capitalize() for word in words)
        else:
            cursor.endEditBlock()
            return
        
        cursor.insertText(new_text)
        cursor.endEditBlock()

    def spell_check(self, enable):
        self.spell_check_enabled = enable
        if enable and not self.spell_checker:
            self.spell_checker = SpellChecker(self.document(), self.word_list)
        elif not enable and self.spell_checker:
            self.spell_checker.setDocument(None)
            self.spell_checker = None
            self.document().setPlainText(self.toPlainText())

    def set_word_wrap(self, enable):
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth if enable else QPlainTextEdit.NoWrap)

class SettingsDialog(QDialog):
    def __init__(self, parent, color_scheme, show_line_numbers, tasks_file, gcbasic_path, show_info_logs, show_error_logs, window_width, window_height, menu_font_size, recent_files_font_size, tab_font_size, word_wrap, show_save_confirmation):
        super().__init__(parent)
        self.setWindowTitle("IDE Settings")
        self.layout = QVBoxLayout()
        self.color_scheme_group = QButtonGroup(self)
        self.color_scheme_label = QLabel("Color Scheme:")
        self.layout.addWidget(self.color_scheme_label)
        for scheme in ColorScheme.SCHEMES.keys():
            rb = QRadioButton(scheme, self)
            self.color_scheme_group.addButton(rb)
            if scheme == color_scheme:
                rb.setChecked(True)
            self.layout.addWidget(rb)
        self.line_numbers_cb = QCheckBox("Show Line Numbers", self)
        self.line_numbers_cb.setChecked(show_line_numbers)
        self.layout.addWidget(self.line_numbers_cb)
        self.word_wrap_cb = QCheckBox("Word Wrap", self)
        self.word_wrap_cb.setChecked(word_wrap)
        self.layout.addWidget(self.word_wrap_cb)
        self.tasks_file_label = QLabel(f"Tasks File: {tasks_file}")
        self.layout.addWidget(self.tasks_file_label)
        self.tasks_file_button = QPushButton("Change Tasks File", self)
        self.tasks_file_button.clicked.connect(self.change_tasks_file)
        self.layout.addWidget(self.tasks_file_button)
        self.gcbasic_path_label = QLabel("GCBASIC Path:")
        self.layout.addWidget(self.gcbasic_path_label)
        self.gcbasic_path_input = QLineEdit(gcbasic_path, self)
        self.layout.addWidget(self.gcbasic_path_input)
        self.show_info_logs_cb = QCheckBox("Show Info Logs", self)
        self.show_info_logs_cb.setChecked(show_info_logs)
        self.layout.addWidget(self.show_info_logs_cb)
        self.show_error_logs_cb = QCheckBox("Show Error Logs", self)
        self.show_error_logs_cb.setChecked(show_error_logs)
        self.layout.addWidget(self.show_error_logs_cb)
        self.show_save_confirmation_cb = QCheckBox("Show Save File Confirmation", self)
        self.show_save_confirmation_cb.setChecked(show_save_confirmation)
        self.layout.addWidget(self.show_save_confirmation_cb)
        self.window_size_label = QLabel("Window Size:")
        self.layout.addWidget(self.window_size_label)
        self.window_width_spin = QSpinBox(self)
        self.window_width_spin.setRange(400, 3840)
        self.window_width_spin.setValue(window_width)
        self.layout.addWidget(self.window_width_spin)
        self.window_height_spin = QSpinBox(self)
        self.window_height_spin.setRange(300, 2160)
        self.window_height_spin.setValue(window_height)
        self.layout.addWidget(self.window_height_spin)
        self.menu_font_size_label = QLabel("Menu Font Size:")
        self.layout.addWidget(self.menu_font_size_label)
        self.menu_font_size_spin = QSpinBox(self)
        self.menu_font_size_spin.setRange(8, 20)
        self.menu_font_size_spin.setValue(menu_font_size)
        self.layout.addWidget(self.menu_font_size_spin)
        self.recent_files_font_size_label = QLabel("Recent Files Font Size:")
        self.layout.addWidget(self.recent_files_font_size_label)
        self.recent_files_font_size_spin = QSpinBox(self)
        self.recent_files_font_size_spin.setRange(8, 20)
        self.recent_files_font_size_spin.setValue(recent_files_font_size)
        self.layout.addWidget(self.recent_files_font_size_spin)
        self.tab_font_size_label = QLabel("Tab Font Size:")
        self.layout.addWidget(self.tab_font_size_label)
        self.tab_font_size_spin = QSpinBox(self)
        self.tab_font_size_spin.setRange(8, 20)
        self.tab_font_size_spin.setValue(tab_font_size)
        self.layout.addWidget(self.tab_font_size_spin)
        self.apply_button = QPushButton("Apply", self)
        self.apply_button.clicked.connect(self.apply_settings)
        self.layout.addWidget(self.apply_button)
        self.setLayout(self.layout)

    def change_tasks_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Select Tasks JSON File", "", "JSON Files (*.json);;All Files (*)")
        if file_name:
            self.tasks_file_label.setText(f"Tasks File: {file_name}")

    def apply_settings(self):
        selected_button = self.color_scheme_group.checkedButton()
        color_scheme = selected_button.text() if selected_button else "Light"
        self.parent().set_color_scheme(color_scheme)
        self.parent().toggle_line_numbers(self.line_numbers_cb.isChecked())
        self.parent().toggle_word_wrap(self.word_wrap_cb.isChecked())
        tasks_file = self.tasks_file_label.text().replace("Tasks File: ", "")
        if tasks_file != self.parent().tasks_file:
            self.parent().tasks_file = tasks_file
            self.parent().load_tasks()
        self.parent().gcbasic_path = self.gcbasic_path_input.text()
        self.parent().show_info_logs = self.show_info_logs_cb.isChecked()
        self.parent().show_error_logs = self.show_error_logs_cb.isChecked()
        self.parent().show_save_confirmation = self.show_save_confirmation_cb.isChecked()
        self.parent().resize(self.window_width_spin.value(), self.window_height_spin.value())
        self.parent().menu_font_size = self.menu_font_size_spin.value()
        self.parent().recent_files_font_size = self.recent_files_font_size_spin.value()
        self.parent().tab_font_size = self.tab_font_size_spin.value()
        self.parent().apply_menu_font()
        self.parent().apply_tab_font()
        self.parent().save_settings()
        self.accept()

class TaskSearchDialog(QDialog):
    def __init__(self, parent, tasks):
        super().__init__(parent)
        self.setWindowTitle("Search Tasks")
        self.tasks = tasks
        self.layout = QVBoxLayout()
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Enter task name...")
        self.search_input.textChanged.connect(self.filter_tasks)
        self.layout.addWidget(self.search_input)
        self.task_list = QMenu(self)
        self.task_list.aboutToShow.connect(self.populate_task_list)
        self.layout.addWidget(QLabel("Select a task:"))
        self.task_button = QPushButton("Show Tasks", self)
        self.task_button.clicked.connect(self.show_task_menu)
        self.layout.addWidget(self.task_button)
        self.setLayout(self.layout)

    def populate_task_list(self):
        self.task_list.clear()
        search_text = self.search_input.text().lower()
        filtered_tasks = [task for task in self.tasks if search_text in task["label"].lower()]
        for task in filtered_tasks:
            action = QAction(task["label"], self)
            action.triggered.connect(lambda checked, label=task["label"]: self.run_task(label))
            self.task_list.addAction(action)

    def show_task_menu(self):
        self.task_list.exec_(self.task_button.mapToGlobal(QPoint(0, self.task_button.height())))

    def run_task(self, label):
        self.parent().run_task(label)
        self.accept()

class GCBASICEssentialIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GCBASIC Essential IDE")
        screen = QApplication.primaryScreen().availableGeometry()
        default_width = int(screen.width() * 0.75)
        default_height = int(screen.height() * 0.75)
        self.setGeometry(100, 100, default_width, default_height)
        self.current_file = None
        self.color_scheme = ColorScheme()
        self.microcontroller = "PIC16F877A"
        self.tools = {"gcbasic": ""}
        self.recent_files = set()
        self.tasks_file = r"C:\GCstudio\vscode\data\user-data\User\tasks.json"
        self.gcbasic_path = ""
        self.tasks = []
        self.task_usage = {}
        self.show_info_logs = True
        self.show_error_logs = True
        self.show_save_confirmation = True
        self.tab_widget = QTabWidget()
        self.editors = {}
        self.terminal = None
        self.ide_operation_actions = []
        self.file_watcher = QFileSystemWatcher(self)
        self.file_watcher.fileChanged.connect(self.handle_file_changed)
        self.source_file_watcher = QFileSystemWatcher(self)
        self.source_file_watcher.fileChanged.connect(self.handle_source_file_changed)
        self.menu_font_size = 12
        self.recent_files_font_size = 10
        self.tab_font_size = 10
        self.word_wrap = True
        self.compilation_index = 0
        self.init_ui()
        self.load_settings()
        self.load_tasks()

    def init_ui(self):
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self.update_current_file)
        self.setCentralWidget(self.tab_widget)
        self.apply_tab_font()

        self.terminal = QDockWidget("Terminal", self)
        self.terminal_text = TerminalTextEdit(self)
        self.terminal_text.setReadOnly(True)
        self.terminal_text.setFont(QFont("Courier New", 10))
        self.url_highlighter = UrlHighlighter(self.terminal_text.document())
        self.terminal.setWidget(self.terminal_text)
        self.terminal.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.terminal)
        self.terminal.setMinimumHeight(100)

        source_file = os.path.abspath(__file__)
        if os.path.exists(source_file):
            self.source_file_watcher.addPath(source_file)

        self.create_menu()
        os.makedirs(os.path.expanduser("~/.GCBASICEssentialIDE"), exist_ok=True)
        self.apply_color_scheme()
        self.add_new_tab(None, "Untitled")

    def handle_source_file_changed(self, path):
        if self.show_info_logs:
            self.log_to_terminal(f"Source file {path} modified.", "Info")

    def log_to_terminal(self, message, level="Info"):
        if level == "Info" and not self.show_info_logs and not message.startswith("Compiler Output:"):
            return
        if level == "Error" and not self.show_error_logs:
            return
        if not self.terminal.isVisible():
            self.terminal.show()
        if message.startswith("Compiler Output:"):
            level = f"Info{self.compilation_index}"
        self.terminal_text.append(f"[{level}] {message}\n")
        self.terminal_text.verticalScrollBar().setValue(self.terminal_text.verticalScrollBar().maximum())

    def clear_terminal(self):
        self.terminal_text.clear()
        if self.show_info_logs:
            self.log_to_terminal("Terminal cleared.", "Info")

    def update_current_file(self, index):
        widget = self.tab_widget.widget(index)
        self.current_file = next((k for k, v in self.editors.items() if v == widget), None)
        editor = self.get_active_editor()
        if editor and editor.file_path != self.current_file:
            if self.show_error_logs:
                self.log_to_terminal(f"File path mismatch: editor.file_path={editor.file_path}, current_file={self.current_file}", "Error")
            self.current_file = editor.file_path
        if self.show_info_logs:
            self.log_to_terminal(f"Current file updated to: {self.current_file or 'None'}", "Info")

    def create_menu(self):
        menubar = self.menuBar()
        menu_font = QFont()
        menu_font.setPointSize(self.menu_font_size)
        menubar.setFont(menu_font)
        
        file_menu = menubar.addMenu("&File")
        file_menu.setFont(menu_font)
        for name, shortcut, func in [
            ("&New", "Ctrl+N", self.new_file),
            ("&Open", "Ctrl+O", self.open_file),
            ("&Save", "Ctrl+S", self.save_file),
            ("Save &As...", "Ctrl+Shift+S", self.save_file_as),
            ("&Recent Files", "Ctrl+R", self.show_recent_files),
            ("E&xit", "Ctrl+Q", self.close)
        ]:
            action = QAction(name, self)
            action.setShortcut(shortcut)
            action.setFont(menu_font)
            action.triggered.connect(func)
            file_menu.addAction(action)

        edit_menu = menubar.addMenu("&Edit")
        edit_menu.setFont(menu_font)
        for name, shortcut, func in [
            ("&Undo", "Ctrl+Z", lambda: self.get_active_editor().undo() if self.get_active_editor() else None),
            ("&Redo", "Ctrl+Y", lambda: self.get_active_editor().redo() if self.get_active_editor() else None),
            ("&Cut", "Ctrl+X", lambda: self.get_active_editor().cut() if self.get_active_editor() else None),
            ("&Copy", "Ctrl+C", lambda: self.get_active_editor().copy() if self.get_active_editor() else None),
            ("&Paste", "Ctrl+V", lambda: self.get_active_editor().paste() if self.get_active_editor() else None),
            ("&Find", "Ctrl+F", self.find),
            ("&Replace", "Ctrl+H", self.replace),
            ("&Goto Line", "Ctrl+G", self.goto_line),
            ("Select &Line", "Ctrl+L", self.select_line),
            ("Select &All", "Ctrl+A", lambda: self.get_active_editor().selectAll() if self.get_active_editor() else None),
            ("Clear &Terminal", "Ctrl+Shift+K", self.clear_terminal),
            ("Toggle &Comment", "Ctrl+/", lambda: self.get_active_editor().toggle_comment() if self.get_active_editor() else None),
            ("Spell &Check", "Ctrl+Shift+S", lambda: self.toggle_spell_check())
        ]:
            action = QAction(name, self)
            action.setShortcut(shortcut)
            action.setFont(menu_font)
            action.triggered.connect(func)
            edit_menu.addAction(action)

        case_menu = QMenu("Change &Case", self)
        case_menu.setFont(menu_font)
        for name, case_type in [
            ("&Upper Case", "upper"),
            ("&Lower Case", "lower"),
            ("&Camel Case", "camel"),
            ("&Sentence Case", "sentence"),
            ("&Title Case", "title")
        ]:
            action = QAction(name, self)
            action.setFont(menu_font)
            action.triggered.connect(lambda checked, ct=case_type: self.get_active_editor().change_case(ct) if self.get_active_editor() else None)
            case_menu.addAction(action)
        edit_menu.addMenu(case_menu)

        self.ide_operations_menu = menubar.addMenu("&IDE Operations")
        self.ide_operations_menu.setFont(menu_font)
        f4_action = QAction("Run F4 (List All Tasks)", self)
        f4_action.setShortcut("F4")
        f4_action.setFont(menu_font)
        f4_action.triggered.connect(self.run_f4)
        self.ide_operations_menu.addAction(f4_action)
        self.ide_operation_actions.append(f4_action)
        self.update_ide_operations_menu()

        settings_menu = menubar.addMenu("&IDE Settings")
        settings_menu.setFont(menu_font)
        settings_action = QAction("&Settings", self)
        settings_action.setFont(menu_font)
        settings_action.triggered.connect(self.open_settings)
        settings_menu.addAction(settings_action)

        help_menu = menubar.addMenu("&Help")
        help_menu.setFont(menu_font)
        source_info_action = QAction("Show Source Info", self)
        source_info_action.setFont(menu_font)
        source_info_action.triggered.connect(self.show_source_info)
        help_menu.addAction(source_info_action)

    def apply_menu_font(self):
        menu_font = QFont()
        menu_font.setPointSize(self.menu_font_size)
        menubar = self.menuBar()
        menubar.setFont(menu_font)
        for menu in menubar.findChildren(QMenu):
            menu.setFont(menu_font)
            for action in menu.actions():
                action.setFont(menu_font)
        self.update_ide_operations_menu()

    def apply_tab_font(self):
        font = QFont("Arial", self.tab_font_size)
        metrics = QFontMetrics(font)
        # Estimate max tab width based on longest tab title
        max_width = 100  # Default minimum width
        for i in range(self.tab_widget.count()):
            text = self.tab_widget.tabText(i)
            text_width = metrics.horizontalAdvance(text) + 20  # Add padding
            max_width = max(max_width, text_width)
        self.tab_widget.setStyleSheet(f"""
            QTabBar::tab {{
                font-size: {self.tab_font_size}px;
                padding: 5px 10px;
                min-width: {max_width}px;
                max-width: {max_width + 20}px;
            }}
            QTabBar::tab:selected {{
                font-weight: bold;
            }}
        """)
        # Force tab bar to update geometry
        self.tab_widget.tabBar().setMinimumHeight(metrics.height() + 10)
        self.tab_widget.tabBar().updateGeometry()

    def toggle_spell_check(self):
        editor = self.get_active_editor()
        if editor:
            editor.spell_check(not editor.spell_check_enabled)
            if self.show_info_logs:
                self.log_to_terminal(f"Spell check {'enabled' if editor.spell_check_enabled else 'disabled'}.", "Info")

    def goto_line(self):
        editor = self.get_active_editor()
        if not editor:
            return
        line_number, ok = QInputDialog.getInt(self, "Goto Line", "Enter line number:", 1, 1, editor.blockCount())
        if ok:
            # Use physical line number (block number)
            block = editor.document().findBlockByNumber(line_number - 1)
            if block.isValid():
                cursor = QTextCursor(block)
                editor.setTextCursor(cursor)
                editor.centerCursor()
                if self.show_info_logs:
                    self.log_to_terminal(f"Moved to physical line {line_number}.", "Info")
            else:
                if self.show_error_logs:
                    self.log_to_terminal(f"Line {line_number} is out of range.", "Error")

    def toggle_word_wrap(self, enable):
        self.word_wrap = enable
        for editor in self.editors.values():
            editor.set_word_wrap(enable)
        if self.show_info_logs:
            self.log_to_terminal(f"Word wrap {'enabled' if enable else 'disabled'}.", "Info")

    def show_source_info(self):
        source_file = os.path.abspath(__file__)
        try:
            mtime = os.path.getmtime(source_file)
            last_edit = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
            QMessageBox.information(
                self,
                "Source File Information",
                f"SuperIDE5.py Last Edit Date and Time:\n{last_edit}"
            )
            if self.show_info_logs:
                self.log_to_terminal("Displayed source file last edit time.", "Info")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to retrieve last edit time for SuperIDE5.py:\n{str(e)}"
            )
            if self.show_error_logs:
                self.log_to_terminal(f"Failed to retrieve source file info: {str(e)}", "Error")

    def update_ide_operations_menu(self):
        for action in self.ide_operation_actions:
            self.ide_operations_menu.removeAction(action)
        self.ide_operation_actions.clear()

        menu_font = QFont()
        menu_font.setPointSize(self.menu_font_size)
        f4_action = QAction("Run F4 (List All Tasks)", self)
        f4_action.setShortcut("F4")
        f4_action.setFont(menu_font)
        f4_action.triggered.connect(self.run_f4)
        self.ide_operations_menu.addAction(f4_action)
        self.ide_operation_actions.append(f4_action)
        if self.show_info_logs:
            self.log_to_terminal("Added IDE operation action: Run F4", "Info")

        used_keys = set(["F4"])
        for task in self.tasks:
            key = task.get("key")
            if key and key.startswith("F") and key[1:].isdigit() and 4 <= int(key[1:]) <= 12 and key not in ["F5", "F6", "F7"]:
                if key in used_keys:
                    if self.show_error_logs:
                        self.log_to_terminal(f"Duplicate shortcut {key} for task '{task['label']}' ignored.", "Error")
                    continue
                used_keys.add(key)
                action = QAction(f"Run {key} ({task['label']})", self)
                action.setShortcut(key)
                action.setFont(menu_font)
                action.setData(key)
                action.triggered.connect(lambda checked, k=key: self.execute_task(k))
                self.ide_operations_menu.addAction(action)
                self.ide_operation_actions.append(action)
                if self.show_info_logs:
                    self.log_to_terminal(f"Added IDE operation action: Run {key} ({task['label']})", "Info")

    def get_active_editor(self):
        current_widget = self.tab_widget.currentWidget()
        if isinstance(current_widget, CodeEditor):
            return current_widget
        return None

    def add_new_tab(self, file_path, title):
        editor = CodeEditor(self.color_scheme, file_path)
        editor.set_word_wrap(self.word_wrap)
        self.editors[file_path] = editor
        self.tab_widget.addTab(editor, title)
        self.tab_widget.setCurrentWidget(editor)
        if file_path:
            try:
                with open(file_path, 'r') as file:
                    editor.setPlainText(file.read())
                self.file_watcher.addPath(file_path)
                if self.show_info_logs:
                    self.log_to_terminal(f"Opened file: {file_path}", "Info")
            except Exception as e:
                if self.show_error_logs:
                    self.log_to_terminal(f"Failed to load file {file_path}: {str(e)}", "Error")
        self.current_file = file_path
        self.apply_tab_font()  # Ensure tabs are resized for new tab

    def close_tab(self, index):
        if self.tab_widget.count() > 1:
            widget = self.tab_widget.widget(index)
            file_path = next((k for k, v in self.editors.items() if v == widget), None)
            if file_path and self.save_if_modified(file_path):
                if file_path in self.file_watcher.files():
                    self.file_watcher.removePath(file_path)
                del self.editors[file_path]
                self.tab_widget.removeTab(index)
                if self.show_info_logs:
                    self.log_to_terminal(f"Closed tab: {file_path or 'Untitled'}", "Info")
            elif not file_path:
                reply = QMessageBox.question(self, "Close Tab", "Close untitled tab?", QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.tab_widget.removeTab(index)
                    if self.show_info_logs:
                        self.log_to_terminal("Closed untitled tab.", "Info")
                else:
                    if self.show_info_logs:
                        self.log_to_terminal("Close untitled tab cancelled.", "Info")
        else:
            if self.show_info_logs:
                self.log_to_terminal("Cannot close the last tab.", "Warning")

    def handle_file_changed(self, file_path):
        if file_path not in self.editors:
            self.file_watcher.removePath(file_path)
            return
        editor = self.editors[file_path]
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                disk_content = file.read()
            editor_content = editor.toPlainText()
            if disk_content == editor_content:
                if self.show_info_logs:
                    self.log_to_terminal(f"File {os.path.basename(file_path)} unchanged on disk, skipping reload.", "Info")
                return
        except Exception as e:
            if self.show_error_logs:
                self.log_to_terminal(f"Failed to read file {file_path} for comparison: {str(e)}", "Error")
            return
        if editor.document().isModified():
            reply = QMessageBox.question(
                self, "File Changed Externally",
                f"The file {os.path.basename(file_path)} has been modified externally.\n"
                "Reload from disk? (Unsaved changes will be lost.)",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
            editor.setPlainText(content)
            editor.document().setModified(False)
            if self.show_info_logs:
                self.log_to_terminal(f"File {os.path.basename(file_path)} reloaded due to external change.", "Info")
        except Exception as e:
            if self.show_error_logs:
                self.log_to_terminal(f"Failed to reload file {file_path}: {str(e)}", "Error")

    def save_if_modified(self, file_path):
        if self.show_info_logs:
            self.log_to_terminal(f"Checking save for file: {file_path or 'None'}", "Info")
        editor = self.get_active_editor()
        if not editor:
            if self.show_error_logs:
                self.log_to_terminal("No active editor found for save.", "Error")
            return False
        if not editor.document().isModified():
            if self.show_info_logs:
                self.log_to_terminal(f"File {file_path or 'Untitled'} is not modified, skipping save.", "Info")
            return True
        if not file_path:
            if self.show_info_logs:
                self.log_to_terminal("No file path, prompting save as.", "Info")
            saved = self.save_file_as()
            if self.show_info_logs:
                self.log_to_terminal(f"Save as result: {saved}", "Info")
            return saved
        if editor.file_path != file_path:
            if self.show_error_logs:
                self.log_to_terminal(f"File path mismatch: editor.file_path={editor.file_path}, requested={file_path}", "Error")
            return False
        if self.show_info_logs:
            self.log_to_terminal(f"Editor modified: {editor.document().isModified()}, file exists: {os.path.exists(file_path)}", "Info")
        if self.show_save_confirmation:
            reply = QMessageBox.question(
                self, "Save Changes",
                f"Save changes to {os.path.basename(file_path)} before proceeding?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if reply == QMessageBox.No:
                return True  # Proceed without saving
            if reply == QMessageBox.Cancel:
                return False  # Cancel operation
        try:
            directory = os.path.dirname(file_path)
            if not os.path.exists(directory):
                os.makedirs(directory)
                if self.show_info_logs:
                    self.log_to_terminal(f"Created directory: {directory}", "Info")
            if os.path.exists(file_path):
                if not os.access(file_path, os.W_OK):
                    if self.show_error_logs:
                        self.log_to_terminal(f"File {file_path} is not writable.", "Error")
                    return False
            was_watched = False
            if file_path in self.file_watcher.files():
                self.file_watcher.removePath(file_path)
                was_watched = True
                if self.show_info_logs:
                    self.log_to_terminal(f"Temporarily removed {file_path} from file watcher.", "Info")
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(editor.toPlainText())
            editor.document().setModified(False)
            if was_watched:
                self.file_watcher.addPath(file_path)
                if self.show_info_logs:
                    self.log_to_terminal(f"Restored {file_path} to file watcher.", "Info")
            if self.show_info_logs:
                self.log_to_terminal(f"File {os.path.basename(file_path)} saved successfully.", "Info")
            return True
        except PermissionError as e:
            if self.show_error_logs:
                self.log_to_terminal(f"Permission error saving file {file_path}: {str(e)}", "Error")
            return False
        except IOError as e:
            if self.show_error_logs:
                self.log_to_terminal(f"IO error saving file {file_path}: {str(e)}", "Error")
            return False
        except Exception as e:
            if self.show_error_logs:
                self.log_to_terminal(f"Unexpected error saving file {file_path}: {str(e)}", "Error")
            return False

    def run_f4(self):
        # Save modified file before showing tasks
        editor = self.get_active_editor()
        if editor and editor.document().isModified():
            if not self.save_if_modified(editor.file_path):
                if self.show_info_logs:
                    self.log_to_terminal("F4 task aborted due to save cancellation.", "Info")
                return
        menu = QMenu(self)
        menu_font = QFont()
        menu_font.setPointSize(self.menu_font_size)
        menu.setFont(menu_font)
        sorted_tasks = sorted(self.tasks, key=lambda x: self.task_usage.get(x["label"], 0), reverse=True)
        for task in sorted_tasks:
            action = QAction(task["label"], self)
            action.setFont(menu_font)
            action.triggered.connect(lambda checked, label=task["label"]: self.run_task(label))
            menu.addAction(action)
        search_action = QAction("Search Tasks...", self)
        search_action.setFont(menu_font)
        search_action.triggered.connect(self.search_tasks)
        menu.addAction(search_action)
        menu.exec_(self.mapToGlobal(QPoint(0, 0)))

    def execute_task(self, key):
        if self.show_info_logs:
            self.log_to_terminal(f"execute_task: Starting for key: {key}", "Info")
        editor = self.get_active_editor()
        if editor and editor.document().isModified():
            if self.show_info_logs:
                self.log_to_terminal(f"Saving modified file {editor.file_path or 'Untitled'} before task {key}.", "Info")
            if not self.save_if_modified(editor.file_path):
                if self.show_info_logs:
                    self.log_to_terminal(f"Task {key} aborted due to save failure or cancellation.", "Info")
                return
        task_found = False
        for task in self.tasks:
            if task.get("key") == key:
                task_found = True
                label = task.get("label", "Unnamed Task")
                if self.show_info_logs:
                    self.log_to_terminal(f"execute_task: Found task '{label}' for key {key}", "Info")
                self.run_task(label)
                if self.show_info_logs:
                    self.log_to_terminal(f"execute_task: Task '{label}' execution triggered.", "Info")
                break
        if not task_found:
            if self.show_error_logs:
                self.log_to_terminal(f"execute_task: No task found for key: {key}", "Error")

    def run_task(self, label):
        self.clear_terminal()
        for task in self.tasks:
            if task["label"] == label:
                if "Open ASM" in label:
                    if not self.current_file:
                        if self.show_info_logs:
                            self.log_to_terminal("No current file to derive ASM from.", "Warning")
                        break
                    asm_file = os.path.splitext(self.current_file)[0] + ".ASM"
                    if os.path.isfile(asm_file):
                        if asm_file not in self.editors:
                            self.add_new_tab(asm_file, f"ASM: {os.path.basename(asm_file)}")
                            self.task_usage[label] = self.task_usage.get(label, 0) + 1
                            self.save_settings()
                            if self.show_info_logs:
                                self.log_to_terminal(f"Opened ASM file: {asm_file} in new tab", "Success")
                        else:
                            self.tab_widget.setCurrentWidget(self.editors[asm_file])
                            if self.show_info_logs:
                                self.log_to_terminal(f"ASM file: {asm_file} is already open", "Info")
                    else:
                        if self.show_error_logs:
                            self.log_to_terminal(f"ASM file not found: {asm_file}", "Error")
                    break

                command = task.get("command", "")
                args = task.get("args", [])
                options = task.get("options", {})
                cwd = options.get("cwd", os.getcwd()) if options else os.getcwd()
                resolved_cwd = self.resolve_placeholders(cwd)
                resolved_command = self.resolve_placeholders(command)
                resolved_args = [self.resolve_placeholders(arg) for arg in args]
                if resolved_command:
                    try:
                        if not os.path.isfile(resolved_command):
                            raise FileNotFoundError(f"Command not found: {resolved_command}")
                        if "${execPath}" in command.lower():
                            filename = next((arg for arg in resolved_args if os.path.isfile(arg)), None)
                            if filename:
                                if ' ' in filename:
                                    filename = f'"{filename}"'
                                subprocess.Popen([resolved_command, "/K", filename], shell=True, cwd=resolved_cwd)
                                if self.show_info_logs:
                                    self.log_to_terminal(f"Opened terminal for '{label}' with file: {filename}", "Success")
                            else:
                                raise ValueError("No valid filename found in args for terminal execution")
                        else:
                            self.compilation_index += 1
                            process = subprocess.run(
                                [resolved_command] + resolved_args, 
                                check=False,
                                shell=True, 
                                capture_output=True, 
                                text=True, 
                                cwd=resolved_cwd
                            )
                            self.task_usage[label] = self.task_usage.get(label, 0) + 1
                            self.save_settings()
                            if process.stdout:
                                self.log_to_terminal(f"Compiler Output:\n{process.stdout}", "Info")
                            if process.stderr and self.show_error_logs:
                                self.log_to_terminal(f"Compiler Errors:\n{process.stderr}", "Error")
                            if process.returncode == 3735928559 and self.show_info_logs:
                                self.log_to_terminal("GCBASIC.EXE returned DEADBEEF, indicating errors.txt was generated.", "Info")
                            elif process.returncode != 0 and self.show_error_logs:
                                self.log_to_terminal(f"Task '{label}' failed with exit code {process.returncode}.", "Error")
                            elif self.show_info_logs:
                                self.log_to_terminal(f"Task '{label}' executed successfully.", "Success")

                        if "gcbasic.exe" in resolved_command.lower():
                            if self.current_file:
                                source_dir = os.path.dirname(self.current_file)
                                source_errors_file = os.path.join(source_dir, "errors.txt")
                                if os.path.exists(source_errors_file):
                                    try:
                                        with open(source_errors_file, 'r') as f:
                                            errors_content = f.read()
                                        if self.show_error_logs:
                                            self.log_to_terminal(f"Build Errors from errors.txt (source dir):\n{errors_content or 'File is empty'}", "Error")
                                    except Exception as e:
                                        if self.show_error_logs:
                                            self.log_to_terminal(f"Failed to read errors.txt from source dir: {str(e)}", "Error")

                            cwd_errors_file = os.path.join(resolved_cwd, "errors.txt")
                            if os.path.exists(cwd_errors_file):
                                try:
                                    with open(cwd_errors_file, 'r') as f:
                                        errors_content = f.read()
                                    if self.show_error_logs:
                                        self.log_to_terminal(f"Build Errors from errors.txt (cwd):\n{errors_content or 'File is empty'}", "Error")
                                except Exception as e:
                                    if self.show_error_logs:
                                        self.log_to_terminal(f"Failed to read errors.txt from cwd: {str(e)}", "Error")

                            ide_cwd = os.getcwd()
                            ide_errors_file = os.path.join(ide_cwd, "errors.txt")
                            if os.path.exists(ide_errors_file):
                                try:
                                    with open(ide_errors_file, 'r') as f:
                                        errors_content = f.read()
                                    if self.show_error_logs:
                                        self.log_to_terminal(f"Build Errors from errors.txt (IDE cwd):\n{errors_content or 'File is empty'}", "Error")
                                except Exception as e:
                                    if self.show_error_logs:
                                        self.log_to_terminal(f"Failed to read errors.txt from IDE cwd: {str(e)}", "Error")

                    except subprocess.CalledProcessError as e:
                        error_message = e.stderr or str(e)
                        if self.show_error_logs:
                            self.log_to_terminal(f"Failed to execute task '{label}': {error_message}", "Error")
                    except PermissionError as e:
                        if self.show_error_logs:
                            self.log_to_terminal(f"Failed to execute task '{label}': Access denied. Check permissions for {resolved_command} or run as administrator.", "Error")
                    except FileNotFoundError as e:
                        if self.show_error_logs:
                            self.log_to_terminal(f"Failed to execute task '{label}': {str(e)}", "Error")
                    except ValueError as e:
                        if self.show_error_logs:
                            self.log_to_terminal(f"Failed to execute task '{label}': {str(e)}", "Error")
                    except Exception as e:
                        if self.show_error_logs:
                            self.log_to_terminal(f"Failed to execute task '{label}': {str(e)}", "Error")
                break

    def resolve_placeholders(self, value):
        if not isinstance(value, str):
            return value
        env_var_match = re.match(r'\${env:([^}]+)}', value)
        if env_var_match:
            env_var = env_var_match.group(1)
            resolved = os.getenv(env_var, self.gcbasic_path or "")
            return resolved + value[len(f"${{env:{env_var}}}"):].replace('/', '\\')
        
        if value.lower() == "${execPath}" or value == '"${execPath}"' or value == "'${execPath}'":
            return "cmd.exe"
        
        if self.current_file:
            if value == "${file}":
                resolved = self.current_file
            elif value == "${fileDirname}":
                resolved = os.path.dirname(self.current_file)
            elif value == "${fileBasenameNoExtension}":
                resolved = os.path.splitext(os.path.basename(self.current_file))[0]
            elif value == "'${file}'" or value == '"${file}"':
                resolved = self.current_file
                if ' ' in resolved:
                    return f'"{resolved}"'
                return resolved
            elif value == "'${fileDirname}\\${fileBasenameNoExtension}.ASM'" or value == '"${fileDirname}\\${fileBasenameNoExtension}.ASM"':
                resolved = os.path.join(os.path.dirname(self.current_file), f"{os.path.splitext(os.path.basename(self.current_file))[0]}.ASM")
                if ' ' in resolved:
                    return f'"{resolved}"'
                return resolved
            elif value == "'${fileDirname}\\${fileBasenameNoExtension}.S'" or value == '"${fileDirname}\\${fileBasenameNoExtension}.S"':
                resolved = os.path.join(os.path.dirname(self.current_file), f"{os.path.splitext(os.path.basename(self.current_file))[0]}.S")
                if ' ' in resolved:
                    return f'"{resolved}"'
                return resolved
        
        return value

    def search_tasks(self):
        dialog = TaskSearchDialog(self, self.tasks)
        dialog.move(self.mapToGlobal(QPoint(0, 0)))
        dialog.exec_()

    def load_tasks(self):
        if not os.path.exists(self.tasks_file):
            if self.show_info_logs:
                self.log_to_terminal(f"Tasks file not found at {self.tasks_file}", "Warning")
            self.tasks_file, _ = QFileDialog.getOpenFileName(self, "Select Tasks JSON File", "", "JSON Files (*.json);;All Files (*)")
            if not self.tasks_file:
                self.tasks = []
                return
            self.save_settings()
        
        try:
            with open(self.tasks_file, 'r') as file:
                content = file.read()
            data = json5.loads(content)
            self.tasks = data.get("tasks", [])
            key_counts = {}
            for task in self.tasks:
                label = task.get("label", "")
                match = re.search(r'\[(F\d+|Shift\+F\d+)\]', label)
                if match:
                    key = match.group(1).replace("Shift+", "")
                    task["key"] = key
                    key_counts[key] = key_counts.get(key, 0) + 1
                else:
                    task["key"] = None
            for key, count in key_counts.items():
                if count > 1 and self.show_error_logs:
                    self.log_to_terminal(f"Warning: Duplicate shortcut {key} found {count} times in tasks.json.", "Error")
            self.update_ide_operations_menu()
        except json5.JSON5DecodeError as e:
            if self.show_error_logs:
                self.log_to_terminal(f"Failed to parse tasks.json: {str(e)}\nPlease ensure the file contains valid JSON5.", "Error")
            self.tasks = []
        except Exception as e:
            if self.show_error_logs:
                self.log_to_terminal(f"Failed to load tasks: {str(e)}", "Error")
            self.tasks = []

    def new_file(self):
        self.add_new_tab(None, "Untitled")

    def save_file(self):
        editor = self.get_active_editor()
        if not editor or not editor.file_path:
            self.save_file_as()
            return
        if editor.document().isModified():
            try:
                with open(editor.file_path, 'w', encoding='utf-8') as file:
                    file.write(editor.toPlainText())
                editor.document().setModified(False)
                if self.show_info_logs:
                    self.log_to_terminal(f"File {os.path.basename(editor.file_path)} saved successfully.", "Success")
                self.save_settings()
            except Exception as e:
                if self.show_error_logs:
                    self.log_to_terminal(f"Failed to save file {editor.file_path}: {str(e)}", "Error")

    def save_file_as(self):
        file_name, _ = QFileDialog.getSaveFileName(self, "Save File As", "", "GCBASIC Files (*.gcb);;All Files (*)")
        if file_name:
            editor = self.get_active_editor()
            if not editor:
                if self.show_error_logs:
                    self.log_to_terminal("No active editor for save as.", "Error")
                return False
            try:
                directory = os.path.dirname(file_name)
                if not os.path.exists(directory):
                    os.makedirs(directory)
                    if self.show_info_logs:
                        self.log_to_terminal(f"Created directory: {directory}", "Info")
                with open(file_name, 'w', encoding='utf-8') as file:
                    file.write(editor.toPlainText())
                old_file = editor.file_path
                self.current_file = file_name
                if old_file in self.editors:
                    if old_file in self.file_watcher.files():
                        self.file_watcher.removePath(old_file)
                    del self.editors[old_file]
                self.editors[file_name] = editor
                editor.file_path = file_name
                self.tab_widget.setTabText(self.tab_widget.currentIndex(), os.path.basename(file_name))
                editor.document().setModified(False)
                self.recent_files.add(file_name)
                self.file_watcher.addPath(file_name)
                self.save_settings()
                if self.show_info_logs:
                    self.log_to_terminal(f"File saved as {file_name}.", "Success")
                return True
            except Exception as e:
                if self.show_error_logs:
                    self.log_to_terminal(f"Failed to save file as {file_name}: {str(e)}", "Error")
                return False
        else:
            if self.show_info_logs:
                self.log_to_terminal("Save as cancelled by user.", "Info")
            return False

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open File", "", "GCBASIC Files (*.gcb);;All Files (*)")
        if file_name:
            if file_name not in self.editors:
                self.add_new_tab(file_name, os.path.basename(file_name))
            else:
                self.tab_widget.setCurrentWidget(self.editors[file_name])
            self.recent_files.add(file_name)
            self.save_settings()

    def show_recent_files(self):
        if not self.recent_files:
            if self.show_info_logs:
                self.log_to_terminal("No recent files.", "Info")
            return
        menu = QMenu(self)
        recent_font = QFont()
        recent_font.setPointSize(self.recent_files_font_size)
        menu.setFont(recent_font)
        for file_path in sorted(list(self.recent_files), reverse=True):
            action = QAction(file_path, self)
            action.setFont(recent_font)
            action.triggered.connect(lambda checked, fp=file_path: self.open_recent_file(fp))
            menu.addAction(action)
        menu.exec_(self.mapToGlobal(self.tab_widget.pos()))

    def open_recent_file(self, file_path):
        if os.path.exists(file_path):
            if file_path not in self.editors:
                self.add_new_tab(file_path, os.path.basename(file_path))
            else:
                self.tab_widget.setCurrentWidget(self.editors[file_path])
        else:
            self.recent_files.remove(file_path)
            self.save_settings()
            if self.show_info_logs:
                self.log_to_terminal(f"File {file_path} no longer exists.", "Warning")

    def find(self):
        search_text, ok = QInputDialog.getText(self, "Find", "Enter text to find:")
        if ok and search_text:
            editor = self.get_active_editor()
            if editor:
                cursor = editor.document().find(search_text, editor.textCursor())
                if not cursor.isNull():
                    editor.setTextCursor(cursor)
                else:
                    if self.show_info_logs:
                        self.log_to_terminal("Text not found.", "Info")

    def replace(self):
        editor = self.get_active_editor()
        if not editor:
            return
        search_text, ok = QInputDialog.getText(self, "Replace", "Enter text to find:")
        if not ok or not search_text:
            return
        replace_text, ok = QInputDialog.getText(self, "Replace", "Enter replacement text:")
        if not ok:
            return
        cursor = editor.document().find(search_text, editor.textCursor())
        if not cursor.isNull():
            cursor.insertText(replace_text)
            editor.setTextCursor(cursor)
            if self.show_info_logs:
                self.log_to_terminal("Text replaced.", "Info")
        else:
            if self.show_info_logs:
                self.log_to_terminal("Text not found.", "Info")

    def select_line(self):
        editor = self.get_active_editor()
        if editor:
            cursor = editor.textCursor()
            cursor.select(QTextCursor.LineUnderCursor)
            editor.setTextCursor(cursor)

    def open_settings(self):
        editor = self.get_active_editor()
        dialog = SettingsDialog(
            self, 
            self.color_scheme.name, 
            editor.show_line_numbers if editor else True, 
            self.tasks_file, 
            self.gcbasic_path,
            self.show_info_logs,
            self.show_error_logs,
            self.geometry().width(),
            self.geometry().height(),
            self.menu_font_size,
            self.recent_files_font_size,
            self.tab_font_size,
            self.word_wrap,
            self.show_save_confirmation
        )
        dialog.exec_()

    def set_color_scheme(self, scheme_name):
        self.color_scheme = ColorScheme(scheme_name)
        self.apply_color_scheme()
        self.save_settings()

    def apply_color_scheme(self):
        for editor in self.editors.values():
            editor.setStyleSheet(
                f"background-color: {self.color_scheme.colors['background']}; "
                f"color: {self.color_scheme.colors['foreground']};"
            )
            editor.viewport().update()
        self.terminal_text.setStyleSheet(
            f"background-color: {self.color_scheme.colors['background']}; "
            f"color: {self.color_scheme.colors['foreground']};"
        )

    def toggle_line_numbers(self, show):
        for editor in self.editors.values():
            editor.toggle_line_numbers(show)

    def load_settings(self):
        settings_file = os.path.expanduser("~/.GCBASICEssentialIDE/settings.json")
        if os.path.exists(settings_file):
            try:
                with open(settings_file, 'r') as file:
                    settings = json5.load(file)
                self.tools = settings.get("tools", self.tools)
                self.microcontroller = settings.get("microcontroller", self.microcontroller)
                scheme_name = settings.get("color_scheme", "Light")
                self.color_scheme = ColorScheme(scheme_name)
                show_line_numbers = settings.get("show_line_numbers", True)
                self.word_wrap = settings.get("word_wrap", True)
                self.show_info_logs = settings.get("show_info_logs", True)
                self.show_error_logs = settings.get("show_error_logs", True)
                self.show_save_confirmation = settings.get("show_save_confirmation", True)
                self.menu_font_size = settings.get("menu_font_size", 12)
                self.recent_files_font_size = settings.get("recent_files_font_size", 10)
                self.tab_font_size = settings.get("tab_font_size", 10)
                for editor in self.editors.values():
                    editor.show_line_numbers = show_line_numbers
                    editor.toggle_line_numbers(show_line_numbers)
                    editor.set_word_wrap(self.word_wrap)
                self.recent_files = set(settings.get("recent_files", []))
                self.tasks_file = settings.get("tasks_file", self.tasks_file)
                self.gcbasic_path = settings.get("gcbasic_path", "")
                self.task_usage = settings.get("task_usage", {})
                window_geometry = settings.get("window_geometry", {})
                window_width = settings.get("window_width", int(QApplication.primaryScreen().availableGeometry().width() * 0.75))
                window_height = settings.get("window_height", int(QApplication.primaryScreen().availableGeometry().height() * 0.75))
                if window_geometry:
                    self.setGeometry(
                        window_geometry.get("x", 100),
                        window_geometry.get("y", 100),
                        window_width,
                        window_height
                    )
                else:
                    self.resize(window_width, window_height)
                self.apply_color_scheme()
                self.apply_menu_font()
                self.apply_tab_font()
            except Exception as e:
                if self.show_error_logs:
                    self.log_to_terminal(f"Failed to load settings: {str(e)}", "Warning")

    def save_settings(self):
        settings = {
            "tools": self.tools,
            "microcontroller": self.microcontroller,
            "color_scheme": self.color_scheme.name,
            "show_line_numbers": self.get_active_editor().show_line_numbers if self.get_active_editor() else True,
            "word_wrap": self.word_wrap,
            "recent_files": list(self.recent_files),
            "tasks_file": self.tasks_file,
            "gcbasic_path": self.gcbasic_path,
            "task_usage": self.task_usage,
            "show_info_logs": self.show_info_logs,
            "show_error_logs": self.show_error_logs,
            "show_save_confirmation": self.show_save_confirmation,
            "menu_font_size": self.menu_font_size,
            "recent_files_font_size": self.recent_files_font_size,
            "tab_font_size": self.tab_font_size,
            "window_geometry": {
                "x": self.geometry().x(),
                "y": self.geometry().y(),
                "width": self.geometry().width(),
                "height": self.geometry().height()
            },
            "window_width": self.geometry().width(),
            "window_height": self.geometry().height()
        }
        settings_file = os.path.expanduser("~/.GCBASICEssentialIDE/settings.json")
        try:
            with open(settings_file, 'w') as file:
                json5.dump(settings, file, indent=4)
        except Exception as e:
            if self.show_error_logs:
                self.log_to_terminal(f"Failed to save settings: {str(e)}", "Warning")

    def closeEvent(self, event):
        for file_path, editor in list(self.editors.items()):
            if editor.document().isModified():
                reply = QMessageBox.question(
                    self, "Unsaved Changes",
                    f"Save changes to {os.path.basename(file_path) if file_path else 'Untitled'} before closing?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
                )
                if reply == QMessageBox.Yes:
                    if file_path:
                        self.save_if_modified(file_path)
                    else:
                        if not self.save_file_as():
                            event.ignore()
                            return
                elif reply == QMessageBox.Cancel:
                    event.ignore()
                    return
        self.save_settings()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ide = GCBASICEssentialIDE()
    ide.show()
    sys.exit(app.exec_())