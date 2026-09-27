"""Dashboard surfaces and a focus overlay that preserves the live widgets."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


STYLE = """
QMainWindow, QWidget#console { background: #0b0e14; color: #edf3fa; }
QWidget { color: #edf3fa; font-family: 'Segoe UI'; font-size: 12px; }
QFrame#toolbar, QFrame#rail { background: #111721; border: 1px solid #293444; border-radius: 8px; }
QFrame#panel, QFrame#metric { background: #151c28; border: 1px solid #2c3748; border-radius: 8px; }
QFrame#healthPanel { background: #131d29; border: 1px solid #2c3748; border-radius: 6px; }
QFrame#focusCard { background: #151c28; border: 1px solid #637e90; border-radius: 10px; }
QLabel { background: transparent; border: none; }
QLabel#brand { font-size: 20px; font-weight: 600; }
QLabel#section { color: #a9bbcb; font-size: 11px; font-weight: 600; }
QLabel#muted { color: #a9bbcb; font-size: 11px; }
QLabel#value { font-size: 21px; font-weight: 600; }
QLabel#motionState { font-size: 25px; font-weight: 600; color: #ebc66d; }
QLabel#panelTitle { font-size: 12px; font-weight: 600; }
QLabel#healthValue { color: #86d8a6; font-size: 11px; font-weight: 600; }
QPushButton, QComboBox { background: #1c2735; border: 1px solid #354457; border-radius: 5px; padding: 7px 10px; }
QPushButton:hover, QComboBox:hover { border-color: #61a9bc; background: #233346; }
QPushButton:pressed, QPushButton:checked { background: #1d4755; border-color: #55b7cd; }
QPushButton:disabled, QComboBox:disabled { color: #6f8092; border-color: #293443; background: #161e29; }
QPushButton#primary { background: #24677c; border-color: #3f8498; font-weight: 600; }
QPushButton#danger { color: #f3a2a8; border-color: #73434e; }
QPushButton#expand { padding: 4px 9px; color: #b7c9d9; font-size: 11px; }
QPushButton#terminalFilter { color: #aebfd0; background: #111a26; font-size: 11px; padding: 6px 8px; text-align: left; }
QPushButton#terminalFilter:checked { color: #effbff; background: #1d5264; border-color: #55b7cd; }
QToolButton#healthToggle { color: #a9bbcb; background: transparent; border: none; font-size: 10px; font-weight: 600; padding: 2px 0; }
QToolButton#healthToggle:hover { color: #edf3fa; }
QPushButton#resumeLive { color: #edf3fa; background: #1d5264; border: 1px solid #55b7cd; border-radius: 10px; padding: 4px 9px; font-size: 10px; font-weight: 600; }
QSlider::groove:horizontal { height: 4px; background: #2a3a4c; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #55b7cd; border: 1px solid #87d7e5; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #24677c; border-radius: 2px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #1c2735; selection-background-color: #275c70; }
QTextEdit { background: #0f151e; border: none; color: #a9c8bd; font-family: Consolas; font-size: 11px; }
QSplitter::handle { background: #0b0e14; }
QSplitter::handle:hover { background: #35495b; }
QToolTip { color: #edf3fa; background: #243448; border: 1px solid #526578; padding: 5px; }
"""


class Panel(QFrame):
    def __init__(self, title, content, expand=None, header_action=None):
        super().__init__()
        self.setObjectName("panel")
        self.title, self.content = title, content
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(10, 8, 10, 10)
        self.box.setSpacing(7)
        header = QHBoxLayout()
        name = QLabel(title)
        name.setObjectName("panelTitle")
        header.addWidget(name)
        header.addStretch()
        if header_action:
            header.addWidget(header_action)
        if expand:
            self.expand_button = QPushButton("Expand ↗")
            self.expand_button.setObjectName("expand")
            self.expand_button.setToolTip("Focus this view; Escape returns to the dashboard")
            self.expand_button.clicked.connect(lambda: expand(self))
            header.addWidget(self.expand_button)
        self.box.addLayout(header)
        self.box.addWidget(content, 1)


class FocusOverlay(QWidget):
    """Move the same widget within the same top-level window; never clone plots."""

    def __init__(self, parent):
        super().__init__(parent)
        self.source = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 40, 48, 40)
        self.card = QFrame()
        self.card.setObjectName("focusCard")
        self.box = QVBoxLayout(self.card)
        self.box.setContentsMargins(14, 12, 14, 14)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        header.addWidget(self.title)
        header.addStretch()
        close = QPushButton("Close ×  ·  Esc")
        close.clicked.connect(self.restore)
        header.addWidget(close)
        self.close_button = close
        self.box.addLayout(header)
        outer.addWidget(self.card)
        self.hide()

    def expand(self, panel):
        if self.source is not None:
            self.restore()
        self.source = panel
        self.placeholder = QLabel()
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setPixmap(panel.content.grab())
        self.placeholder.setScaledContents(True)
        self.placeholder.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        panel.box.removeWidget(panel.content)
        panel.box.addWidget(self.placeholder, 1)
        self.box.addWidget(panel.content, 1)
        panel.content.show()
        self.title.setText(panel.title)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.close_button.setFocus()

    def restore(self):
        if self.source is None:
            return
        panel = self.source
        self.box.removeWidget(panel.content)
        panel.box.removeWidget(self.placeholder)
        self.placeholder.deleteLater()
        panel.box.addWidget(panel.content, 1)
        panel.content.show()
        self.source = None
        self.hide()
        panel.expand_button.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(2, 5, 12, 205))

    def mousePressEvent(self, event):
        if not self.card.geometry().contains(event.position().toPoint()):
            self.restore()
