"""Local session picker for ZIP bundles and legacy CSV/JSON recordings."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from session_io import load_session


class SessionImportDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.session = None
        self.setWindowTitle('Open session for replay')
        self.setAcceptDrops(True)
        self.resize(500, 300)
        self.setStyleSheet(parent.styleSheet() + '''
            QDialog { background: #151c28; }
            QFrame#sessionDrop { background: #12202c; border: 1px dashed #55b7cd; border-radius: 8px; }
            QLabel#sessionError { color: #f3a2a8; }
        ''')
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 20, 20, 20)
        box.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel('Open session for replay')
        title.setObjectName('panelTitle')
        header.addWidget(title, 1)
        close = QPushButton('Close ×')
        close.clicked.connect(self.reject)
        header.addWidget(close)
        box.addLayout(header)
        note = QLabel('Drop your session ZIP here, or choose the matching CSV and JSON together. Files are read locally on your computer.')
        note.setObjectName('muted')
        note.setWordWrap(True)
        box.addWidget(note)
        drop = QFrame()
        drop.setObjectName('sessionDrop')
        drop_box = QVBoxLayout(drop)
        drop_box.setContentsMargins(20, 24, 20, 24)
        drop_box.setSpacing(12)
        for text in ('Drop session files here', 'One ZIP, or a matching CSV + JSON pair'):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            drop_box.addWidget(label)
        choose = QPushButton('Choose files…')
        choose.setObjectName('primary')
        choose.clicked.connect(self.choose_files)
        drop_box.addWidget(choose, alignment=Qt.AlignmentFlag.AlignCenter)
        box.addWidget(drop, 1)
        self.error = QLabel()
        self.error.setObjectName('sessionError')
        self.error.setWordWrap(True)
        self.error.hide()
        box.addWidget(self.error)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Choose session files', '', 'Telemetry sessions (*.zip *.csv *.json)')
        if paths:
            self.open_files(paths)

    def open_files(self, paths):
        try:
            files = [Path(path) for path in paths]
            if len(files) == 1:
                if files[0].suffix.lower() not in ('.zip', '.csv', '.json'):
                    raise ValueError('Choose a ZIP, CSV or JSON session file.')
                self.session = load_session(files[0])
            elif len(files) == 2:
                csv = [path for path in files if path.suffix.lower() == '.csv']
                metadata = [path for path in files if path.suffix.lower() == '.json']
                if len(csv) != 1 or len(metadata) != 1:
                    raise ValueError('Choose one ZIP, or one CSV and its matching JSON.')
                self.session = load_session(csv[0], metadata[0])
            else:
                raise ValueError('Choose one ZIP, or one CSV and its matching JSON.')
        except (OSError, ValueError, UnicodeError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                event.acceptProposedAction()
                self.open_files(paths)
