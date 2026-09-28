"""Dashboard surfaces and a focus overlay that preserves the live widgets."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget


SCROLLBAR_STYLE = """
QScrollBar:vertical { background: #1d5264; width: 8px; margin: 4px 1px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #edf3fa; min-height: 24px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #ffffff; }
QScrollBar::handle:vertical:pressed { background: #d1e6ef; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""


def fit_window_to_screen(window, preferred, minimum, reference=None):
    """Use Qt's logical screen geometry so high-DPI and smaller displays both fit."""
    screen = (reference.screen() if reference is not None else window.screen()) or QGuiApplication.primaryScreen()
    if screen is None:
        width, height = preferred
    else:
        available = screen.availableGeometry()
        width = min(preferred[0], max(1, available.width() - 48))
        height = min(preferred[1], max(1, available.height() - 48))
    window.setMinimumSize(min(minimum[0], width), min(minimum[1], height))
    window.resize(width, height)
    if screen is not None:
        available = screen.availableGeometry()
        window.move(available.x() + max(0, (available.width() - width) // 2),
                    available.y() + max(0, (available.height() - height) // 2))


STYLE = SCROLLBAR_STYLE + """
QMainWindow, QWidget#console { background: #0b0e14; color: #edf3fa; }
QWidget { color: #edf3fa; font-family: 'Segoe UI'; font-size: 12px; }
QFrame#toolbar, QFrame#rail { background: #111721; border: 1px solid #293444; border-radius: 8px; }
QScrollArea#railScroll { background: #111721; border: 1px solid #293444; border-radius: 8px; }
QScrollArea#railScroll > QWidget > QWidget { background: #111721; }
QFrame#panel, QFrame#metric { background: #151c28; border: 1px solid #2c3748; border-radius: 8px; }
QFrame#healthPanel { background: #131d29; border: 1px solid #2c3748; border-radius: 6px; }
QFrame#focusCard { background: #151c28; border: 1px solid #637e90; border-radius: 10px; }
QFrame#focusControls { background: #111721; border: 1px solid #293444; border-radius: 6px; }
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
QFrame#focusControls QPushButton { padding: 5px 9px; font-size: 11px; }
QPushButton#terminalFilter { color: #aebfd0; background: #111a26; font-size: 11px; padding: 6px 8px; text-align: left; }
QPushButton#terminalFilter:checked { color: #effbff; background: #1d5264; border-color: #55b7cd; }
QToolButton#healthToggle { color: #a9bbcb; background: transparent; border: none; font-size: 10px; font-weight: 600; padding: 2px 0; }
QToolButton#healthToggle:hover { color: #edf3fa; }
QToolButton#sectionToggle { color: #91a7b9; background: transparent; border: none; border-radius: 4px; font-size: 10px; font-weight: 600; padding: 3px 2px; text-align: left; }
QToolButton#sectionToggle:checked { color: #dce8f2; }
QToolButton#sectionToggle:hover { color: #edf3fa; background: #192633; }
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


class CollapsibleSection(QFrame):
    """A compact left-rail pane whose content can be expanded independently."""

    def __init__(self, title, content, expanded=False):
        super().__init__()
        self.setObjectName("railSection")
        self.content = content
        self.toggle = QToolButton()
        self.toggle.setObjectName("sectionToggle")
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setFixedHeight(24)
        self.toggle.setToolTip(f"Show or hide {title.title()} controls")
        self.toggle.toggled.connect(self.set_expanded)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(2)
        self.box.addWidget(self.toggle)
        self.box.addWidget(content)
        self.set_expanded(expanded)

    def set_expanded(self, expanded):
        self.content.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


class RailScrollArea(QScrollArea):
    """A control rail that keeps mouse-wheel navigation reliable over its children."""

    def enable_wheel_navigation(self):
        """Route wheel gestures from nested buttons/panes to the vertical bar."""
        if self.widget() is None:
            return
        self.viewport().installEventFilter(self)
        self.widget().installEventFilter(self)
        for child in self.widget().findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            scrollbar = self.verticalScrollBar()
            if scrollbar.maximum() > scrollbar.minimum():
                pixel_delta = event.pixelDelta().y()
                angle_delta = event.angleDelta().y()
                if pixel_delta:
                    distance = -pixel_delta
                elif angle_delta:
                    # One wheel notch moves far enough to reveal the next
                    # compact pane, rather than requiring many tiny turns.
                    distance = -round(angle_delta / 120) * max(48, scrollbar.singleStep() * 3)
                else:
                    return super().eventFilter(watched, event)
                previous = scrollbar.value()
                scrollbar.setValue(previous + distance)
                if scrollbar.value() != previous:
                    event.accept()
                    return True
        return super().eventFilter(watched, event)


class FocusOverlay(QWidget):
    """Move the same widget within the same top-level window; never clone plots."""

    def __init__(self, parent):
        super().__init__(parent)
        self.source = None
        self.controls = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName("focusCard")
        self.box = QVBoxLayout(self.card)
        self.box.setContentsMargins(16, 12, 16, 14)
        self.box.setSpacing(10)
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

    def expand(self, panel, controls=None):
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
        self.controls = controls
        self.box.addWidget(panel.content, 1)
        if controls is not None:
            self.box.addWidget(controls)
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
        if self.controls is not None:
            self.box.removeWidget(self.controls)
            self.controls.hide()
            self.controls.deleteLater()
            self.controls = None
        panel.box.removeWidget(self.placeholder)
        self.placeholder.deleteLater()
        panel.box.addWidget(panel.content, 1)
        panel.content.show()
        self.source = None
        self.hide()
        panel.expand_button.setFocus()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Leave a generous plot area while keeping the control strip visible.
        narrow = self.width() < 760
        self.box.setContentsMargins(10 if narrow else 16, 9, 10 if narrow else 16, 10 if narrow else 14)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(2, 5, 12, 205))

    def mousePressEvent(self, event):
        if not self.card.geometry().contains(event.position().toPoint()):
            self.restore()
