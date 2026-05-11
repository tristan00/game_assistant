from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QKeySequenceEdit,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

_MODELS = [
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
]


class SettingsDialog(QDialog):
    def __init__(self, current: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.model_combo = QComboBox()
        self.model_combo.addItems(_MODELS)
        if current["model"] in _MODELS:
            self.model_combo.setCurrentText(current["model"])
        form.addRow("Model:", self.model_combo)

        self.interval_spinbox = QSpinBox()
        self.interval_spinbox.setRange(5, 3600)
        self.interval_spinbox.setSuffix(" s")
        self.interval_spinbox.setValue(current["interval_seconds"])
        form.addRow("Capture interval:", self.interval_spinbox)

        self.last_n_spinbox = QSpinBox()
        self.last_n_spinbox.setRange(1, 50)
        self.last_n_spinbox.setValue(current["last_n"])
        form.addRow("Last N images sent:", self.last_n_spinbox)

        self.hotkey_edit = QKeySequenceEdit(QKeySequence(current["hotkey_qt"]))
        self.hotkey_edit.setMaximumSequenceLength(1)
        form.addRow("Global capture hotkey:", self.hotkey_edit)

        layout.addLayout(form)

        hint = QLabel("All settings apply immediately on Save and persist across launches.")
        hint.setStyleSheet("color: gray; padding-top: 6px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "model": self.model_combo.currentText(),
            "interval_seconds": self.interval_spinbox.value(),
            "last_n": self.last_n_spinbox.value(),
            "hotkey_qt": self.hotkey_edit.keySequence().toString(),
        }
