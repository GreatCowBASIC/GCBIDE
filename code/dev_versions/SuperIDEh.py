# issues
# - no toolbar support

import sys
import os
import os.path
import json
import re
import html
import socket
import errno
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
                             QMenuBar, QAction, QFileDialog, QDockWidget, QListWidget, QMessageBox,
                             QInputDialog, QMenu, QFrame, QDialog, QDialogButtonBox, QTextBrowser)
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
from PyQt5.QtGui import QTextOption, QTextDocument, QFont, QPainter, QFontMetrics, QTextCursor, QIcon, QTextCharFormat, QColor, QImage
from PyQt5.QtCore import Qt, QUrl, QPoint, QTimer, QRect
from PyQt5.QtGui import QDesktopServices, QTextBlockUserData
from collections import deque
import uuid

# Global flag for HL: INFO messages (not user-settable)
SHOW_HL_INFO = False  # Disabled to reduce clutter
SHOW_FONT_CONTROL = False

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
        font = QFont("Consolas", self.editor.ide.settings["editor_font_size"])
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

    def get_text(self):
        return self.text

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
        self.highlight_timer.setInterval(500)  # Increased to 500ms to reduce frequency
        self.highlight_pending = False
        self.last_visible_range = None
        self.highlighted_blocks = set()  # Track highlighted block numbers
        self.load_highlighting_rules()
        # Connect contentsChange signal to track text changes
        self.text_edit.document().contentsChange.connect(self.on_contents_change)
        self.pending_changes = []  # Store pending change regions

    def load_highlighting_rules(self):
        """Load highlighting rules from JSON configuration."""
        language_file = self.ide.settings.get("language_file", resource_path("GCB.tmLanguage.json"))
        if not os.path.exists(language_file):
            language_file = resource_path("GCB.tmLanguage.json")
            if not os.path.exists(language_file):
                if SHOW_HL_INFO:
                    self.ide.terminal.log("HL: Language file GCB.tmLanguage.json not found in local folder", "ERROR")
                return

        try:
            with open(language_file, "r", encoding="utf-8") as f:
                config = json.load(f)
                self.highlighting_rules = []

                # Load and compile block comment patterns
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

                # Load syntax patterns
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
                        # Compile regex with case sensitivity flag
                        flags = re.IGNORECASE if case_insensitive else 0
                        compiled_pattern = re.compile(pattern, flags)
                        self.highlighting_rules.append((compiled_pattern, format))
                        if SHOW_HL_INFO:
                            self.ide.terminal.log(f"HL: Loaded rule - Pattern: {pattern}, Color: {rule['color']}, Case Insensitive: {case_insensitive}", "INFO")
                    except re.error as e:
                        if SHOW_HL_INFO:
                            self.ide.terminal.log(f"HL: Invalid regex pattern '{rule.get('match', 'unknown')}' in JSON: {str(e)}", "ERROR")
                    except Exception as e:
                        if SHOW_HL_INFO:
                            self.ide.terminal.log(f"HL: Error processing rule {rule.get('match', 'unknown')}: {str(e)}", "ERROR")

        except json.JSONDecodeError as e:
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: Corrupted JSON in {language_file}: {str(e)}", "ERROR")
        except Exception as e:
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: Error loading {language_file}: {str(e)}", "ERROR")

    def schedule_highlighting(self):
        """Schedule highlighting with a debounce delay."""
        if not self.highlight_pending:
            self.highlight_pending = True
            self.highlight_timer.start()

    def on_contents_change(self, position, chars_removed, chars_added):
        """Track document changes to highlight only affected regions."""
        if chars_removed > 0 or chars_added > 0:
            doc = self.text_edit.document()
            start_block = doc.findBlock(position)
            end_position = position + chars_added
            end_block = doc.findBlock(end_position)
            if not end_block.isValid():
                end_block = doc.lastBlock()
            self.pending_changes.append((start_block.blockNumber(), end_block.blockNumber()))
            self.schedule_highlighting()
            self.ide.terminal.log(f"Contents changed - pos: {position}, removed: {chars_removed}, added: {chars_added}, blocks: {start_block.blockNumber()}-{end_block.blockNumber()}", "INFO")

    def _apply_highlighting(self):
        """Apply highlighting to changed or visible text, preserving undo stack and modified state."""
        if not hasattr(self.text_edit, "file_path") or not self.text_edit.file_path.lower().endswith(".gcb"):
            self.highlight_pending = False
            return

        if not self.block_comment_start or not self.block_comment_end:
            if SHOW_HL_INFO:
                self.ide.terminal.log("HL: Block comment patterns invalid, skipping block comment highlighting", "ERROR")
            self.highlight_pending = False
            return

        doc = self.text_edit.document()
        # Store the modified state before highlighting
        was_modified = doc.isModified()
        if SHOW_HL_INFO:
            self.ide.terminal.log(f"HL: Before highlighting - isUndoAvailable: {doc.isUndoAvailable()}, isModified: {was_modified}", "INFO")

        # Determine blocks to highlight (visible + pending changes)
        cursor = self.text_edit.cursorForPosition(self.text_edit.viewport().pos())
        first_visible_block = doc.findBlock(cursor.position())
        last_visible_block = doc.findBlock(self.text_edit.cursorForPosition(
            self.text_edit.viewport().rect().bottomLeft()).position())
        visible_range = (first_visible_block.blockNumber(), last_visible_block.blockNumber() if last_visible_block.isValid() else doc.blockCount() - 1)

        # Collect block numbers to highlight from pending changes
        blocks_to_highlight = set()  # Store block numbers (integers), not QTextBlock
        for start_block_num, end_block_num in self.pending_changes:
            for block_num in range(start_block_num, end_block_num + 1):
                block = doc.findBlockByNumber(block_num)
                if block.isValid():
                    blocks_to_highlight.add(block_num)  # Add block number
            if SHOW_HL_INFO:
                self.ide.terminal.log(f"HL: Added pending blocks {start_block_num}-{end_block_num} to highlight", "INFO")
        self.pending_changes.clear()  # Clear processed changes

        # Add visible blocks not recently highlighted
        block = first_visible_block
        while block.isValid() and block.blockNumber() <= visible_range[1]:
            block_num = block.blockNumber()
            if block_num not in self.highlighted_blocks:
                blocks_to_highlight.add(block_num)  # Add block number
            block = block.next()

        if not blocks_to_highlight:
            if SHOW_HL_INFO:
                self.ide.terminal.log("HL: No blocks to highlight", "INFO")
            self.highlight_pending = False
            return

        # Determine block comment state
        in_block_comment = False
        block = doc.firstBlock()
        while block.isValid() and block.blockNumber() < visible_range[0]:
            block_data = block.userData()
            if block_data:
                in_block_comment = block_data.get_in_block_comment()
            block = block.next()

        max_total_ranges = 10000
        was_undo_enabled = doc.isUndoRedoEnabled()
        doc.setUndoRedoEnabled(False)  # Disable undo to minimize stack impact

        try:
            for block_num in sorted(blocks_to_highlight):  # Iterate over sorted block numbers
                block = doc.findBlockByNumber(block_num)
                if not block.isValid():
                    if SHOW_HL_INFO:
                        self.ide.terminal.log(f"HL: Invalid block number {block_num}, skipping", "ERROR")
                    continue
                text = block.text()
                block_length = len(text)
                format_ranges = []
                block_number = block.blockNumber()

                # Check block comment state
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

                # Apply other highlighting rules
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

                # Apply formats with minimal undo impact
                cursor = QTextCursor(block)
                cursor.beginEditBlock()
                try:
                    # Clear existing formats
                    cursor.setPosition(block.position())
                    cursor.setPosition(block.position() + block_length, QTextCursor.KeepAnchor)
                    cursor.setCharFormat(QTextCharFormat())  # Reset to default format

                    # Apply new formats
                    for start, end, format in format_ranges:
                        if end > block_length:
                            if SHOW_HL_INFO:
                                self.ide.terminal.log(f"HL: Skipping invalid range ({start}, {end}) in block {block_number}", "ERROR")
                            continue
                        cursor.setPosition(block.position() + start)
                        cursor.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                        cursor.mergeCharFormat(format)  # Use mergeCharFormat to minimize undo impact
                finally:
                    cursor.endEditBlock()

                self.highlighted_blocks.add(block_number)
                block.setUserData(TextBlockData(block.text(), in_block_comment))

        finally:
            doc.setUndoRedoEnabled(was_undo_enabled)
            # Restore the original modified state to prevent highlighting from marking document as modified
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

    def on_scroll(self, value):
        """Schedule highlighting on scroll."""
        if not self._is_highlighting:
            self.highlighter.schedule_highlighting()

    def keyPressEvent(self, event):
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
        # Schedule highlighting only for text-modifying keys
        if event.text() or event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            if not self._is_highlighting:
                self.highlighter.schedule_highlighting()

    def on_text_changed(self):
        """Schedule highlighting and log state."""
        if not self._is_highlighting:
            self._is_highlighting = True
            try:
                if SHOW_HL_INFO:
                    self.ide.terminal.log(f"HL: Text changed - isUndoAvailable: {self.document().isUndoAvailable()}, isModified: {self.document().isModified()}", "INFO")
                self.highlighter.schedule_highlighting()  # Rely on contentsChange for block tracking
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
            "ui_font_size": 12,
            "editor_font_size": 12,
            "indent_size": 4,
            "recent_files": [],
            "goto_marker_duration": 3,
            "showTerminal": True,
            "terminal_size_percentage": 30,
            "language_file": resource_path("GCB.tmLanguage.json"),
            "check_external_modifications": True
        }
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
        self.terminal = TerminalWindow()
        self.dock = QDockWidget("Terminal", self)
        self.dock.setWidget(self.terminal)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
        self.background_widget = BackgroundWidget(self)
        self.setCentralWidget(self.background_widget)
        self.central_layout = QVBoxLayout(self.background_widget)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.setStyleSheet("QTabWidget::pane { background: transparent; border: 0; } "
                               "QTabBar::tab { background: transparent; } "
                               "QTabWidget > QWidget > QWidget { background: transparent; } "
                               "QTextEdit { background: transparent; }")
        self.central_layout.addWidget(self.tabs, 1)
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
        self.apply_logging_settings()
        if filename and os.path.exists(filename):
            self.open_file_by_path(filename)
        self.terminal.log(f"Logging Status - INFO: {self.settings['show_info']}, ERROR: {self.settings['show_errors']}", "INFO")
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
        appearance_menu = settings_menu.addMenu("&Appearance")
        editor_menu = settings_menu.addMenu("&Editor")
        logging_menu = settings_menu.addMenu("&Logging")
        recent_files_menu = settings_menu.addMenu("&Recent Files")
        ui_font_action = QAction("&UI Font Size", self)
        ui_font_action.triggered.connect(self.set_ui_font_size)
        appearance_menu.addAction(ui_font_action)
        editor_font_action = QAction("&Editor Font Size", self)
        editor_font_action.triggered.connect(self.set_editor_font_size)
        appearance_menu.addAction(editor_font_action)
        screen_size_action = QAction("&Screen Size and Position", self)
        screen_size_action.triggered.connect(self.set_screen_size_and_position)
        appearance_menu.addAction(screen_size_action)
        language_file_action = QAction("&Language File", self)
        language_file_action.triggered.connect(self.set_language_file)
        appearance_menu.addAction(language_file_action)
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
        recent_files_menu.addSeparator()
        clear_recent_action = QAction("&Clear Recent Files", self)
        clear_recent_action.triggered.connect(self.clear_recent_files)
        recent_files_menu.addAction(clear_recent_action)
        about_action = QAction("&About", self)
        about_action.triggered.connect(lambda: QMessageBox.information(self, "About", "GCBASIC Essential IDE v1.0"))
        help_menu.addAction(about_action)
        license_action = QAction("&License", self)
        license_action.triggered.connect(self.show_license)
        help_menu.addAction(license_action)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self.show_tab_context_menu)
        self.tabs.installEventFilter(self)

    def apply_logging_settings(self):
        """Update logging menu actions to reflect loaded settings."""
        if self.info_action:
            self.info_action.setChecked(self.settings["show_info"])
        if self.error_action:
            self.error_action.setChecked(self.settings["show_errors"])
        if self.show_terminal_action:
            self.show_terminal_action.setChecked(self.settings["showTerminal"])
        self.terminal.log(f"Applied logging settings - show_info: {self.settings['show_info']}, show_errors: {self.settings['show_errors']}, showTerminal: {self.settings['showTerminal']}", "INFO")

    def apply_text_settings(self, text_edit=None):
        ui_font = QFont("Arial", self.settings["ui_font_size"])
        editor_font = QFont("Consolas", self.settings["editor_font_size"])
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
            self.line_numbers_action.setChecked(self.settings["line_numbers"])  # Sync menu action
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
            text_edit.line_number_area.setVisible(self.settings["line_numbers"])  # Explicitly set visibility
            text_edit.line_number_area.update()  # Force update
            text_edit.viewport().update()  # Force repaint
            text_edit.setStyleSheet(f"background: transparent; color: {fg_color};")
            self.terminal.log(f"Applied editor settings to text edit - font_size: {self.settings['editor_font_size']}, line_numbers: {self.settings['line_numbers']}", "INFO")

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
        self.terminal.log(f"Loaded file {file_path} with line_numbers: {self.settings['line_numbers']}", "INFO")
        if normalized_path in [self.normalize_path(p) for p in self.recent_files]:
            self.recent_files = [p for p in self.recent_files if self.normalize_path(p) != normalized_path]
        self.recent_files.insert(0, file_path)
        if len(self.recent_files) > 10:
            self.recent_files.pop()
        self.save_settings()
        self.background_widget.update()

    def load_settings(self):
        settings_path = self.get_settings_path()
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                loaded_settings = json.load(f)
                for key in self.settings.keys():
                    if key in loaded_settings:
                        self.settings[key] = loaded_settings[key]
                if "editor_font_size" not in self.settings or not isinstance(self.settings["editor_font_size"], int):
                    self.settings["editor_font_size"] = 12
                    self.terminal.log("Set default editor_font_size to 12", "INFO")
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
                self.apply_screen_size_and_position()
                self.apply_text_settings()  # Ensure all tabs reflect loaded settings
                self.apply_theme()
                self.apply_terminal_settings()
                self.terminal.log(f"Loaded settings - editor_font_size: {self.settings['editor_font_size']}, line_numbers: {self.settings['line_numbers']}", "INFO")
        except FileNotFoundError:
            self.set_default_geometry()
            self.settings["editor_font_size"] = 12
            self.terminal.log("Settings file not found, set default editor_font_size to 12", "INFO")
        except json.JSONDecodeError:
            self.terminal.log("Invalid settings file, using defaults", "ERROR")
            self.set_default_geometry()
            self.settings["editor_font_size"] = 12
            self.terminal.log("Set default editor_font_size to 12 due to invalid settings", "INFO")
        except Exception as e:
            self.terminal.log(f"Error loading settings: {str(e)}", "ERROR")
            self.set_default_geometry()
            self.settings["editor_font_size"] = 12
            self.terminal.log("Set default editor_font_size to 12 due to error", "INFO")

    def toggle_line_numbers(self):
        self.settings["line_numbers"] = not self.settings["line_numbers"]
        if self.line_numbers_action:
            self.line_numbers_action.setChecked(self.settings["line_numbers"])  # Sync menu action
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

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "GCB Files (*.GCB);;Text Files (*.txt);;CSV Files (*.csv)")
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

    def save_file_as(self):
        current_tab = self.tabs.currentWidget()
        if current_tab and hasattr(current_tab, "file_path"):
            file_path, _ = QFileDialog.getSaveFileName(self, "Save File As", "", "GCB Files (*.GCB);;Text Files (*.txt)")
            if file_path:
                try:
                    with open(file_path, "w") as f:
                        f.write(current_tab.toPlainText())
                    current_tab.file_path = os.path.abspath(file_path)
                    current_tab.document().setModified(False)
                    self.file_cache[file_path] = current_tab.toPlainText()
                    self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(file_path))
                    normalized_path = self.normalize_path(file_path)
                    if normalized_path in [self.normalize_path(p) for p in self.recent_files]:
                        self.recent_files = [p for p in self.recent_files if self.normalize_path(p) != normalized_path]
                    self.recent_files.insert(0, file_path)
                    if len(self.recent_files) > 10:
                        self.recent_files.pop()
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
        self.tabs.removeTab(index)
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
                if current_mtime > last_mtime and text_edit.document().isModified() and user_choice != "ignore":
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
        for index, file in enumerate(self.recent_files[:10], 1):
            normalized_file = self.normalize_path(file)
            if normalized_file not in seen and os.path.exists(file):
                action = menu.addAction(f"{index}. {os.path.basename(file)}")
                action.setFont(QFont("Arial", self.settings["ui_font_size"]))
                action.setToolTip(file)
                action.triggered.connect(lambda checked, f=file: self.open_file_by_path(f))
                seen.add(normalized_file)
        if not seen:
            action = menu.addAction("No recent files")
            action.setFont(QFont("Arial", self.settings["ui_font_size"]))
            action.setEnabled(False)
        pos = self.mapToGlobal(QPoint(0, self.menuBar().height()))
        menu.exec_(pos)

    def show_recent_files_in_settings(self):
        menu = QMenu()
        menu.setFont(QFont("Arial", self.settings["ui_font_size"]))
        seen = set()
        for index, file in enumerate(self.recent_files[:10], 1):
            normalized_file = self.normalize_path(file)
            if normalized_file not in seen and os.path.exists(file):
                action = menu.addAction(f"{index}. {os.path.basename(file)}")
                action.setFont(QFont("Arial", self.settings["ui_font_size"]))
                action.setToolTip(file)
                action.triggered.connect(lambda checked, f=file: self.open_file_by_path(f))
                seen.add(normalized_file)
        if not seen:
            action = menu.addAction("No recent files")
            action.setFont(QFont("Arial", self.settings["ui_font_size"]))
            action.setEnabled(False)
        pos = self.sender().mapToGlobal(QPoint(0, self.sender().height()))
        menu.exec_(pos)

    def clear_recent_files(self):
        self.recent_files.clear()
        self.save_settings()

    def undo(self):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            doc = current_tab.document()
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
                text = cursor.selectedText()
                if text.isupper():
                    cursor.insertText(text.lower())
                else:
                    cursor.insertText(text.upper())
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

    def set_language_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Language File", "", "JSON Files (*.json)")
        if file_path:
            self.settings["language_file"] = file_path
            self.save_settings()
            for i in range(self.tabs.count()):
                text_edit = self.tabs.widget(i)
                text_edit.highlighter.load_highlighting_rules()
                text_edit.highlighter.schedule_highlighting()
            self.terminal.log(f"Set language file to {file_path}", "INFO")

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
            self.terminal.log(f"Set editor_font_size to {size}", "INFO")
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
        self.terminal.log(f"External modification checks {'enabled' if self.settings['check_external_modifications'] else 'disabled'}", "INFO")
        self.save_settings()

    def set_terminal_size(self):
        size, ok = QInputDialog.getInt(self, "Terminal Size", "Height percentage (10-90):", self.settings["terminal_size_percentage"], 10, 90)
        if ok:
            self.settings["terminal_size_percentage"] = size
            self.apply_terminal_settings()
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

    def apply_terminal_settings(self):
        if self.settings["showTerminal"]:
            self.dock.show()
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
        QApplication.instance().setStyleSheet(menu_style)
        self.background_widget.update()

    def get_settings_path(self):
        config_dir = os.path.expanduser("~/.superide")
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        return os.path.join(config_dir, "ide_settings.json")

    def save_settings(self):
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
    # Check for single instance
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", 12345))  # Arbitrary port
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