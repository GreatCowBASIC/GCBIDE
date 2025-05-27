import sys
import os
import os.path
import json
import re
import html
import socket
import errno
import subprocess
import tempfile
import time
import webbrowser
import glob
import shutil
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
                             QMenuBar, QAction, QFileDialog, QDockWidget, QListWidget, QMessageBox,
                             QInputDialog, QMenu, QFrame, QDialog, QDialogButtonBox, QTextBrowser, QComboBox,
                             QPushButton, QHBoxLayout, QLabel, QFontDialog)
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
from PyQt5.QtGui import QTextOption, QTextDocument, QFont, QPainter, QFontMetrics, QTextCursor, QIcon, QTextCharFormat, QColor, QImage
from PyQt5.QtCore import Qt, QUrl, QPoint, QTimer, QRect, QByteArray, QSize, QEvent
from PyQt5.QtGui import QDesktopServices, QTextBlockUserData, QFontDatabase
from collections import deque
import uuid

#build number
BUILD_NUMBER = "05.26.2025"

# Global flag for HL: INFO messages (not user-settable)
SHOW_HL_INFO = False  # Disabled to reduce clutter
SHOW_FONT_CONTROL = False
SHOW_BAR_CONTROL = False  # Disabled to reduce clutter
SHOW_FILE_INFO = False
SHOW_TASK_INFO = False
SHOW_TERMINAL_INFO = False
SHOW_RULES_INFO = False

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
        font = QFont(self.editor.ide.settings["editor_font"], self.editor.ide.settings["editor_font_size"])
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
                        painter.drawPolygon(triangle_points)
                    else:
                        painter.setPen(Qt.black if self.editor.ide.settings["theme"] == "light" else Qt.white)
                        painter.drawText(0, int(y_pos - ascent), self.width() - 5, fm.height(), Qt.AlignRight, number)
            block = block.next()
            block_number += 1

class TextBlockData(QTextBlockUserData):
    def __init__(self, text, in_block_comment=False):
        super().__init__()
        self.text = text
        self.in_block_comment = in_block_comment

    def get_in_block_comment(self):
        return self.in_block_comment

class SyntaxHighlighter:
    def __init__(self, text_edit, ide):
        self.text_edit = text_edit
        self.ide = ide
        self.highlighting_rules = []
        self.block_comment_start = None
        self.block_comment_end = None
        self.highlight_timer = QTimer()
        self.highlight_timer.setSingleShot(True)
        self.highlight_timer.timeout.connect(self._apply_highlighting)
        self.highlight_timer.setInterval(500)
        self.highlight_pending = False
        self.last_visible_range = None
        self.highlighted_blocks = set()
        self.load_highlighting_rules()
        self.text_edit.document().contentsChange.connect(self.on_contents_change)
        self.pending_changes = []

    def load_highlighting_rules(self):
        """Load highlighting rules from JSON configuration in user directory, copying from fallback if needed."""
        language_file = self.ide.settings.get("language_file")
        config_dir = os.path.expanduser("~/.superide")
        if not self.ide.settings.get("last_folder"):
            self.ide.settings["last_folder"] = os.path.expanduser("~")
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        
        if not os.path.exists(language_file):
            fallback_path = resource_path("GCB.tmLanguage.json")
            if os.path.exists(fallback_path):
                try:
                    import shutil
                    shutil.copy(fallback_path, language_file)
                    self.ide.settings["language_file"] = language_file
                    self.ide.save_settings()
                    if SHOW_HL_INFO:
                        self.ide.terminal.log(f"HL: Copied language file from {fallback_path} to {language_file}", "INFO")
                except Exception as e:
                    if SHOW_HL_INFO:
                        self.ide.terminal.log(f"HL: Error copying language file to {language_file}: {str(e)}", "ERROR")
                    return
            else:
                if SHOW_HL_INFO:
                    self.ide.terminal.log(f"HL: Language file not found at {language_file} or {fallback_path}", "ERROR")
                return

        if os.path.exists(language_file):
            try:
                with open(language_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.highlighting_rules = []
                    if SHOW_RULES_INFO:
                        self.ide.terminal.log(f"HL: Loaded language file from {language_file}", "INFO")

                    try:
                        block_start = config.get("block_comment_start", r'/\*')
                        self.block_comment_start = re.compile(block_start)
                        if SHOW_HL_INFO:
                            self.ide.terminal.log(f"HL: Loaded block comment start: {block_start}", "INFO")
                    except re.error as e:
                        if SHOW_HL_INFO:
                            self.ide.terminal.log(f"HL: Invalid block comment start pattern '{block_start}': {str(e)}", "ERROR")
                        self.block_comment_start = None

                    try:
                        block_end = config.get("block_comment_end", r'\*/')
                        self.block_comment_end = re.compile(block_end)
                        if SHOW_HL_INFO:
                            self.ide.terminal.log(f"HL: Loaded block comment end: {block_end}", "INFO")
                    except re.error as e:
                        if SHOW_HL_INFO:
                            self.ide.terminal.log(f"HL: Invalid block comment end pattern '{block_end}': {str(e)}", "ERROR")
                        self.block_comment_end = None

                    for rule in config.get("patterns", []):
                        try:
                            pattern = rule["match"]
                            color = QColor(rule["color"])
                            bold = rule.get("bold", False)
                            italic = rule.get("italic", False)
                            case_insensitive = rule.get("case_insensitive", False)
                            format = QTextCharFormat()
                            format.setForeground(color)
                            if bold:
                                format.setFontWeight(QFont.Bold)
                            if italic:
                                format.setFontItalic(True)
                            flags = re.IGNORECASE if case_insensitive else 0
                            compiled_pattern = re.compile(pattern, flags)
                            self.highlighting_rules.append((compiled_pattern, format))
                            if SHOW_RULES_INFO:
                                self.ide.terminal.log(f"HL: Loaded rule - Pattern: {pattern}, Color: {rule['color']}, Case Insensitive: {case_insensitive}", "INFO")
                        except re.error as e:
                                self.ide.terminal.log(f"HL: Invalid regex pattern '{rule.get('match', 'unknown')}' in JSON: {str(e)}", "ERROR")
                        except Exception as e:
                                self.ide.terminal.log(f"HL: Error processing rule {rule.get('match', 'unknown')}: {str(e)}", "ERROR")
            except json.JSONDecodeError as e:
                if SHOW_HL_INFO:
                    self.ide.terminal.log(f"HL: Corrupted JSON in {language_file}: {str(e)}", "ERROR")
            except Exception as e:
                if SHOW_HL_INFO:
                    self.ide.terminal.log(f"HL: Error loading {language_file}: {str(e)}", "ERROR")
        else:
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: Language file not found at {language_file} after copy attempt", "ERROR")

    def schedule_highlighting(self):
        if SHOW_HL_INFO:
            self.ide.terminal.log("HL: Scheduling highlighting", "INFO")
        if self.highlight_pending:
            self.highlight_timer.stop()  # Reset timer
            self.pending_changes.clear()  # Clear stale changes
        self.highlight_pending = True
        self.highlight_timer.start()

    def on_contents_change(self, position, chars_removed, chars_added):
        if chars_removed > 0 or chars_added > 0:
            doc = self.text_edit.document()
            start_block = doc.findBlock(position)
            end_position = position + chars_added
            end_block = doc.findBlock(end_position)
            if not end_block.isValid():
                end_block = doc.lastBlock()
            start_block_num = start_block.blockNumber()
            end_block_num = end_block.blockNumber()
            self.pending_changes.append((start_block_num, end_block_num))
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: Contents changed - pos: {position}, removed: {chars_removed}, added: {chars_added}, blocks: {start_block_num}-{end_block_num}", "INFO")
            self.schedule_highlighting()

    def _apply_highlighting(self):
        if SHOW_HL_INFO:
            self.ide.terminal.log("HL: Applying highlighting", "INFO")
        if not hasattr(self.text_edit, "file_path"):
            self.highlight_pending = False
            if SHOW_HL_INFO:
                self.ide.terminal.log("HL: No file_path, skipping highlighting", "INFO")
            return
        # Allow highlighting for .gcb files, including unsaved
        if not self.text_edit.file_path.lower().endswith(".gcb"):
            self.highlight_pending = False
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: File {self.text_edit.file_path} is not .gcb, skipping highlighting", "INFO")
            return
        doc = self.text_edit.document()
        was_modified = doc.isModified()
        cursor = self.text_edit.cursorForPosition(self.text_edit.viewport().pos())
        first_visible_block = doc.findBlock(cursor.position())
        # Use bottomRight to include partially visible bottom line
        last_visible_block = doc.findBlock(self.text_edit.cursorForPosition(
            self.text_edit.viewport().rect().bottomRight()).position())
        # Extend to next block if partially visible
        if last_visible_block.isValid():
            next_block = last_visible_block.next()
            if next_block.isValid() and next_block.layout().position().y() < self.text_edit.viewport().rect().bottom():
                last_visible_block = next_block
        visible_range = (first_visible_block.blockNumber(), last_visible_block.blockNumber() if last_visible_block.isValid() else doc.blockCount() - 1)
        if SHOW_HL_INFO:
            self.ide.terminal.log(f"HL: Visible range: {visible_range[0]}-{visible_range[1]}", "INFO")
        blocks_to_highlight = set()
        # Add pending changes
        for start_block_num, end_block_num in self.pending_changes:
            for block_num in range(start_block_num, end_block_num + 1):
                block = doc.findBlockByNumber(block_num)
                if block.isValid():
                    blocks_to_highlight.add(block_num)
                    # Clear highlighted_blocks for modified blocks
                    if block_num in self.highlighted_blocks:
                        self.highlighted_blocks.remove(block_num)
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: Added pending blocks {start_block_num}-{end_block_num} to highlight", "INFO")
        self.pending_changes.clear()
        # Add all visible blocks if none pending
        if not blocks_to_highlight:
            block = first_visible_block
            while block.isValid() and block.blockNumber() <= visible_range[1]:
                block_num = block.blockNumber()
                blocks_to_highlight.add(block_num)
                if block_num in self.highlighted_blocks:
                    self.highlighted_blocks.remove(block_num)
                block = block.next()
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: No pending changes, highlighting all visible blocks {visible_range[0]}-{visible_range[1]}", "INFO")
        if not blocks_to_highlight:
            if SHOW_HL_INFO:
                self.ide.terminal.log("HL: No blocks to highlight", "INFO")
            self.highlight_pending = False
            return
        in_block_comment = False
        block = doc.firstBlock()
        while block.isValid() and block.blockNumber() < visible_range[0]:
            block_data = block.userData()
            if block_data:
                in_block_comment = block_data.get_in_block_comment()
            block = block.next()
        max_total_ranges = 10000
        was_undo_enabled = doc.isUndoRedoEnabled()
        doc.setUndoRedoEnabled(False)
        try:
            for block_num in sorted(blocks_to_highlight):
                block = doc.findBlockByNumber(block_num)
                if not block.isValid():
                    if SHOW_HL_INFO:
                        self.ide.terminal.log(f"HL: Invalid block number {block_num}, skipping", "ERROR")
                    continue
                text = block.text()
                if SHOW_HL_INFO:
                    self.ide.terminal.log(f"HL: Highlighting block {block_num}: {text[:50]}...", "INFO")
                block_length = len(text)
                format_ranges = []
                block_number = block.blockNumber()
                if in_block_comment:
                    end_match = self.block_comment_end.search(text)
                    if end_match and end_match.end() <= block_length:
                        format_ranges.append((0, end_match.end(), self.highlighting_rules[0][1]))
                        in_block_comment = False
                    else:
                        format_ranges.append((0, block_length, self.highlighting_rules[0][1]))
                else:
                    start_match = self.block_comment_start.search(text)
                    if start_match:
                        start_pos = start_match.start()
                        end_match = self.block_comment_end.search(text, start_pos)
                        if end_match and end_match.end() <= block_length:
                            format_ranges.append((start_pos, end_match.end(), self.highlighting_rules[0][1]))
                        else:
                            format_ranges.append((start_pos, block_length, self.highlighting_rules[0][1]))
                            in_block_comment = True
                if not in_block_comment:
                    max_matches = 1000
                    for pattern, format in self.highlighting_rules[1:]:
                        match_count = 0
                        for match in pattern.finditer(text):
                            if match_count >= max_matches:
                                if SHOW_HL_INFO:
                                    self.ide.terminal.log(f"HL: Reached max matches ({max_matches}) for pattern {pattern.pattern}", "WARNING")
                                break
                            start, end = match.start(), match.end()
                            overlaps = False
                            for r_start, r_end, _ in format_ranges:
                                if (start >= r_start and start < r_end) or (end > r_start and end <= r_end) or (start <= r_start and end >= r_end):
                                    overlaps = True
                                    break
                            if not overlaps and end <= block_length:
                                format_ranges.append((start, end, format))
                            match_count += 1
                if len(format_ranges) > max_total_ranges:
                    if SHOW_HL_INFO:
                        self.ide.terminal.log(f"HL: Exceeded max total ranges ({max_total_ranges}) in block {block_number}", "ERROR")
                    continue
                cursor = QTextCursor(block)
                cursor.beginEditBlock()
                try:
                    cursor.setPosition(block.position())
                    cursor.setPosition(block.position() + block_length, QTextCursor.KeepAnchor)
                    cursor.setCharFormat(QTextCharFormat())
                    for start, end, format in format_ranges:
                        if end > block_length:
                            if SHOW_HL_INFO:
                                self.ide.terminal.log(f"HL: Skipping invalid range ({start}, {end}) in block {block_number}", "ERROR")
                            continue
                        cursor.setPosition(block.position() + start)
                        cursor.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                        cursor.mergeCharFormat(format)
                finally:
                    cursor.endEditBlock()
                self.highlighted_blocks.add(block_num)
                block.setUserData(TextBlockData(block.text(), in_block_comment))
        finally:
            doc.setUndoRedoEnabled(was_undo_enabled)
            doc.setModified(was_modified)
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: After highlighting - isUndoAvailable: {doc.isUndoAvailable()}, isModified: {doc.isModified()}", "INFO")
            self.highlight_pending = False
        self.last_visible_range = visible_range


class CustomTextEdit(QTextEdit):
    def __init__(self, ide):
        super().__init__()
        self.ide = ide
        self.setUndoRedoEnabled(True)
        if SHOW_HL_INFO:
            self.ide.terminal.log(f"HL: CustomTextEdit initialized - undoRedoEnabled: {self.isUndoRedoEnabled()}, document undoRedoEnabled: {self.document().isUndoRedoEnabled()}", "INFO")
        self.line_number_area = LineNumberArea(self)
        self.highlighter = SyntaxHighlighter(self, ide)
        self.document().blockCountChanged.connect(self.update_line_number_area_width)
        self.cursorPositionChanged.connect(self.update_line_number_area)
        self.textChanged.connect(self.on_text_changed)
        self.update_line_number_area_width()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self._is_highlighting = False
        self.verticalScrollBar().valueChanged.connect(self.on_scroll)

    def line_number_area_width(self):
        if not self.ide.settings["line_numbers"]:
            return 0
        doc = self.document()
        line_count = doc.blockCount()
        digits = len(str(max(1, line_count)))
        font = QFont("Consolas", self.ide.settings["editor_font_size"])
        fm = QFontMetrics(font)
        base_width = 40
        extra_digits = max(0, digits - 3)
        width = base_width + (extra_digits * 8)
        max_number_width = fm.width(str(line_count) + " ")
        return max(width, max_number_width + 10)

    def update_line_number_area_width(self):
        width = self.line_number_area_width()
        self.setViewportMargins(width, 0, 0, 0)
        self.line_number_area.setFixedWidth(width)
        self.line_number_area.setVisible(self.ide.settings["line_numbers"])
        self.update_line_number_area()

    def update_line_number_area(self):
        self.line_number_area.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(cr.left(), cr.top(), self.line_number_area_width(), cr.height())

    def on_scroll(self, value):
        if not self._is_highlighting:
            self.highlighter.schedule_highlighting()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F4:
            if SHOW_HL_INFO:
                self.ide.terminal.log("HL: F4 intercepted in CustomTextEdit, triggering open_tasks_action", "INFO")
            self.ide.open_tasks_action.trigger()
            return
        if event.key() == Qt.Key_Z and event.modifiers() == Qt.ControlModifier:
            if SHOW_HL_INFO:
                self.ide.terminal.log("HL: Ctrl+Z intercepted in CustomTextEdit, forwarding to IDE.undo", "INFO")
            self.ide.undo()
            return
        if event.key() == Qt.Key_Tab:
            self.ide.indent()
            return
        elif event.key() == Qt.Key_Backtab:
            self.ide.dedent()
            return
        if SHOW_HL_INFO:
            self.ide.terminal.log(f"HL: Key pressed - key: {event.key()}, text: '{event.text()}', undoAvailable: {self.document().isUndoAvailable()}", "INFO")
        super().keyPressEvent(event)
        if event.text() or event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            if not self._is_highlighting:
                self.highlighter.schedule_highlighting()

    def on_text_changed(self):
        if not self._is_highlighting:
            self._is_highlighting = True
            try:
                if SHOW_HL_INFO:
                    self.ide.terminal.log(f"HL: Text changed - isUndoAvailable: {self.document().isUndoAvailable()}, isModified: {self.document().isModified()}, file_path: {getattr(self, 'file_path', 'None')}", "INFO")
                # Force immediate highlighting for .gcb files
                if hasattr(self, "file_path") and self.file_path.lower().endswith(".gcb"):
                    self.highlighter._apply_highlighting()
                else:
                    self.highlighter.schedule_highlighting()
            except Exception as e:
                if SHOW_HL_INFO:
                    self.ide.terminal.log(f"HL: Error in highlighting: {str(e)}", "ERROR")
            finally:
                self._is_highlighting = False

    def show_context_menu(self, position):
        menu = QMenu(self)
        menu.setFont(QFont("Arial", self.ide.settings["ui_font_size"]))
        undo_action = menu.addAction("Undo")
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self.ide.undo)
        undo_action.setEnabled(self.document().isUndoAvailable())
        redo_action = menu.addAction("Redo")
        redo_action.setShortcut("Ctrl+Y")
        redo_action.triggered.connect(self.ide.redo)
        redo_action.setEnabled(self.document().isRedoAvailable())
        menu.addSeparator()
        cut_action = menu.addAction("Cut")
        cut_action.setShortcut("Ctrl+X")
        cut_action.triggered.connect(self.cut)
        cut_action.setEnabled(self.textCursor().hasSelection())
        copy_action = menu.addAction("Copy")
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(self.copy)
        copy_action.setEnabled(self.textCursor().hasSelection())
        paste_action = menu.addAction("Paste")
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(self.paste)
        paste_action.setEnabled(QApplication.clipboard().text() != "")
        menu.addSeparator()
        select_all_action = menu.addAction("Select All")
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self.selectAll)
        if self.ide.settings["theme"] == "dark":
            bg_color = "#2E2E2E"
            fg_color = "#FFFFFF"
            hover_color = "#555555"
            border_color = "#444444"
        else:
            bg_color = "#F5F5F5"
            fg_color = "#000000"
            hover_color = "#D3D3D3"
            border_color = "#CCCCCC"
        menu.setStyleSheet(
            f"QMenu {{ background-color: {bg_color}; color: {fg_color}; border: 1px solid {border_color}; }}"
            f"QMenu::item {{ padding: 2px 16px; }}"
            f"QMenu::item:selected {{ background-color: {hover_color}; }}"
        )
        menu.exec_(self.mapToGlobal(position))

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

    def lognewline(self):
        self.addItem(f"")
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
        self.text_browser.setFont(QFont("Arial", parent.settings["ui_font_size"]))
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
        return f'<div style="font-family: Arial; font-size: {self.parent().settings["ui_font_size"]}pt;">{html_text}</div>'

    def open_url(self, url):
        qurl = QUrl(url)
        if qurl.isValid():
            QDesktopServices.openUrl(qurl)
            self.parent().terminal.log(f"Opened URL: {url.toString()}", "INFO")
        else:
            self.parent().terminal.log(f"Invalid URL clicked: {url.toString()}", "ERROR")

class FloatingButtonBar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.ide = parent
        self.settings = self.ide.settings
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, False)  # Prevent bar from closing app
        self.drag_start_position = None
        self.is_dragging = False
        self.setMouseTracking(True)
        self.installEventFilter(self)
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.timeout.connect(self.start_drag)
        self.hold_threshold = 200  # ms to differentiate click vs. hold
        # Subtle background for visibility
        self.setStyleSheet("background-color: rgba(200, 200, 200, 50);")
        layout = QHBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        button_size = self.settings["button_bar"].get("size", 24)
        self.setFixedHeight(button_size + 12)
        tasks_file = self.ide.get_tasks_file_path()
        self.tasks = self.ide.parse_tasks_json(tasks_file) if tasks_file else []
        valid_keys = {f"f{i}" for i in range(1, 13)} | {f"shift+f{i}" for i in range(1, 13)}
        task_shortcuts = {task.get("shortcut", "").lower(): task for task in self.tasks if task.get("shortcut")}
        if SHOW_BAR_CONTROL:
            self.ide.terminal.log(f"BC: Available task shortcuts: {list(task_shortcuts.keys())}", "INFO")

        # Determine icon directory based on button size
        config_dir = os.path.expanduser("~/.superide")
        user_icon_base_dir = os.path.join(config_dir, "gcb-icons")
        icon_size_dir = f"{button_size}_{button_size}_icons"
        user_icon_dir = os.path.join(user_icon_base_dir, icon_size_dir)
        
        # Check if user icon directory exists; if not, copy from application folder
        if not os.path.exists(user_icon_base_dir):
            app_icon_base_dir = resource_path("gcb-icons")
            if os.path.exists(app_icon_base_dir):
                try:
                    shutil.copytree(app_icon_base_dir, user_icon_base_dir)
                    if SHOW_BAR_CONTROL:
                        self.ide.terminal.log(f"BC: Copied icons from {app_icon_base_dir} to {user_icon_base_dir}", "INFO")
                except Exception as e:
                    if SHOW_BAR_CONTROL:
                        self.ide.terminal.log(f"BC: Error copying icons to {user_icon_base_dir}: {str(e)}", "ERROR")
            else:
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Application icon directory not found at {app_icon_base_dir}", "ERROR")

        for i in range(1, 5):
            config = self.settings["button_bar"].get(f"button{i}", "").strip()
            if SHOW_BAR_CONTROL:
                self.ide.terminal.log(f"BC: Processing button{i} config: {config}", "INFO")
            if not config:
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Button{i} is empty, skipping", "INFO")
                continue
            match = re.match(r'^\[(.*?)\]:(.*)$', config, re.IGNORECASE)
            if not match:
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Invalid button{i} config format: {config}", "ERROR")
                continue
            func_key, icon_filename = match.groups()
            func_key = func_key.replace(" ", "").lower()
            if func_key.startswith("shift") and "f" in func_key:
                func_key = re.sub(r'shift\+?f', 'shift+f', func_key, flags=re.IGNORECASE)
            if SHOW_BAR_CONTROL:
                self.ide.terminal.log(f"BC: Button{i} normalized func_key: {func_key}", "INFO")
            if func_key not in valid_keys:
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Invalid function key for button{i}: {func_key}", "ERROR")
                continue
            task = task_shortcuts.get(func_key)
            if not task:
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: No task found for function key {func_key} in button{i}", "ERROR")
                continue
            button = QPushButton()
            button.setFixedSize(button_size, button_size)
            # Construct icon path using user directory and size-based folder
            icon_path = os.path.join(user_icon_dir, icon_filename.strip())
            if os.path.exists(icon_path):
                icon = QIcon(icon_path)
                button.setIcon(icon)
                button.setIconSize(QSize(button_size - 4, button_size - 4))
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Loaded icon for button{i}: {icon_path}", "INFO")
            else:
                button.setText(str(i))
                button.setFont(QFont("Arial", button_size // 2))
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Icon not found for button{i}: {icon_path}, using fallback text '{i}'", "ERROR")
            button.clicked.connect(lambda checked, t=task: self.run_task_with_focus(t))
            button.setFocusPolicy(Qt.NoFocus)
            button.installEventFilter(self)  # Install event filter on button
            button.setStyleSheet(
                f"QPushButton {{ background-color: {'#F5F5F5' if self.settings['theme'] == 'light' else '#2E2E2E'}; "
                f"border: 1px solid {'#CCCCCC' if self.settings['theme'] == 'light' else '#444444'}; }}"
                f"QPushButton:hover {{ background-color: {'#D3D3D3' if self.settings['theme'] == 'light' else '#555555'}; }}"
            )
            layout.addWidget(button)
            if SHOW_BAR_CONTROL:
                self.ide.terminal.log(f"BC: Added button{i} for task '{task.get('label', 'Unnamed Task')}'", "INFO")
        self.setLayout(layout)
        self.setFixedWidth((button_size + 2) * layout.count() + 12)
        if layout.count() == 0:
            self.hide()
            if SHOW_BAR_CONTROL:
                self.ide.terminal.log("BC: No valid buttons configured, hiding button bar", "ERROR")
        else:
            self.show()
            if SHOW_BAR_CONTROL:
                self.ide.terminal.log(f"BC: Button bar initialized with {layout.count()} buttons", "INFO")

    def run_task_with_focus(self, task):
        """Run the task and shift focus to the IDE."""
        self.ide.run_task(task=task)
        self.ide.setFocus()
        self.ide.activateWindow()
        self.ide.raise_()
        if SHOW_BAR_CONTROL:
            self.ide.terminal.log("BC: Task executed, focus shifted to IDE", "INFO")

    def start_drag(self):
        if self.drag_start_position is not None:
            self.is_dragging = True
            self.grabMouse()
            self.setCursor(Qt.OpenHandCursor)
            if SHOW_BAR_CONTROL:
                self.ide.terminal.log("BC: Hold timer expired, starting drag", "INFO")

    def eventFilter(self, obj, event):
        if obj == self or isinstance(obj, QPushButton):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Mouse press detected at {event.globalPos()} on {'bar' if obj == self else 'button'}", "INFO")
                self.drag_start_position = event.globalPos() - self.pos()
                self.is_dragging = False
                self.hold_timer.start(self.hold_threshold)
                return False  # Allow button to process click
            elif event.type() == QEvent.MouseMove:
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Mouse move detected at {event.globalPos()}, is_dragging: {self.is_dragging}", "INFO")
                if self.is_dragging and self.drag_start_position is not None:
                    new_pos = event.globalPos() - self.drag_start_position
                    if SHOW_BAR_CONTROL:
                        self.ide.terminal.log(f"BC: Raw new_pos: {new_pos}", "INFO")
                    parent_rect = self.ide.geometry()
                    screen = QApplication.primaryScreen().availableGeometry()
                    button_size = self.settings["button_bar"].get("size", 24)
                    x = max(parent_rect.left(), min(new_pos.x(), parent_rect.right() - self.width()))
                    y = max(screen.top(), min(new_pos.y(), screen.bottom() - button_size))
                    if SHOW_BAR_CONTROL:
                        self.ide.terminal.log(f"BC: Constrained pos: [{x}, {y}], parent_rect: {parent_rect}, screen: {screen}", "INFO")
                    self.move(x, y)
                    self.settings["button_bar"]["position"] = [self.x(), self.y()]
                    if SHOW_BAR_CONTROL:
                        self.ide.terminal.log(f"BC: Button bar moved to [{self.x()}, {self.y()}]", "INFO")
                    return True
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.hold_timer.stop()
                was_dragging = self.is_dragging
                self.drag_start_position = None
                self.is_dragging = False
                self.releaseMouse()
                self.unsetCursor()
                if was_dragging:
                    self.settings["button_bar"]["position"] = [self.x(), self.y()]
                    self.ide.save_settings()
                    self.ide.setFocus()
                    self.ide.activateWindow()
                    self.ide.raise_()
                    if SHOW_BAR_CONTROL:
                        self.ide.terminal.log(f"BC: Settings saved with position [{self.x()}, {self.y()}]", "INFO")
                        self.ide.terminal.log("BC: Drag completed, focus shifted to IDE", "INFO")
                if SHOW_BAR_CONTROL:
                    self.ide.terminal.log(f"BC: Mouse released, was_dragging: {was_dragging}", "INFO")
                return False  # Allow button to process release for click
        return super().eventFilter(obj, event)

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
        if self.parent().tabs.count() == 0:
            if self.background_image and not self.background_image.isNull():
                scaled_image = self.background_image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                image_rect = scaled_image.rect()
                image_rect.moveCenter(self.rect().center())
                painter.drawImage(image_rect, scaled_image)
            else:
                painter.fillRect(self.rect(), Qt.white)
                font = QFont("Arial", 48, QFont.Bold)
                painter.setFont(font)
                text = "GCBASIC Essential IDE"
                text_rect = painter.fontMetrics().boundingRect(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, text)
                text_rect.moveCenter(self.rect().center())
                painter.setPen(Qt.black if self.parent().settings["theme"] == "light" else Qt.white)
                painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, text)
        else:
            bg_color = Qt.white if self.parent().settings["theme"] == "light" else Qt.darkGray
            painter.fillRect(self.rect(), bg_color)
        painter.end()

class IDE(QMainWindow):
    def __init__(self, filename=None):
        super().__init__()
        self.setWindowTitle(f"GCBASIC Essential IDE : Build {BUILD_NUMBER}")
        config_dir = os.path.expanduser("~/.superide")
        gcbasic_path = os.path.normpath(os.environ.get("GCBASIC_INSTALL_PATH", os.path.expanduser("~")))
        self.recent_files_path = os.path.join(gcbasic_path, "GCstudio.mrf.json")
        self.settings = {
            "theme": "light",
            "line_numbers": True,
            "word_wrap": False,
            "show_info": True,
            "show_errors": True,
            "save_confirmation": True,
            "window_size": [800, 600],
            "window_position": [0, 0],
            "ui_font_size": 12,
            "editor_font_size": 12,
            "indent_size": 4,
            "goto_marker_duration": 3,
            "showTerminal": True,
            "terminal_size_percentage": 30,
            "language_file": os.path.join(config_dir, "GCB.tmLanguage.json"),
            "check_external_modifications": True,
            "tasks_file": os.path.join(config_dir, "tasks.json"),
            "last_folder": os.path.expanduser("~"),
            "recent_files_path": self.recent_files_path,
            "button_bar": {
                "button1": "[F5]:hexflash.png",
                "button2": "[F6]:hex.png",
                "button3": "[F7]:asm.png",
                "button4": "[F1]:help.png",
                "size": 24,
                "position": []
            }
        }
        self.first_time_settings = False  # Flag to indicate first-time settings creation
        self.recent_files = []
        self.file_cache = {}
        self.history = {}
        self.file_states = {}
        self.file_menu = None
        self.last_search = None
        self.info_action = None
        self.error_action = None
        self.show_terminal_action = None
        self.line_numbers_action = None
        self.task_output_cache = []
        self.terminal = TerminalWindow()
        self.dock = QDockWidget("Terminal", self)
        self.dock.setObjectName("TerminalDock")
        self.dock.setWidget(self.terminal)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
        if SHOW_TERMINAL_INFO:
            self.terminal.log(f"Initialized Terminal dock with objectName: {self.dock.objectName()}", "INFO")
        self.background_widget = BackgroundWidget(self)
        self.setCentralWidget(self.background_widget)
        self.central_layout = QVBoxLayout(self.background_widget)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.update_background_after_close)
        self.tabs.setStyleSheet("QTabWidget::pane { background: transparent; border: 0; } "
                               "QTabBar::tab { background: transparent; } "
                               "QTabWidget > QWidget > QWidget { background: transparent; } "
                               "QTextEdit { background: transparent; }")
        self.central_layout.addWidget(self.tabs, 1)
        self.tabs.tabBar().tabBarClicked.connect(self.update_background)
        icon_path = resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            self.terminal.log(f"Application icon not found at {icon_path}", "ERROR")
        self.check_file_timer = QTimer(self)
        self.check_file_timer.timeout.connect(self.check_all_files)
        if self.settings["check_external_modifications"]:
            self.check_file_timer.start(5000)
        else:
            self.check_file_timer.stop()
        self.button_bar = None
        self._tasks_loaded = False  # Flag to track tasks loading
        self.init_ui()
        self.load_settings()
        self.load_and_populate_tasks()  # Call after load_settings
        self.apply_theme()
        self.apply_terminal_settings()
        self.apply_logging_settings()
        self.init_button_bar()
        if filename and os.path.exists(filename):
            self.open_file_by_path(filename)
        # Open demo files if first-time settings were created and no file was specified
        if self.first_time_settings and not filename:
            self.open_demo_files()
        # self.terminal.log(f"Logging Status - INFO: {self.settings['show_info']}, ERROR: {self.settings['show_errors']}", "INFO")
        self.background_widget.update()

    def open_demo_files(self):
        """Open the specified demo files from the GCBASIC demos directory."""
        gcbasic_path = os.path.normpath(os.environ.get("GCBASIC_INSTALL_PATH", os.path.expanduser("~")))
        demo_dir = os.path.join(gcbasic_path, "gcbasic", "demos")
        demo_files = [
            "first-start-sample.gcb",
            "This is useful list of tools for the IDE.txt"
        ]
        for demo_file in demo_files:
            file_path = os.path.join(demo_dir, demo_file)
            if os.path.exists(file_path):
                self.open_file_by_path(file_path)
                if SHOW_FILE_INFO:
                    self.terminal.log(f"Opened demo file: {file_path}", "INFO")
            else:
                self.terminal.log(f"Demo file not found: {file_path}", "ERROR")

    def check_all_files(self):
        for i in range(self.tabs.count()):
            self.check_file_changes(self.tabs.widget(i))

    def on_tab_changed(self, index):
        if index >= 0:
            self.check_file_changes(self.tabs.widget(index))

    def update_background(self, index=None):
        self.background_widget.update()

    def update_background_after_close(self, index):
        self.close_tab(index)

    def normalize_path(self, path):
        return os.path.normpath(os.path.abspath(path)).lower()

    def init_ui(self):
        menubar = self.menuBar()
        self.file_menu = menubar.addMenu("&File")
        edit_menu = menubar.addMenu("&Edit")
        self.ide_tasks_menu = menubar.addMenu("IDE &Tasks")
        settings_menu = menubar.addMenu("&IDE Settings")
        help_menu = menubar.addMenu("&Help")
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
        save_as_action = QAction("Save &As", self)
        save_as_action.setShortcut("Ctrl+Shift+A")
        save_as_action.triggered.connect(self.save_file_as)
        self.file_menu.addAction(save_as_action)
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
        self.open_tasks_action = QAction("Open Tasks Menu", self)
        self.open_tasks_action.setShortcut("F4")
        self.open_tasks_action.triggered.connect(self.open_tasks_menu)
        self.addAction(self.open_tasks_action)
        self.populate_tasks_menu()
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
        upper_case_action = QAction("&UpperCase", self)
        upper_case_action.setShortcut("Ctrl+U")
        upper_case_action.triggered.connect(self.upper_case)
        edit_menu.addAction(upper_case_action)
        lower_case_action = QAction("&LowerCase", self)
        lower_case_action.setShortcut("Ctrl+L")
        lower_case_action.triggered.connect(self.lower_case)
        edit_menu.addAction(lower_case_action)
        goto_action = QAction("&Go to Line", self)
        goto_action.setShortcut("Ctrl+G")
        goto_action.triggered.connect(self.goto_line)
        edit_menu.addAction(goto_action)
        comment_action = QAction("Toggle Co&mment", self)
        comment_action.setShortcut("Ctrl+/")
        comment_action.triggered.connect(self.toggle_comment)
        edit_menu.addAction(comment_action)

        # Add repaint highlighting shortcut without menu item
        repaint_highlight_action = QAction("Repaint Highlighting", self)
        repaint_highlight_action.setShortcut("Ctrl+Shift+R")
        repaint_highlight_action.triggered.connect(self.repaint_highlighting)
        self.addAction(repaint_highlight_action)

        appearance_menu = settings_menu.addMenu("&Appearance")
        editor_menu = settings_menu.addMenu("&Editor")
        logging_menu = settings_menu.addMenu("&Logging")
        recent_files_menu = settings_menu.addMenu("&Recent Files")
        button_bar_menu = settings_menu.addMenu("&Button Bar")
        ui_font_action = QAction("&UI Font Size", self)
        ui_font_action.triggered.connect(self.set_ui_font_size)
        appearance_menu.addAction(ui_font_action)
    
        # New font chooser action
        editor_font_action = QAction("&Editor Font", self)
        editor_font_action.triggered.connect(self.set_editor_font)
        appearance_menu.addAction(editor_font_action)

        editor_font_action = QAction("&Editor Font Size", self)
        editor_font_action.triggered.connect(self.set_editor_font_size)
        appearance_menu.addAction(editor_font_action)
        screen_size_action = QAction("&Screen Size and Position", self)
        screen_size_action.triggered.connect(self.set_screen_size_and_position)
        appearance_menu.addAction(screen_size_action)
        indent_size_action = QAction("&Indent Size", self)
        indent_size_action.triggered.connect(self.set_indent_size)
        editor_menu.addAction(indent_size_action)
        self.line_numbers_action = QAction("Show &Line Numbers", self)
        self.line_numbers_action.setCheckable(True)
        self.line_numbers_action.setChecked(self.settings["line_numbers"])
        self.line_numbers_action.triggered.connect(self.toggle_line_numbers)
        editor_menu.addAction(self.line_numbers_action)
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
        check_external_action = QAction("Check &External Modifications", self)
        check_external_action.setCheckable(True)
        check_external_action.setChecked(self.settings["check_external_modifications"])
        check_external_action.triggered.connect(self.toggle_external_checks)
        editor_menu.addAction(check_external_action)
        marker_duration_action = QAction("Goto Marker Duration", self)
        marker_duration_action.triggered.connect(self.set_goto_marker_duration)
        editor_menu.addAction(marker_duration_action)
        self.info_action = QAction("Toggle Info Logs", self)
        self.info_action.setCheckable(True)
        self.info_action.setChecked(self.settings["show_info"])
        self.info_action.triggered.connect(self.toggle_info_logs)
        logging_menu.addAction(self.info_action)
        self.error_action = QAction("Toggle Error Logs", self)
        self.error_action.setCheckable(True)
        self.error_action.setChecked(self.settings["show_errors"])
        self.error_action.triggered.connect(self.toggle_error_logs)
        logging_menu.addAction(self.error_action)
        self.show_terminal_action = QAction("Toggle Terminal", self)
        self.show_terminal_action.setCheckable(True)
        self.show_terminal_action.setChecked(self.settings["showTerminal"])
        self.show_terminal_action.triggered.connect(self.toggle_terminal)
        logging_menu.addAction(self.show_terminal_action)
        terminal_size_action = QAction("Terminal Size", self)
        terminal_size_action.triggered.connect(self.set_terminal_size)
        logging_menu.addAction(terminal_size_action)
        reset_terminal_action = QAction("Reset Terminal Position", self)
        reset_terminal_action.triggered.connect(self.reset_terminal_position)
        logging_menu.addAction(reset_terminal_action)
        clear_recent_action = QAction("&Clear Recent Files", self)
        clear_recent_action.triggered.connect(self.clear_recent_files)
        recent_files_menu.addAction(clear_recent_action)
        reset_button_bar_action = QAction("Reset Position", self)
        reset_button_bar_action.triggered.connect(self.reset_button_bar_position)
        button_bar_menu.addAction(reset_button_bar_action)
        icon_size_action = QAction("Icon Size", self)
        icon_size_action.triggered.connect(self.set_button_bar_icon_size)
        button_bar_menu.addAction(icon_size_action)
        about_action = QAction("&About", self)
        about_action.triggered.connect(lambda: QMessageBox.information(self, "About", f"GCBASIC Essential IDE build {BUILD_NUMBER}"))
        help_menu.addAction(about_action)
        license_action = QAction("&License", self)
        license_action.triggered.connect(self.show_license)
        help_menu.addAction(license_action)
        
        help_menu.addSeparator()
        report_issue_action = QAction("&Report Issue", self)
        report_issue_action.triggered.connect(lambda: self.open_url("https://github.com/GreatCowBASIC/GCBIDE/issues"))
        help_menu.addAction(report_issue_action)
        latest_release_action = QAction("&Latest Release", self)
        latest_release_action.triggered.connect(lambda: self.open_url("https://github.com/GreatCowBASIC/GCBIDE/releases"))
        help_menu.addAction(latest_release_action)
                
        help_menu.addSeparator()
        manage_language_action = QAction("&Manage Language File", self)
        manage_language_action.triggered.connect(self.open_language_file)
        help_menu.addAction(manage_language_action)
        manage_tasks_action = QAction("&Manage Tasks File", self)
        manage_tasks_action.triggered.connect(self.open_tasks_file)
        help_menu.addAction(manage_tasks_action)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self.show_tab_context_menu)
        self.tabs.installEventFilter(self)
        self.tabs.currentChanged.connect(self.on_tab_changed)

    # New Function
    def set_editor_font(self):
        class FontDialog(QDialog):
            def __init__(self, parent=None, current_font="Consolas"):
                super().__init__(parent)
                self.setWindowTitle("Select Editor Font")
                layout = QVBoxLayout()
                self.font_combo = QComboBox()
                font_db = QFontDatabase()
                # Filter for monospaced fonts
                monospaced_fonts = []
                for family in font_db.families():
                    if font_db.isScalable(family) and font_db.isFixedPitch(family):
                        monospaced_fonts.append(family)
                monospaced_fonts.sort()
                self.font_combo.addItems(monospaced_fonts)
                # Set current font
                current_index = self.font_combo.findText(current_font)
                if current_index != -1:
                    self.font_combo.setCurrentIndex(current_index)
                layout.addWidget(self.font_combo)
                button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                button_box.accepted.connect(self.accept)
                button_box.rejected.connect(self.reject)
                layout.addWidget(button_box)
                self.setLayout(layout)

            def selected_font(self):
                return self.font_combo.currentText()

        dialog = FontDialog(self, self.settings.get("editor_font", "Consolas"))
        if dialog.exec_() == QDialog.Accepted:
            selected_font = dialog.selected_font()
            font_db = QFontDatabase()
            # Validate selected font is monospaced and available
            if font_db.isFixedPitch(selected_font) and selected_font in font_db.families():
                if selected_font != self.settings.get("editor_font", "Consolas"):
                    self.settings["editor_font"] = selected_font
                    self.apply_text_settings()
                    self.save_settings()
                    # self.terminal.log(f"Set editor font to {selected_font}", "INFO")
            else:
                self.terminal.log(f"Selected font {selected_font} is not a valid monospaced font, ignoring", "ERROR")
                            
    def repaint_highlighting(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.highlighter._apply_highlighting()
            self.terminal.log("Syntax highlighting repainted", "INFO")
        else:
            self.terminal.log("No file open to repaint highlighting", "ERROR")

    def open_url(self, url):
        qurl = QUrl(url)
        if qurl.isValid():
            QDesktopServices.openUrl(qurl)
            self.terminal.log(f"Opened URL: {url}", "INFO")
        else:
            self.terminal.log(f"Invalid URL: {url}", "ERROR")

    def init_button_bar(self):
        """Initialize the floating button bar if any button is configured."""
        button_bar_settings = self.settings.get("button_bar", {})
        has_valid_button = any(
            button_bar_settings.get(f"button{i}", "").strip() != ""
            for i in range(1, 5)
        )
        if SHOW_BAR_CONTROL:
            self.terminal.log(f"Button bar settings: {button_bar_settings}", "INFO")
        if not has_valid_button:
            if self.button_bar:
                self.button_bar.hide()
                self.button_bar.deleteLater()
                self.button_bar = None
            if SHOW_BAR_CONTROL:
                self.terminal.log("No non-empty button configurations found, button bar not shown", "INFO")
            return
        if self.button_bar:
            self.button_bar.deleteLater()
        self.button_bar = FloatingButtonBar(self)
        if self.button_bar.isVisible():
            button_size = button_bar_settings.get("size", 24)
            window_width = self.width()
            menu_bar_height = self.menuBar().height()
            bar_width = (button_size + 2) * self.button_bar.layout().count() + 4
            default_x = window_width // 2 - bar_width // 2  # 50% of window width
            default_y = menu_bar_height  # Align with bottom of menu bar
            position = button_bar_settings.get("position", [])
            if not position or len(position) != 2:
                pos_x, pos_y = default_x, default_y
                if SHOW_BAR_CONTROL:
                    self.terminal.log(f"Using default position: [{pos_x}, {pos_y}]", "INFO")
            else:
                pos_x, pos_y = position
                if SHOW_BAR_CONTROL:
                    self.terminal.log(f"Using saved position: [{pos_x}, {pos_y}]", "INFO")
            parent_rect = self.geometry()
            screen = QApplication.primaryScreen().availableGeometry()
            pos_x = max(parent_rect.left(), min(pos_x, parent_rect.right() - bar_width))
            pos_y = max(screen.top(), min(pos_y, screen.bottom() - button_size))
            self.button_bar.move(pos_x, pos_y)
            self.button_bar.settings["button_bar"]["position"] = [pos_x, pos_y]
            if SHOW_BAR_CONTROL:
                self.terminal.log(f"Button bar positioned at: [{pos_x}, {pos_y}]", "INFO")

    def reset_button_bar_position(self):
        """Reset the button bar to the middle of the window near the top."""
        if not self.button_bar:
            self.init_button_bar()
            if not self.button_bar:
                self.terminal.log("No button bar to reset due to no valid configurations", "INFO")
                return
        button_size = self.settings["button_bar"].get("size", 24)
        window_width = self.width()
        menu_bar_height = self.menuBar().height()
        bar_width = (button_size + 2) * self.button_bar.layout().count() + 4
        pos_x = window_width // 2 - bar_width // 2  # 50% of window width
        pos_y = menu_bar_height  # Align with bottom of menu bar
        self.button_bar.move(pos_x, pos_y)
        self.settings["button_bar"]["position"] = [pos_x, pos_y]
        self.save_settings()
        self.terminal.log(f"Button bar reset to position: [{pos_x}, {pos_y}]", "INFO")

    def set_button_bar_icon_size(self):
        sizes = ["24", "32", "64"]
        current_size = str(self.settings["button_bar"].get("size", 24))
        size, ok = QInputDialog.getItem(self, "Select Icon Size", "Icon Size:", sizes, sizes.index(current_size) if current_size in sizes else 0, False)
        if ok:
            self.settings["button_bar"]["size"] = int(size)
            self.save_settings()
            self.init_button_bar()
            if SHOW_BAR_CONTROL:
                self.terminal.log(f"Button bar icon size set to {size}", "INFO")

    def populate_tasks_menu(self):
        """Populate the IDE Tasks menu with tasks from tasks.json, deferring file loading."""
        self.ide_tasks_menu.clear()        
        # Defer tasks loading until load_and_populate_tasks() is called after load_settings()
        if not hasattr(self, '_tasks_loaded') or not self._tasks_loaded:
            if SHOW_FILE_INFO:
                self.terminal.log("Tasks not loaded yet, will be populated after settings load", "INFO")
            action = self.ide_tasks_menu.addAction("Tasks loading...")
            action.setEnabled(False)
            return
    
        tasks_file = self.settings.get("tasks_file")        
        if not tasks_file:
            self.terminal.log("No tasks file path available", "ERROR")
            action = self.ide_tasks_menu.addAction("No tasks available")
            action.setEnabled(False)
            return
        
        try:
            tasks = self.parse_tasks_json(tasks_file)
            if not tasks:
                self.terminal.log("No tasks found in file", "INFO")
                action = self.ide_tasks_menu.addAction("No tasks available")
                action.setEnabled(False)
            else:
                for task in tasks:
                    label = task.get("label", "Unnamed Task")
                    if isinstance(label, list):
                        label = label[0] if label else "Unnamed Task"
                    if isinstance(label, str):
                        label = re.sub(r'\[.*?\]', '', label).strip()
                        if not label:
                            label = "Unnamed Task"
                    action = QAction(label, self)
                    action.triggered.connect(lambda checked, t=task: self.run_task(task=t))
                    shortcut = task.get("shortcut")
                    if shortcut:
                        action.setShortcut(shortcut)
                        if SHOW_HL_INFO:
                            self.terminal.log(f"Assigned shortcut '{shortcut}' to task '{label}'", "INFO")
                    self.ide_tasks_menu.addAction(action)
        except Exception as e:
            self.terminal.log(f"Error populating tasks menu: {str(e)}", "ERROR")
            action = self.ide_tasks_menu.addAction("Error loading tasks")
            action.setEnabled(False)

    def load_and_populate_tasks(self):
        """Load tasks file and populate tasks menu after settings are loaded."""
        self.terminal.log("Loading and populating tasks", "INFO")
        try:
            tasks_file = self.get_tasks_file_path()
            if tasks_file:
                self.settings["tasks_file"] = tasks_file  # Ensure tasks_file is stored
            self._tasks_loaded = True
            self.populate_tasks_menu()  # Repopulate after loading tasks
        except Exception as e:
            self.terminal.log(f"Error loading tasks file path: {str(e)}", "ERROR")
            self._tasks_loaded = False
            self.populate_tasks_menu()  # Populate with error state

    def open_tasks_menu(self):
        if not self.ide_tasks_menu:
            self.terminal.log("IDE Tasks menu not initialized", "ERROR")
            return
        try:
            self.populate_tasks_menu()
            action = self.ide_tasks_menu.menuAction()
            pos = self.menuBar().actionGeometry(action).bottomLeft()
            global_pos = self.menuBar().mapToGlobal(pos)
            self.ide_tasks_menu.popup(global_pos)
        except Exception as e:
            self.terminal.log(f"Error opening IDE Tasks menu: {str(e)}", "ERROR")

    def parse_tasks_json(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            cleaned_lines = []
            for line in lines:
                line = line.strip()
                if not (line.startswith('//') or line.startswith('#') or line == ''):
                    cleaned_lines.append(line)
            cleaned_json = '\n'.join(cleaned_lines)
            tasks_data = json.loads(cleaned_json)
            tasks = tasks_data.get('tasks', [tasks_data]) if 'tasks' in tasks_data else [tasks_data]
            for task in tasks:
                label = task.get("label", "Unnamed Task")
                match = re.search(r'\[?\s*(Shift\s*\+?\s*)?(F|f)([1-9]|1[0-2])\s*\]?', label, re.IGNORECASE)
                if match:
                    shift = match.group(1) is not None
                    key_num = match.group(3)
                    shortcut = f"shift+f{key_num}" if shift else f"f{key_num}"
                    task["shortcut"] = shortcut.lower()
                    if SHOW_BAR_CONTROL:
                        self.terminal.log(f"Parsed shortcut '{shortcut.lower()}' for task '{label}'", "INFO")
                else:
                    task["shortcut"] = None
                    if SHOW_BAR_CONTROL:
                        self.terminal.log(f"No valid shortcut found in task label '{label}'", "INFO")
            if SHOW_BAR_CONTROL:            
                self.terminal.log(f"Loaded {len(tasks)} tasks from {file_path}: {[task.get('label', 'Unnamed Task') for task in tasks]}", "INFO")
            return tasks
        except json.JSONDecodeError as e:
            self.terminal.log(f"Error parsing tasks JSON {file_path}: {str(e)}", "ERROR")
            return []
        except Exception as e:
            self.terminal.log(f"Error reading tasks file {file_path}: {str(e)}", "ERROR")
        return []

    def get_tasks_file_path(self):
        tasks_file = self.settings.get("tasks_file")
        config_dir = os.path.expanduser("~/.superide")
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        
        if not os.path.exists(tasks_file):
            local_tasks = resource_path("tasks.json")
            if os.path.exists(local_tasks):
                try:
                    import shutil
                    shutil.copy(local_tasks, tasks_file)
                    self.settings["tasks_file"] = tasks_file
                    self.save_settings()
                    self.terminal.log(f"Copied tasks file from {local_tasks} to {tasks_file}", "INFO")
                except Exception as e:
                    self.terminal.log(f"Error copying tasks file to {tasks_file}: {str(e)}", "ERROR")
                    return None
            else:
                self.terminal.log(f"Tasks file not found at {tasks_file} or {local_tasks}", "ERROR")
                return None
        
        if os.path.exists(tasks_file):
            if SHOW_BAR_CONTROL:
                self.terminal.log(f"Using tasks file from {tasks_file}", "INFO")
            return tasks_file
        else:
            self.terminal.log(f"Tasks file not found at {tasks_file} after copy attempt", "ERROR")
            return None

    def run_task(self, task=None):
        try:
            command = task.get("command", "")
            args = task.get("args", [])
            options = task.get("options", {})
            self.task_output_cache.clear()
            dirname = os.getcwd()
            local_file_path = ""
            if command.lower().__contains__("gcbasic.exe") and not "debug" in task.get("label", "").lower():
                current_tab = self.tabs.currentWidget()
                if not current_tab or not hasattr(current_tab, "file_path"):
                    self.terminal.log("No file open to run GCBASIC task", "ERROR")
                    return
                local_file_path = current_tab.file_path
                dirname = os.path.dirname(local_file_path)
                if current_tab.document().isModified():
                    self.save_file()
                    self.terminal.log(f"Saved file {local_file_path} before executing GCBASIC task", "INFO")
            if command.lower() == "explorer":
                env_vars = os.environ
                gcbasic_path = os.path.normpath(env_vars.get("GCBASIC_INSTALL_PATH", ""))
                processed_args = []
                for arg in args:
                    arg = arg.replace("${env:GCBASIC_INSTALL_PATH}", gcbasic_path)
                    arg = arg.replace("${file}", local_file_path)
                    arg = arg.replace("${fileDirname}", dirname)
                    processed_args.append(arg)
                target = processed_args[0] if processed_args else dirname
                try:
                    webbrowser.open(target)
                    self.terminal.log(f"Launched default browser with: {target}", "INFO")
                except Exception as e:
                    self.terminal.log(f"Error launching browser with {target}: {str(e)}", "ERROR")
                return
            full_command_str = f"{command} {' '.join(args)}".lower()
            current_tab = self.tabs.currentWidget()
            if current_tab:
                local_file_path = current_tab.file_path
                dirname = os.path.dirname(local_file_path)
            
            if "remove-item" in full_command_str and "-include" in full_command_str:
                current_tab = self.tabs.currentWidget()
                if not current_tab or not hasattr(current_tab, "file_path") or not current_tab.file_path:
                    self.terminal.log("No file open to derive directory for Remove-Item", "ERROR")
                    return
                local_file_path = current_tab.file_path
                dirname = os.path.dirname(local_file_path)
                if not os.path.exists(dirname):
                    self.terminal.log(f"Directory not found: {dirname}", "ERROR")
                    return
                include_match = re.search(r'-include\s+([^\s]+)', full_command_str, re.IGNORECASE)
                if not include_match:
                    self.terminal.log("Invalid -Include parameter in Remove-Item command", "ERROR")
                    return
                raw_extensions = include_match.group(1).split(',')
                extensions = [ext.strip().lstrip('*.') for ext in raw_extensions if ext.strip()]
                if not extensions:
                    self.terminal.log("No valid extensions specified in -Include", "ERROR")
                    return
                deleted = False
                for ext in extensions:
                    pattern = os.path.join(dirname, f"*.{ext}")
                    for filepath in glob.glob(pattern, recursive=False):
                        try:
                            os.remove(filepath)
                            self.terminal.log(f"Deleted file: {filepath}", "INFO")
                            deleted = True
                        except PermissionError:
                            self.terminal.log(f"Permission denied deleting {filepath}", "ERROR")
                        except FileNotFoundError:
                            self.terminal.log(f"File not found: {filepath}", "ERROR")
                        except OSError as e:
                            self.terminal.log(f"Error deleting {filepath}: {str(e)}", "ERROR")
                if not deleted:
                    self.terminal.log(f"No files found to delete in {dirname} with extensions: {', '.join(raw_extensions)}", "INFO")
                return
            env_vars = os.environ
            gcbasic_path = os.path.normpath(env_vars.get("GCBASIC_INSTALL_PATH", ""))
            command = command.replace("${env:GCBASIC_INSTALL_PATH}", gcbasic_path)
            command = command.replace("${file}", local_file_path)
            command = command.replace("${fileDirname}", dirname)
            
            if "${execPath}" in command:
                self.terminal.clear()
                self.terminal.log(f"Executing request: Opened file in new tab: {local_file_path}", "INFO")
                if local_file_path and os.path.exists(local_file_path):
                    self.open_file_by_path(local_file_path)
                    if SHOW_FILE_INFO:
                        self.terminal.log(f"Opened file in new tab: {local_file_path}", "INFO")
                else:
                    self.terminal.log(f"File not found for new tab: {local_file_path}", "ERROR")
                    return
                current_tab = self.tabs.currentWidget()
                if current_tab and hasattr(current_tab, "file_path"):
                    asm_file = os.path.splitext(current_tab.file_path)[0] + ".asm"
                    if os.path.exists(asm_file):
                        self.open_file_by_path(asm_file)
                        if SHOW_FILE_INFO:
                            self.terminal.log(f"Opened ASM file in new tab: {asm_file}", "INFO")
                    else:
                        self.terminal.log(f"ASM file not found: {asm_file}", "ERROR")
                else:
                    self.terminal.log("No current tab to derive ASM file", "ERROR")
                return
            if "\\" in command or "/" in command:
                command = f'"{command}"'
            cwd = options.get("cwd", dirname)
            cwd = cwd.replace("${env:GCBASIC_INSTALL_PATH}", gcbasic_path)
            cwd = os.path.normpath(cwd)
            if not os.path.exists(command.strip('"')):
                self.terminal.log(f"Executable not found: {command}", "ERROR")
                return
            if local_file_path and not os.path.exists(local_file_path):
                self.terminal.log(f"Input file not found: {local_file_path}", "ERROR")
                return
            if not os.path.exists(cwd):
                self.terminal.log(f"Working directory not found: {cwd}", "ERROR")
                return
            processed_args = []
            quoted_placeholder_args = {i for i, arg in enumerate(args) if re.match(r"'\${[^}]+}'", arg)}
            for i, arg in enumerate(args):
                arg = arg.replace("${env:GCBASIC_INSTALL_PATH}", gcbasic_path)
                arg = arg.replace("${file}", local_file_path)
                arg = arg.replace("${fileDirname}", dirname)
                if i in quoted_placeholder_args:
                    arg = arg.strip("'")
                    arg = f'"{arg}"'
                processed_args.append(arg)
            full_command = [command] + processed_args
            self.terminal.clear()
            if command.lower().__contains__("gcbasic.exe") and not "debug" in task.get("label", "").lower():
                self.terminal.log(f"Executing process: {' '.join(full_command)}", "INFO")
                self.terminal.scrollToBottom()
                self.terminal.user_scrolled = False
                QApplication.processEvents()
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.txt') as temp_file:
                        process = subprocess.run(
                            ' '.join(full_command),
                            cwd=cwd,
                            stdout=temp_file,
                            stderr=temp_file,
                            shell=True,
                            timeout=30
                        )
                except subprocess.TimeoutExpired:
                    self.terminal.log("Process took too long and was terminated. Use GCBASIC - Debug Mode", "ERROR")
                    self.terminal.scrollToBottom()
                    self.terminal.user_scrolled = False
                    return
                output_file = os.path.expandvars(r'%temp%\gcbasic.log')
                for _ in range(5):
                    if os.path.exists(output_file):
                        try:
                            with open(output_file, 'r', encoding='utf-8') as f:
                                lines = f.readlines()
                                if not lines:
                                    self.terminal.log(f"No output in {output_file}", "WARNING")
                                for line in lines:
                                    line = line.rstrip('\n')
                                    self.terminal.addItem(f"{line}")
                                    self.task_output_cache.append(f"{line}")
                                    if not self.terminal.user_scrolled or self.terminal.verticalScrollBar().value() == self.terminal.verticalScrollBar().maximum():
                                        self.terminal.scrollToBottom()
                                        self.terminal.user_scrolled = False
                            break
                        except IOError as e:
                            self.terminal.log(f"Error reading {output_file}: {str(e)}, retrying...", "ERROR")
                            time.sleep(1.0)
                    else:
                        self.terminal.log(f"Output file not found: {output_file}", "ERROR")
                        break
                errors_file = re.sub(r'gcbasic\.exe', 'errors.txt', command.strip('"'), flags=re.IGNORECASE)
                for _ in range(5):
                    if os.path.exists(errors_file):
                        try:
                            with open(errors_file, 'r', encoding='utf-8') as f:
                                lines = f.readlines()
                                if not lines:
                                    self.terminal.log(f"No output in {errors_file}", "WARNING")
                                else:
                                    self.terminal.log(f"Compiler errors detected in {errors_file}: {len(lines)} lines", "ERROR")
                                for line in lines:
                                    line = line.rstrip('\n')
                                    self.terminal.addItem(f"{line}")
                                    self.task_output_cache.append(f"{line}")
                                    if not self.terminal.user_scrolled or self.terminal.verticalScrollBar().value() == self.terminal.verticalScrollBar().maximum():
                                        self.terminal.scrollToBottom()
                                        self.terminal.user_scrolled = False
                            break
                        except IOError as e:
                            self.terminal.log(f"Error reading {errors_file}: {str(e)}, retrying...", "ERROR")
                            time.sleep(1.0)
                    break
                if process.returncode != 0:
                    if "GCBASIC.EXE" in command.upper():
                        self.terminal.log(f"Task '{task.get('label', 'Unnamed Task')}' failed", "ERROR")
                    else:
                        self.terminal.log(f"Task '{task.get('label', 'Unnamed Task')}' failed with exit code {process.returncode}", "ERROR")
            else:
                self.terminal.log(f"Executing Task: {' '.join(full_command)}", "INFO")
                self.terminal.scrollToBottom()
                self.terminal.user_scrolled = False
                QApplication.processEvents()
                current_tab = self.tabs.currentWidget()
                selected_text = current_tab.textCursor().selectedText() if current_tab else ""
                if not selected_text:
                    selected_text = ""
                placeholder = r'\${command:extension\.commandvariable\.selectedText}'
                full_command = [re.sub(placeholder, selected_text, arg) for arg in full_command]
                if not "debug" in task.get("label", "").lower():
                    subprocess.Popen(' '.join(full_command), cwd=cwd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(
                        f'start cmd.exe /K "cd /d {cwd} && {" ".join(full_command)} && pause && exit"',
                        shell=True
                    )
                if self.settings["show_info"]:
                    if SHOW_TASK_INFO:
                        self.terminal.addItem(f"[INFO] Launched Task: {' '.join(full_command)}")
                        self.task_output_cache.append(f"[INFO] Launched Task: {' '.join(full_command)}")
                    self.terminal.addItem("")
                    if not self.terminal.user_scrolled or self.terminal.verticalScrollBar().value() == self.terminal.verticalScrollBar().maximum():
                        self.terminal.scrollToBottom()
                        self.terminal.user_scrolled = False
        except Exception as e:
            self.terminal.log(f"Error executing task: {str(e)}", "ERROR")

    def open_language_file(self):
        language_file = self.settings.get("language_file")
        if os.path.exists(language_file):
            self.open_file_by_path(language_file)
            self.terminal.log(f"Opened language file: {language_file}", "INFO")
        else:
            self.terminal.log(f"Language file not found: {language_file}", "ERROR")

    def open_tasks_file(self):
        tasks_file = self.settings.get("tasks_file")
        if os.path.exists(tasks_file):
            self.open_file_by_path(tasks_file)
            self.terminal.log(f"Opened tasks file: {tasks_file}", "INFO")
        else:
            self.terminal.log(f"Tasks file not found: {tasks_file}", "ERROR")

    def apply_logging_settings(self):
        if self.info_action:
            self.info_action.setChecked(self.settings["show_info"])
        if self.error_action:
            self.error_action.setChecked(self.settings["show_errors"])
        if self.show_terminal_action:
            self.show_terminal_action.setChecked(self.settings["showTerminal"])
        self.terminal.log(f"Applied logging settings: \n\tshow Info \t\t{self.settings['show_info']} \n\tshow Errors: \t{self.settings['show_errors']} \n\tshow Terminal: \t{self.settings['showTerminal']}", "INFO")

    def apply_text_settings(self, text_edit=None):
        font_db = QFontDatabase()
        editor_font_name = self.settings.get("editor_font", "Consolas")
        # Validate font availability
        if not font_db.isFixedPitch(editor_font_name) or editor_font_name not in font_db.families():
            self.terminal.log(f"Font {editor_font_name} is not a valid monospaced font, falling back to Consolas", "WARNING")
            editor_font_name = "Consolas"
            self.settings["editor_font"] = "Consolas"
        ui_font = QFont("Arial", self.settings["ui_font_size"])
        editor_font = QFont(editor_font_name, self.settings["editor_font_size"])
        fg_color = "#FFFFFF" if self.settings["theme"] == "dark" else "#000000"
        QApplication.instance().setFont(ui_font)
        self.menuBar().setFont(ui_font)
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu:
                menu.setFont(ui_font)
                for sub_action in menu.actions():
                    sub_menu = sub_action.menu()
                    if sub_menu:
                        sub_menu.setFont(ui_font)
        self.tabs.setFont(ui_font)
        self.terminal.setFont(ui_font)
        if self.line_numbers_action:
            self.line_numbers_action.setChecked(self.settings["line_numbers"])
        if text_edit is None:
            for i in range(self.tabs.count()):
                self.apply_text_settings(self.tabs.widget(i))
        else:
            text_edit.setFont(editor_font)
            text_edit.document().setDefaultFont(editor_font)
            if self.settings["word_wrap"]:
                text_edit.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            else:
                text_edit.setWordWrapMode(QTextOption.NoWrap)
            text_edit.update_line_number_area_width()
            text_edit.line_number_area.setVisible(self.settings["line_numbers"])
            # Force Line Numbering refresh
            text_edit.line_number_area.hide()
            text_edit.line_number_area.show()
            text_edit.line_number_area.repaint()
            text_edit.viewport().update()
            text_edit.setStyleSheet(f"background: transparent; color: {fg_color};")
            if SHOW_FILE_INFO:
                self.terminal.log(f"Applied editor settings to text edit - font: {self.settings['editor_font']}, font_size: {self.settings['editor_font_size']}, line_numbers: {self.settings['line_numbers']}", "INFO")
                self.terminal.log(f"Line Numbering set to font: {self.settings['editor_font']}", "INFO")

    def save_recent_files(self):
        try:
            recent_names = [self.recent_files[i]["name"] if i < len(self.recent_files) else "" for i in range(10)]
            recent_dirs = [self.recent_files[i]["path"] if i < len(self.recent_files) else "" for i in range(10)]
            recent_data = {
                "RecentName": recent_names,
                "RecentDir": recent_dirs,
                "RecentN": 1
            }
            os.makedirs(os.path.dirname(self.recent_files_path), exist_ok=True)
            with open(self.recent_files_path, "w", encoding="utf-8") as f:
                json.dump(recent_data, f, indent=4)
            if SHOW_FILE_INFO:
                self.terminal.log(f"Saved recent files to {self.recent_files_path}", "INFO")
        except Exception as e:
            self.terminal.log(f"Error saving recent files to {self.recent_files_path}: {str(e)}", "ERROR")

    def open_file_by_path(self, file_path):
        if not os.path.exists(file_path):
            self.terminal.log(f"File {file_path} does not exist", "ERROR")
            normalized_path = self.normalize_path(file_path)
            if normalized_path in [self.normalize_path(entry["path"]) for entry in self.recent_files]:
                self.recent_files = [entry for entry in self.recent_files if self.normalize_path(entry["path"]) != normalized_path]
                self.save_recent_files()
            return
        normalized_path = self.normalize_path(file_path)
        for i in range(self.tabs.count()):
            if self.normalize_path(self.tabs.widget(i).file_path) == normalized_path:
                self.tabs.setCurrentWidget(self.tabs.widget(i))
                return
        try:
            current_mtime = os.path.getmtime(file_path)
            if file_path in self.file_cache and file_path in self.file_states:
                cached_mtime, _ = self.file_states[file_path]
                if current_mtime <= cached_mtime:
                    content = self.file_cache[file_path]
                else:
                    with open(file_path, "r") as f:
                        content = f.read()
                    self.file_cache[file_path] = content
            else:
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
        try:
            current_mtime = os.path.getmtime(file_path)
            self.file_states[file_path] = (current_mtime, None)
        except Exception as e:
            self.terminal.log(f"Error getting mtime for {file_path}: {str(e)}", "ERROR")
        text_edit.document().setModified(False)
        self.tabs.addTab(text_edit, os.path.basename(file_path))
        self.tabs.setCurrentWidget(text_edit)
        self.apply_text_settings(text_edit)
        text_edit.highlighter.schedule_highlighting()
        text_edit.document().setModified(False)
        if SHOW_HL_INFO:
            self.terminal.log(f"HL: Opened file {file_path} - undoRedoEnabled: {text_edit.isUndoRedoEnabled()}, isUndoAvailable: {text_edit.document().isUndoAvailable()}, isModified: {text_edit.document().isModified()}", "INFO")
        if SHOW_FILE_INFO:            
            self.terminal.log(f"Loaded file {file_path} with line_numbers: {self.settings['line_numbers']}", "INFO")
        if normalized_path in [self.normalize_path(entry["path"]) for entry in self.recent_files]:
            self.recent_files = [entry for entry in self.recent_files if self.normalize_path(entry["path"]) != normalized_path]
        self.recent_files.insert(0, {"name": os.path.basename(file_path), "path": file_path})
        if len(self.recent_files) > 10:
            self.recent_files.pop()
        self.save_recent_files()
        last_folder = os.path.dirname(os.path.abspath(file_path))
        self.settings['last_folder'] = last_folder
        self.save_settings()
        self.check_file_changes(text_edit)
        self.background_widget.update()

    def open_file(self):
        last_folder = self.settings.get('last_folder', os.path.expanduser('~'))
        if not os.path.exists(last_folder):
            last_folder = os.path.expanduser('~')
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open File",
            last_folder,
            "GCB Files (*.GCB);;Text Files (*.txt);;CSV Files (*.csv)"
        )
        if file_path:
            self.open_file_by_path(file_path)

    def save_file(self):
        current_tab = self.tabs.currentWidget()
        if current_tab and hasattr(current_tab, "file_path"):
            if current_tab.file_path.startswith("untitled_"):
                self.save_file_as()
            else:
                try:
                    with open(current_tab.file_path, "w") as f:
                        f.write(current_tab.toPlainText())
                        f.flush()
                        os.fsync(f.fileno())
                    current_tab.document().setModified(False)
                    if current_tab.file_path in self.file_cache:
                        del self.file_cache[current_tab.file_path]
                    current_mtime = os.path.getmtime(current_tab.file_path)
                    self.file_states[current_tab.file_path] = (current_mtime, None)
                    self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(current_tab.file_path))
                    normalized_path = self.normalize_path(current_tab.file_path)
                    if normalized_path in [self.normalize_path(entry["path"]) for entry in self.recent_files]:
                        self.recent_files = [entry for entry in self.recent_files if self.normalize_path(entry["path"]) != normalized_path]
                    self.recent_files.insert(0, {"name": os.path.basename(current_tab.file_path), "path": current_tab.file_path})
                    if len(self.recent_files) > 10:
                        self.recent_files.pop()
                    self.save_recent_files()
                    last_folder = os.path.dirname(os.path.abspath(current_tab.file_path))
                    self.settings['last_folder'] = last_folder
                    self.save_settings()
                except Exception as e:
                    self.terminal.log(f"Error saving {current_tab.file_path}: {str(e)}", "ERROR")

    def save_file_as(self):
        current_tab = self.tabs.currentWidget()
        if current_tab and hasattr(current_tab, "file_path"):
            last_folder = self.settings.get('last_folder', os.path.expanduser('~'))
            if not os.path.exists(last_folder):
                last_folder = os.path.expanduser('~')
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save File As",
                last_folder,
                "GCB Files (*.GCB);;Text Files (*.txt)"
            )
            if file_path:
                try:
                    with open(file_path, "w") as f:
                        f.write(current_tab.toPlainText())
                        f.flush()
                        os.fsync(f.fileno())
                    current_tab.file_path = os.path.abspath(file_path)
                    current_tab.document().setModified(False)
                    if file_path in self.file_cache:
                        del self.file_cache[file_path]
                    current_mtime = os.path.getmtime(file_path)
                    self.file_states[file_path] = (current_mtime, None)
                    self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(file_path))
                    normalized_path = self.normalize_path(file_path)
                    if normalized_path in [self.normalize_path(entry["path"]) for entry in self.recent_files]:
                        self.recent_files = [entry for entry in self.recent_files if self.normalize_path(entry["path"]) != normalized_path]
                    self.recent_files.insert(0, {"name": os.path.basename(file_path), "path": file_path})
                    if len(self.recent_files) > 10:
                        self.recent_files.pop()
                    self.save_recent_files()
                    last_folder = os.path.dirname(os.path.abspath(file_path))
                    self.settings['last_folder'] = last_folder
                    self.save_settings()
                except Exception as e:
                    self.terminal.log(f"Error saving {file_path}: {str(e)}", "ERROR")

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
        self.tabs.tabCloseRequested.disconnect(self.update_background_after_close)
        self.tabs.removeTab(index)
        self.tabs.tabCloseRequested.connect(self.update_background_after_close)
        self.background_widget.update()

    def close_current_file(self):
        current_index = self.tabs.currentIndex()
        if current_index != -1:
            self.close_tab(current_index)

    def check_file_changes(self, text_edit):
        if not self.settings.get("check_external_modifications", True):
            return
        if not hasattr(text_edit, "file_path") or text_edit.file_path.startswith("untitled_"):
            return
        file_path = text_edit.file_path
        try:
            current_mtime = os.path.getmtime(file_path)
            if file_path in self.file_states:
                last_mtime, user_choice = self.file_states[file_path]
                if current_mtime > last_mtime and user_choice != "ignore":
                    reply = QMessageBox.question(self, "File Changed",
                                                f"{file_path} has been modified externally. Reload?",
                                                QMessageBox.Yes | QMessageBox.No)
                    if reply == QMessageBox.Yes:
                        with open(file_path, "r") as f:
                            content = f.read()
                        text_edit.setPlainText(content)
                        text_edit.document().setModified(False)
                        self.file_cache[file_path] = content
                        text_edit.highlighter.schedule_highlighting()
                        self.file_states[file_path] = (current_mtime, "reload")
                    else:
                        self.file_states[file_path] = (current_mtime, "ignore")
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
        menu.setFont(QFont("Arial", self.settings["ui_font_size"]))
        seen = set()
        for index, entry in enumerate(self.recent_files[:10], 1):
            normalized_file = self.normalize_path(entry["path"])
            if normalized_file not in seen and os.path.exists(entry["path"]) and entry["path"] != "":
                action = menu.addAction(f"{index}. {entry['name']}")
                action.setFont(QFont("Arial", self.settings["ui_font_size"]))
                action.setToolTip(entry["path"])
                action.triggered.connect(lambda checked, f=entry["path"]: self.open_file_by_path(f))
                seen.add(normalized_file)
        if not seen:
            action = menu.addAction("No recent files")
            action.setFont(QFont("Arial", self.settings["ui_font_size"]))
            action.setEnabled(False)
        pos = self.mapToGlobal(QPoint(0, self.menuBar().height()))
        menu.exec_(pos)

    def clear_recent_files(self):
        self.recent_files.clear()
        self.save_recent_files()

    def undo(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            doc = current_tab.document()
            if SHOW_HL_INFO:
                self.terminal.log(f"Undo requested - isUndoAvailable: {doc.isUndoAvailable()}, modified: {doc.isModified()}", "INFO")
            if doc.isUndoAvailable():
                current_tab.undo()
                current_tab.highlighter.schedule_highlighting()
            else:
                if SHOW_HL_INFO:
                    self.terminal.log("HL: Undo not available", "INFO")

    def redo(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            doc = current_tab.document()
            if SHOW_HL_INFO:
                self.terminal.log(f"Redo requested - isRedoAvailable: {doc.isRedoAvailable()}, modified: {doc.isModified()}", "INFO")
            if doc.isRedoAvailable():
                current_tab.redo()
                current_tab.highlighter.schedule_highlighting()
            else:
                if SHOW_HL_INFO:
                    self.terminal.log("HL: Redo not available", "INFO")

    def cut(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.cut()
            current_tab.highlighter.schedule_highlighting()

    def copy(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.copy()

    def paste(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            current_tab.paste()
            current_tab.highlighter.schedule_highlighting()

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
                    current_tab.highlighter.schedule_highlighting()
                else:
                    pass

    def toggle_case(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            cursor = current_tab.textCursor()
            if cursor.hasSelection():
                start_pos = cursor.selectionStart()
                text = cursor.selectedText()
                new_text = text.lower() if text.isupper() else text.upper()
                cursor.insertText(new_text)
                cursor.setPosition(start_pos)
                cursor.setPosition(start_pos + len(new_text), QTextCursor.KeepAnchor)
                current_tab.setTextCursor(cursor)
                current_tab.highlighter.schedule_highlighting()

    def upper_case(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            cursor = current_tab.textCursor()
            if cursor.hasSelection():
                start_pos = cursor.selectionStart()
                text = cursor.selectedText()
                new_text = text.upper()
                cursor.insertText(new_text)
                cursor.setPosition(start_pos)
                cursor.setPosition(start_pos + len(new_text), QTextCursor.KeepAnchor)
                current_tab.setTextCursor(cursor)
                current_tab.highlighter.schedule_highlighting()

    def lower_case(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            cursor = current_tab.textCursor()
            if cursor.hasSelection():
                start_pos = cursor.selectionStart()
                text = cursor.selectedText()
                new_text = text.lower()
                cursor.insertText(new_text)
                cursor.setPosition(start_pos)
                cursor.setPosition(start_pos + len(new_text), QTextCursor.KeepAnchor)
                current_tab.setTextCursor(cursor)
                current_tab.highlighter.schedule_highlighting()

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
                    new_text = "/* " + selected_text + " */"
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
                    cursor.insertText(" */")
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
        current_tab.highlighter.schedule_highlighting()

    def indent(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            return
        cursor = self.tabs.currentWidget().textCursor()
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
            cursor.setPosition(end + indent_size, QTextCursor.KeepAnchor)
        else:
            cursor.insertText(" " * indent_size)
        cursor.endEditBlock()
        current_tab.highlighter.schedule_highlighting()

    def dedent(self):
        current_tab = self.tabs.currentWidget()
        if not current_tab:
            return
        cursor = self.tabs.currentWidget().textCursor()
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
        current_tab.highlighter.schedule_highlighting()

    def set_theme(self):
        themes = ["dark", "light"]
        theme, ok = QInputDialog.getItem(self, "Select Theme", "Theme:", themes, themes.index(self.settings["theme"]), False)
        if ok:
            self.settings["theme"] = theme
            self.apply_theme()
            self.background_widget.update()
            self.save_settings()
            for i in range(self.tabs.count()):
                self.tabs.widget(i).highlighter.schedule_highlighting()

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

    def set_ui_font_size(self):
        size, ok = QInputDialog.getInt(self, "UI Font Size", "Enter font size (8-24):", self.settings["ui_font_size"], 8, 24)
        if ok:
            self.settings["ui_font_size"] = size
            self.apply_text_settings()
            self.save_settings()

    def set_editor_font_size(self):
        size, ok = QInputDialog.getInt(self, "Editor Font Size", "Enter font size (8-24):", self.settings["editor_font_size"], 8, 24)
        if ok:
            self.settings["editor_font_size"] = size
            self.apply_text_settings()
            # self.terminal.log(f"Set editor_font_size to {size}", "INFO")
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
        self.info_action.setChecked(self.settings["show_info"])
        self.terminal.log(f"INFO logging {'enabled' if self.settings['show_info'] else 'disabled'}", "INFO")
        self.save_settings()

    def toggle_error_logs(self):
        self.settings["show_errors"] = not self.settings["show_errors"]
        self.error_action.setChecked(self.settings["show_errors"])
        self.terminal.log(f"ERROR logging {'enabled' if self.settings['show_errors'] else 'disabled'}", "INFO")
        self.save_settings()

    def toggle_terminal(self):
        self.settings["showTerminal"] = not self.settings["showTerminal"]
        self.show_terminal_action.setChecked(self.settings["showTerminal"])
        self.apply_terminal_settings()
        self.save_settings()

    def toggle_external_checks(self):
        self.settings["check_external_modifications"] = not self.settings["check_external_modifications"]
        if self.settings["check_external_modifications"]:
            self.check_file_timer.start(5000)
        else:
            self.check_file_timer.stop()
        self.terminal.log(f"External modification checks {'enabled' if self.settings['check_external_modifications'] else 'disabled'}", "INFO")
        self.save_settings()

    def set_terminal_size(self):
        size, ok = QInputDialog.getInt(self, "Terminal Size", "Height percentage (10-90):", self.settings["terminal_size_percentage"], 10, 90)
        if ok:
            self.settings["terminal_size_percentage"] = size
            self.apply_terminal_settings()
            self.save_settings()

    def reset_terminal_position(self):
        self.dock.setFloating(False)
        self.removeDockWidget(self.dock)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
        self.settings["terminal_size_percentage"] = 30
        self.settings["showTerminal"] = True
        self.apply_terminal_settings()
        self.save_settings()
        self.terminal.log("Terminal reset to default bottom position", "INFO")

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
        self.init_button_bar()

    def apply_terminal_settings(self):
        if self.settings["showTerminal"]:
            self.dock.show()
            if not self.dock.isFloating():
                window_height = self.height()
                terminal_height = int(window_height * (self.settings["terminal_size_percentage"] / 100.0))
                self.resizeDocks([self.dock], [terminal_height], Qt.Vertical)
        else:
            self.dock.hide()

    def apply_theme(self):
        if self.settings["theme"] == "dark":
            fg_color = "#FFFFFF"
            bg_color = "#2E2E2E"
            hover_color = "#555555"
            border_color = "#444444"
        else:
            fg_color = "#000000"
            bg_color = "#F5F5F5"
            hover_color = "#D3D3D3"
            border_color = "#CCCCCC"
        self.setStyleSheet(f"color: {fg_color};")
        menu_style = (
            f"QMenuBar {{ background-color: {bg_color}; color: {fg_color}; padding: 2px; }}"
            f"QMenuBar::item {{ background-color: {bg_color}; color: {fg_color}; padding: 2px 8px; }}"
            f"QMenuBar::item:selected {{ background-color: {hover_color}; }}"
            f"QMenu, QTextEdit QMenu {{ background-color: {bg_color}; color: {fg_color}; border: 1px solid {border_color}; }}"
            f"QMenu::item, QTextEdit QMenu::item {{ padding: 2px 16px; }}"
            f"QMenu::item:selected, QTextEdit QMenu::item:selected {{ background-color: {hover_color}; }}"
        )
        for i in range(self.tabs.count()):
            text_edit = self.tabs.widget(i)
            text_edit.setStyleSheet(f"background: transparent; color: {fg_color};")
            text_edit.line_number_area.update()
            text_edit.highlighter.schedule_highlighting()
        self.terminal.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
        self.dock.setStyleSheet(f"background-color: {bg_color}; color: {fg_color};")
        if self.button_bar:
            for i in range(self.button_bar.layout().count()):
                button = self.button_bar.layout().itemAt(i).widget()
                if isinstance(button, QPushButton):
                    button.setStyleSheet(
                        f"QPushButton {{ background-color: {bg_color}; "
                        f"border: 1px solid {border_color}; }}"
                        f"QPushButton:hover {{ background-color: {hover_color}; }}"
                    )
        QApplication.instance().setStyleSheet(menu_style)
        self.background_widget.update()

    def get_settings_path(self):
        config_dir = os.path.expanduser("~/.superide")
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        return os.path.join(config_dir, "ide_settings.json")

    def save_settings(self):
        # self.terminal.log("Calling save_settings", "INFO")
        self.settings["window_size"] = [self.width(), self.height()]
        self.settings["window_position"] = [self.pos().x(), self.pos().y()]
        try:
            dock_state = self.saveState().toBase64().data().decode('latin1')
            self.settings["dock_state"] = dock_state
        except Exception as e:
            self.terminal.log(f"Error saving dock state: {str(e)}", "ERROR")
        settings_path = self.get_settings_path()
        # Ensure editor_font is defined
        if "editor_font" not in self.settings or not isinstance(self.settings["editor_font"], str):
            self.settings["editor_font"] = "Consolas"
            # self.terminal.log("Ensured editor_font is set to Consolas before saving", "INFO")
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
            os.chmod(settings_path, 0o600)
            if SHOW_FILE_INFO:
                self.terminal.log(f"Saved settings to {settings_path} with editor_font: {self.settings['editor_font']}", "INFO")
        except Exception as e:
            self.terminal.log(f"Error saving settings to {settings_path}: {str(e)}", "ERROR")


    def load_settings(self):
        settings_path = self.get_settings_path()
        default_button_bar = {
            "button1": "[F5]:hexflash.png",
            "button2": "[F6]:hex.png",
            "button3": "[F7]:asm.png",
            "button4": "[F1]:help.png",
            "size": 24,
            "position": []
        }
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                loaded_settings = json.load(f)
                if SHOW_BAR_CONTROL:
                    self.terminal.log(f"Loaded settings from {settings_path}: {loaded_settings}", "INFO")
                # Load all keys with error handling
                for key in loaded_settings:
                    try:
                        self.settings[key] = loaded_settings[key]
                        # self.terminal.log(f"Loaded {key}: {self.settings[key]}", "INFO")
                    except Exception as e:
                        self.terminal.log(f"Error loading key {key}: {str(e)}", "ERROR")
                # Simplified editor_font validation
                font_db = QFontDatabase()
                if "editor_font" not in self.settings or not isinstance(self.settings["editor_font"], str):
                    self.settings["editor_font"] = "Consolas"
                    self.terminal.log("Set default editor_font to Consolas due to missing or invalid font", "INFO")
                else:
                    # Case-insensitive check for font availability
                    font_families = [f.lower() for f in font_db.families()]
                    if self.settings["editor_font"].lower() not in font_families:
                        self.terminal.log(f"Font {self.settings['editor_font']} not found in font database: {font_db.families()[:10]}...", "WARNING")
                        self.settings["editor_font"] = "Consolas"
                    else:
                        pass
                        # self.terminal.log(f"Loaded editor_font: {self.settings['editor_font']}", "INFO")
                if "editor_font_size" not in self.settings or not isinstance(self.settings["editor_font_size"], int):
                    self.settings["editor_font_size"] = 12
                    self.terminal.log("Set default editor_font_size to 12", "INFO")
                if "last_folder" not in self.settings or not isinstance(self.settings["last_folder"], str):
                    self.settings["last_folder"] = os.path.expanduser("~")
                    self.terminal.log("Set default last_folder to home directory", "INFO")
                if "window_size" not in self.settings or not isinstance(self.settings["window_size"], list) or len(self.settings["window_size"]) != 2:
                    self.settings["window_size"] = [800, 600]
                    self.terminal.log("Set default window_size to [800, 600]", "INFO")
                if "window_position" not in self.settings or not isinstance(self.settings["window_position"], list) or len(self.settings["window_position"]) != 2:
                    self.settings["window_position"] = [0, 0]
                    self.terminal.log("Set default window_position to [0, 0]", "INFO")
                if "button_bar" not in self.settings or not isinstance(self.settings["button_bar"], dict):
                    self.settings["button_bar"] = default_button_bar
                    self.terminal.log("Set default button_bar settings", "INFO")
                else:
                    for key, value in default_button_bar.items():
                        if key not in self.settings["button_bar"]:
                            self.settings["button_bar"][key] = value
                            self.terminal.log(f"Set default button_bar.{key} to {value}", "INFO")
                    if "position" not in self.settings["button_bar"] or not isinstance(self.settings["button_bar"]["position"], list):
                        self.settings["button_bar"]["position"] = []
                        self.terminal.log("Set default button_bar.position to []", "INFO")
                try:
                    if os.path.exists(self.recent_files_path):
                        with open(self.recent_files_path, "r", encoding="utf-8") as f:
                            recent_data = json.load(f)
                        recent_names = recent_data.get("RecentName", [])
                        recent_dirs = recent_data.get("RecentDir", [])
                        self.recent_files = [
                            {"name": name, "path": path}
                            for name, path in zip(recent_names, recent_dirs)
                            if path != "" and os.path.exists(path)
                        ]
                        self.recent_files = self.recent_files[:10]
                        if SHOW_FILE_INFO:
                            self.terminal.log(f"Loaded recent files from {self.recent_files_path}", "INFO")
                    else:
                        self.recent_files = []
                        self.save_recent_files()
                        self.terminal.log(f"Created new recent files file at {self.recent_files_path}", "INFO")
                except Exception as e:
                    self.terminal.log(f"Error loading recent files from {self.recent_files_path}: {str(e)}", "ERROR")
                    self.recent_files = []
                    self.save_recent_files()
                if "dock_state" in loaded_settings:
                    try:
                        self.restoreState(QByteArray.fromBase64(loaded_settings["dock_state"].encode()))
                        if SHOW_TERMINAL_INFO:
                            self.terminal.log("Restored Terminal dock position", "INFO")
                    except Exception as e:
                        self.terminal.log(f"Error restoring dock state: {str(e)}", "ERROR")
                        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
                else:
                    self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
                self.apply_screen_size_and_position()
                self.apply_text_settings()
                self.apply_theme()
                self.apply_terminal_settings()
        except FileNotFoundError:
            self.terminal.log("No IDE setting file found, using default parameters", "INFO")
            self.recent_files = []
            self.save_recent_files()
            self.settings["last_folder"] = os.path.expanduser("~")
            self.set_default_geometry()
            self.settings["editor_font"] = "Consolas"
            self.settings["editor_font_size"] = 12
            self.settings["button_bar"] = default_button_bar
            self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
            self.apply_screen_size_and_position()
            self.apply_text_settings()
            self.apply_theme()
            self.apply_terminal_settings()
            self.first_time_settings = True
            self.terminal.log("First-time settings created, flag set to open demo files", "INFO")
        except json.JSONDecodeError as e:
            self.terminal.log(f"Invalid settings file, using defaults: {str(e)}", "ERROR")
            self.recent_files = []
            self.save_recent_files()
            self.settings["last_folder"] = os.path.expanduser("~")
            self.set_default_geometry()
            self.settings["editor_font"] = "Consolas"
            self.settings["editor_font_size"] = 12
            self.settings["button_bar"] = default_button_bar
            self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
            self.apply_screen_size_and_position()
            self.apply_text_settings()
            self.apply_theme()
            self.apply_terminal_settings()
            self.first_time_settings = True
            self.terminal.log("Invalid settings file detected, flag set to open demo files", "INFO")
        except Exception as e:
            self.terminal.log(f"Error loading settings: {str(e)}", "ERROR")
            self.recent_files = []
            self.save_recent_files()
            self.settings["last_folder"] = os.path.expanduser("~")
            self.set_default_geometry()
            self.settings["editor_font"] = "Consolas"
            self.settings["editor_font_size"] = 12
            self.settings["button_bar"] = default_button_bar
            self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
            self.apply_screen_size_and_position()
            self.apply_text_settings()
            self.apply_theme()
            self.apply_terminal_settings()
            self.first_time_settings = True
            self.terminal.log("Error loading settings, flag set to open demo files", "INFO")


    def set_default_geometry(self):
        self.settings["window_size"] = [800, 600]
        self.settings["window_position"] = [0, 0]

    def toggle_line_numbers(self):
        self.settings["line_numbers"] = not self.settings["line_numbers"]
        if self.line_numbers_action:
            self.line_numbers_action.setChecked(self.settings["line_numbers"])
        self.apply_text_settings()
        self.terminal.log(f"Line numbers {'enabled' if self.settings['line_numbers'] else 'disabled'}", "INFO")
        self.save_settings()

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
        gcbasic_header = "/*\n    A GCBASIC source program\n*/\n\n#CHIP {specify your chip, removing the braces}\n#OPTION EXPLICIT\n\n  Do\n    PulseOut PORTB.5, 100 ms\n    Wait 100 ms\n  Loop\n"
        text_edit.setDocument(QTextDocument(gcbasic_header))
        text_edit.file_path = f"untitled_{uuid.uuid4().hex[:8]}.gcb"
        text_edit.textChanged.connect(lambda: self.record_history(text_edit))
        self.tabs.addTab(text_edit, "untitled.gcb")
        self.tabs.setCurrentWidget(text_edit)
        self.apply_text_settings(text_edit)
        text_edit.highlighter.schedule_highlighting()
        text_edit.document().setModified(False)
        if SHOW_HL_INFO:
            self.terminal.log(f"HL: Created new file - undoRedoEnabled: {text_edit.isUndoRedoEnabled()}, isUndoAvailable: {text_edit.document().isUndoAvailable()}, isModified: {text_edit.document().isModified()}", "INFO")
        self.terminal.log(f"Created new file with line_numbers: {self.settings['line_numbers']}", "INFO")
        self.background_widget.update()

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
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", 12345))
    except socket.error as e:
        if e.errno == errno.EADDRINUSE:
            print("Another instance of the IDE is already running.")
            sys.exit(1)
        else:
            raise
    app = QApplication(sys.argv)
    filename = sys.argv[1] if len(sys.argv) > 1 else None
    ide = IDE(filename)
    ide.show()
    sys.exit(app.exec_())