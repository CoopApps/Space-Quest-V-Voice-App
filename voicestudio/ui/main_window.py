"""
SQ5 Voice Studio — main window.

Layout:
  [Toolbar: game dir | deploy | mode]
  ┌──────────────┬────────────────────────┬─────────────────────────┐
  │ Character    │ Line list              │ Recording studio        │
  │ browser      │ (room / text / status) │ (text, waveform, mic)   │
  └──────────────┴────────────────────────┴─────────────────────────┘
  [Status bar]
"""

import csv
import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
from PyQt6.QtCore import (
    Qt, QThread, QTimer, pyqtSignal, QSize,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QSplitter, QStatusBar, QTableWidget, QTableWidgetItem,
    QTabWidget, QToolBar, QVBoxLayout, QWidget, QFrame,
)

from voicestudio.audio.recorder import Recorder, save_wav
from voicestudio.audio.sol import wav_to_patch
from voicestudio.audio.loader import (
    AudioLoadError, load_to_wav, SUPPORTED_SUFFIXES,
)
from voicestudio.packer.audiocache import AudioCache, base36_filename
from voicestudio.packer.injector import Injector
from voicestudio.packer.repackage import repackage

# ---------------------------------------------------------------------------
# Paths (defaults — overridable via Settings)
# ---------------------------------------------------------------------------
DEFAULT_DB       = Path("D:/projects/SQ5/voicestudio/sq5_lines.db")
DEFAULT_GAME_DIR = Path("D:/projects/sq5")
DEFAULT_CACHE    = Path("D:/projects/SQ5/voicestudio/audiocache")
DEFAULT_WAV_DIR  = Path("D:/projects/SQ5/voicestudio/recordings")

STATUS_COLORS = {
    "unrecorded": "#c0392b",
    "recorded":   "#27ae60",
    "deployed":   "#2980b9",
}


# ---------------------------------------------------------------------------
# Background recording thread
# ---------------------------------------------------------------------------

class RecordThread(QThread):
    level_signal  = pyqtSignal(float)
    stopped       = pyqtSignal(bytes)   # emits WAV bytes when done

    def __init__(self, device_id, parent=None):
        super().__init__(parent)
        self.device_id = device_id
        self._recorder = Recorder()

    def run(self):
        self._recorder.start(
            device_id=self.device_id,
            level_callback=lambda lvl: self.level_signal.emit(lvl),
        )

    def stop_recording(self):
        wav_bytes = self._recorder.stop()
        self.stopped.emit(wav_bytes)


# ---------------------------------------------------------------------------
# Waveform widget
# ---------------------------------------------------------------------------

class WaveformWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self._data: np.ndarray | None = None
        self._live_level: float = 0.0
        self.setStyleSheet("background:#1a1a2e;")

    def set_waveform(self, data: np.ndarray) -> None:
        self._data = data
        self._live_level = 0.0
        self.update()

    def set_live_level(self, level: float) -> None:
        self._live_level = level
        self.update()

    def clear(self) -> None:
        self._data = None
        self._live_level = 0.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1a1a2e"))

        w, h = self.width(), self.height()
        mid = h // 2

        if self._data is not None and len(self._data):
            pen = QPen(QColor("#00d4ff"), 1)
            p.setPen(pen)
            xs = np.linspace(0, w, len(self._data))
            for i in range(1, len(self._data)):
                x1 = int(xs[i - 1])
                x2 = int(xs[i])
                y1 = int(mid - self._data[i - 1] * mid * 0.9)
                y2 = int(mid - self._data[i] * mid * 0.9)
                p.drawLine(x1, y1, x2, y2)
        elif self._live_level > 0:
            # Live level bar while recording
            bar_h = int(self._live_level * h * 0.9)
            p.fillRect(0, mid - bar_h, w, bar_h * 2, QColor("#e74c3c"))
        else:
            # Empty state — dashed centre line
            pen = QPen(QColor("#444466"), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(0, mid, w, mid)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SQ5 Voice Studio")
        self.resize(1400, 800)

        # State
        self.db_path    = DEFAULT_DB
        self.game_dir   = DEFAULT_GAME_DIR
        self.cache_dir  = DEFAULT_CACHE
        self.wav_dir    = DEFAULT_WAV_DIR
        self.cache      = AudioCache(self.cache_dir)
        self.injector   = Injector(self.game_dir, self.cache, self.db_path)
        self.recorder   = None          # RecordThread
        self._current_line: dict | None = None
        self._pending_wav: bytes | None = None

        self._build_ui()
        self._load_characters()
        self._refresh_characters()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._build_toolbar()

        splitter = QSplitter(Qt.Orientation.Horizontal)

        splitter.addWidget(self._build_character_panel())
        splitter.addWidget(self._build_line_panel())
        splitter.addWidget(self._build_studio_panel())
        splitter.setSizes([220, 560, 420])

        self.setCentralWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        # Game dir
        tb.addWidget(QLabel(" Game dir: "))
        self.game_dir_label = QLabel(str(self.game_dir))
        self.game_dir_label.setStyleSheet("color:#aaa; margin-right:4px;")
        tb.addWidget(self.game_dir_label)
        btn_dir = QPushButton("Change...")
        btn_dir.clicked.connect(self._pick_game_dir)
        tb.addWidget(btn_dir)

        tb.addSeparator()

        # Deploy
        btn_deploy = QPushButton("Deploy to Game")
        btn_deploy.setStyleSheet("font-weight:bold; background:#2980b9; color:white; padding:4px 10px;")
        btn_deploy.clicked.connect(self._deploy_all)
        tb.addWidget(btn_deploy)

        btn_undeploy = QPushButton("Undeploy All")
        btn_undeploy.clicked.connect(self._undeploy_all)
        tb.addWidget(btn_undeploy)

        # Build a standalone RESOURCE.AUD + N.MAP set into a folder of your
        # choice — useful for packaging a release without touching the game
        # directory.
        btn_build_aud = QPushButton("Build RESOURCE.AUD...")
        btn_build_aud.setStyleSheet(
            "font-weight:bold; background:#27ae60; color:white; padding:4px 10px;"
        )
        btn_build_aud.setToolTip(
            "Compile every recorded line into a fresh AUDIO/RESOURCE.AUD + "
            "AUDIO/<module>.MAP set in a folder you pick.\n"
            "Drop the AUDIO/ folder into the game install (or distribute it)."
        )
        btn_build_aud.clicked.connect(self._build_resource_aud)
        tb.addWidget(btn_build_aud)

        tb.addSeparator()

        # Refresh from disk (rescans recordings/ for orphan WAVs not yet in DB)
        btn_refresh = QPushButton("Refresh from Disk")
        btn_refresh.setToolTip(
            "Scan the recordings folder and update the database for any "
            "WAVs that were dropped in without going through the app."
        )
        btn_refresh.clicked.connect(self._refresh_from_disk)
        tb.addWidget(btn_refresh)

        # Export
        btn_export = QPushButton("Export Progress CSV")
        btn_export.clicked.connect(self._export_csv)
        tb.addWidget(btn_export)

    def _build_character_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        # Tab between browsing by Character and by Room.
        self.browser_tabs = QTabWidget()

        char_w = QWidget()
        char_lay = QVBoxLayout(char_w)
        char_lay.setContentsMargins(0, 4, 0, 0)
        self.char_list = QListWidget()
        self.char_list.currentItemChanged.connect(self._on_character_selected)
        char_lay.addWidget(self.char_list)
        self.browser_tabs.addTab(char_w, "Characters")

        room_w = QWidget()
        room_lay = QVBoxLayout(room_w)
        room_lay.setContentsMargins(0, 4, 0, 0)
        self.room_list = QListWidget()
        self.room_list.currentItemChanged.connect(self._on_room_selected)
        room_lay.addWidget(self.room_list)
        self.browser_tabs.addTab(room_w, "Rooms")

        self.browser_tabs.currentChanged.connect(self._on_browser_tab_changed)
        lay.addWidget(self.browser_tabs)

        # Filter
        lay.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All lines", "Unrecorded", "Recorded"])
        self.filter_combo.currentIndexChanged.connect(self._refresh_lines)
        lay.addWidget(self.filter_combo)

        return w

    def _build_line_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        # Search bar — typing here searches every line across all
        # characters/rooms.  Clearing it returns to normal browsing.
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(
            "Type to search line text across the whole game..."
        )
        self.search_box.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_box, stretch=1)
        self.btn_clear_search = QPushButton("✗")
        self.btn_clear_search.setFixedWidth(28)
        self.btn_clear_search.setToolTip("Clear search")
        self.btn_clear_search.clicked.connect(lambda: self.search_box.setText(""))
        search_row.addWidget(self.btn_clear_search)
        lay.addLayout(search_row)

        self.line_table = QTableWidget(0, 3)
        self.line_table.setHorizontalHeaderLabels(["Room", "Text", "Status"])
        self.line_table.horizontalHeader().setStretchLastSection(False)
        self.line_table.setColumnWidth(0, 55)
        self.line_table.setColumnWidth(2, 90)
        self.line_table.horizontalHeader().setSectionResizeMode(
            1, self.line_table.horizontalHeader().ResizeMode.Stretch
        )
        self.line_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.line_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.line_table.verticalHeader().setDefaultSectionSize(22)
        self.line_table.itemSelectionChanged.connect(self._on_line_selected)
        # Ctrl+C on the table copies the selected row's line text.
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.line_table)
        copy_shortcut.activated.connect(self._copy_line_text)
        lay.addWidget(self.line_table)

        return w

    def _build_studio_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # Character / room context + quick "copy text" button
        ctx_row = QHBoxLayout()
        self.studio_context = QLabel("Select a line to record")
        self.studio_context.setStyleSheet("color:#888; font-size:11px;")
        ctx_row.addWidget(self.studio_context, stretch=1)
        self.btn_copy_text = QPushButton("📋 Copy")
        self.btn_copy_text.setToolTip("Copy this line's text to the clipboard")
        self.btn_copy_text.setFixedWidth(80)
        self.btn_copy_text.clicked.connect(self._copy_line_text)
        self.btn_copy_text.setEnabled(False)
        ctx_row.addWidget(self.btn_copy_text)
        lay.addLayout(ctx_row)

        # Line text — selectable so the user can copy it (e.g. into a TTS app).
        self.line_text = QLabel("")
        self.line_text.setWordWrap(True)
        self.line_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.line_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.line_text.setCursor(Qt.CursorShape.IBeamCursor)
        self.line_text.setStyleSheet(
            "font-size:16px; font-weight:bold; color:#eee; "
            "background:#1e1e3e; border-radius:6px; padding:10px; "
            "min-height:80px;"
        )
        lay.addWidget(self.line_text)

        # Waveform
        self.waveform = WaveformWidget()
        lay.addWidget(self.waveform)

        # Duration label
        self.duration_label = QLabel("")
        self.duration_label.setStyleSheet("color:#888; font-size:10px;")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.duration_label)

        # Mic selector
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Mic:"))
        self.mic_combo = QComboBox()
        self._populate_mic_combo()
        mic_row.addWidget(self.mic_combo, stretch=1)
        lay.addLayout(mic_row)

        # Record / playback buttons
        btn_row = QHBoxLayout()
        self.btn_record = QPushButton("● Record")
        self.btn_record.setStyleSheet(
            "font-weight:bold; background:#c0392b; color:white; padding:6px 16px;"
        )
        self.btn_record.clicked.connect(self._toggle_record)
        self.btn_record.setEnabled(False)
        btn_row.addWidget(self.btn_record)

        self.btn_load = QPushButton("📂 Load File…")
        self.btn_load.setToolTip(
            "Import a pre-recorded audio file (WAV, MP3, FLAC, OGG, M4A) "
            "and use it as the recording for this line."
        )
        self.btn_load.clicked.connect(self._load_audio_file)
        self.btn_load.setEnabled(False)
        btn_row.addWidget(self.btn_load)

        self.btn_play = QPushButton("▶ Play")
        self.btn_play.clicked.connect(self._play_pending)
        self.btn_play.setEnabled(False)
        btn_row.addWidget(self.btn_play)

        self.btn_accept = QPushButton("✓ Accept")
        self.btn_accept.setStyleSheet(
            "font-weight:bold; background:#27ae60; color:white; padding:6px 16px;"
        )
        self.btn_accept.clicked.connect(self._accept_recording)
        self.btn_accept.setEnabled(False)
        btn_row.addWidget(self.btn_accept)

        self.btn_discard = QPushButton("✗ Discard")
        self.btn_discard.clicked.connect(self._discard_recording)
        self.btn_discard.setEnabled(False)
        btn_row.addWidget(self.btn_discard)

        lay.addLayout(btn_row)

        # Remove deployed recording
        self.btn_remove = QPushButton("Remove Recording")
        self.btn_remove.setStyleSheet("color:#e74c3c;")
        self.btn_remove.clicked.connect(self._remove_recording)
        self.btn_remove.setEnabled(False)
        lay.addWidget(self.btn_remove)

        lay.addStretch()

        # Progress summary
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(sep)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color:#aaa; font-size:11px;")
        lay.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        lay.addWidget(self.progress_bar)

        return w

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_characters(self):
        chars_path = Path(__file__).parent.parent / "data" / "characters.json"
        self._characters: dict[str, str] = {}
        if chars_path.exists():
            with open(chars_path, encoding="utf-8") as f:
                self._characters = json.load(f)

    def _lookup_character_name(self, module: int, noun: int, verb: int,
                               cond: int, seq: int) -> str:
        try:
            con = sqlite3.connect(self.db_path)
            row = con.execute(
                "SELECT COALESCE(character_name,'Unknown-'||talker_id) "
                "FROM lines WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?",
                (module, noun, verb, cond, seq),
            ).fetchone()
            con.close()
            return row[0] if row else "?"
        except Exception:
            return "?"

    def _deployed_patch_set(self) -> set[str]:
        """Filenames currently present in the game dir as audio36 patches."""
        try:
            return {p.name for p in self.injector.deployed_patches()}
        except Exception:
            return set()

    def _line_deployed(self, deployed_set: set[str],
                       module: int, noun: int, verb: int,
                       cond: int, seq: int) -> bool:
        return base36_filename(module, noun, verb, cond, seq) in deployed_set

    def _refresh_characters(self):
        self.char_list.clear()
        deployed_set = self._deployed_patch_set()
        con = sqlite3.connect(self.db_path)

        # Per-character counts (recorded comes from the DB; deployed is
        # computed below by joining against the filesystem set).
        rows = con.execute("""
            SELECT talker_id,
                   COALESCE(character_name, 'Unknown-' || talker_id),
                   COUNT(*) as total,
                   SUM(recorded) as done
            FROM lines
            WHERE talker_id NOT IN (97, 98)
            GROUP BY talker_id
            ORDER BY COUNT(*) DESC
        """).fetchall()

        # For each character, compute how many of its recorded lines are
        # actually deployed on disk.
        deployed_per_char: dict[int, int] = {}
        if deployed_set:
            for talker_id, _name, _tot, _done in rows:
                d = con.execute(
                    "SELECT module,noun,verb,cond,seq FROM lines "
                    "WHERE talker_id=? AND recorded=1",
                    (talker_id,),
                ).fetchall()
                deployed_per_char[talker_id] = sum(
                    1 for m, n, v, c, s in d
                    if self._line_deployed(deployed_set, m, n, v, c, s)
                )
        con.close()

        total_lines = total_done = total_deployed = 0
        for talker_id, name, total, done in rows:
            done = done or 0
            dep = deployed_per_char.get(talker_id, 0)
            label = f"{name}  ({done}/{total}"
            if dep != done:
                label += f", {dep} deployed"
            label += ")"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, talker_id)
            pct = done / total if total else 0
            if pct == 1.0:
                item.setForeground(QColor(STATUS_COLORS["deployed"]))
            elif pct > 0:
                item.setForeground(QColor("#f39c12"))
            else:
                item.setForeground(QColor(STATUS_COLORS["unrecorded"]))
            self.char_list.addItem(item)
            total_lines    += total
            total_done     += done
            total_deployed += dep

        # Also refresh the Rooms tab so both stay in sync.
        self._refresh_rooms(deployed_set=deployed_set)

        self.progress_bar.setMaximum(total_lines)
        self.progress_bar.setValue(total_done)
        pct = 100 * total_done // total_lines if total_lines else 0
        self.progress_label.setText(
            f"{total_done} recorded  ·  {total_deployed} deployed  ·  "
            f"{total_lines} total  ({pct}%)"
        )

    def _refresh_rooms(self, deployed_set: set[str] | None = None):
        """Populate the Rooms tab — one entry per module with done/total."""
        if deployed_set is None:
            deployed_set = self._deployed_patch_set()
        self.room_list.clear()
        con = sqlite3.connect(self.db_path)
        rows = con.execute("""
            SELECT module,
                   COUNT(*) as total,
                   SUM(recorded) as done
            FROM lines
            WHERE talker_id NOT IN (97, 98)
            GROUP BY module
            ORDER BY module
        """).fetchall()

        for module, total, done in rows:
            done = done or 0
            dep = 0
            if deployed_set:
                drows = con.execute(
                    "SELECT noun,verb,cond,seq FROM lines "
                    "WHERE module=? AND recorded=1 AND talker_id NOT IN (97,98)",
                    (module,),
                ).fetchall()
                dep = sum(
                    1 for n, v, c, s in drows
                    if self._line_deployed(deployed_set, module, n, v, c, s)
                )
            label = f"Room {module}  ({done}/{total}"
            if dep != done:
                label += f", {dep} deployed"
            label += ")"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, module)
            pct = done / total if total else 0
            if pct == 1.0:
                item.setForeground(QColor(STATUS_COLORS["deployed"]))
            elif pct > 0:
                item.setForeground(QColor("#f39c12"))
            else:
                item.setForeground(QColor(STATUS_COLORS["unrecorded"]))
            self.room_list.addItem(item)
        con.close()

    def _refresh_lines(self):
        # Search takes precedence over normal browsing — if the user has
        # typed anything, show search results regardless of which tab is
        # active or what's selected.
        query = self.search_box.text().strip()
        if query:
            self._load_lines_for_search(query)
            return

        # Dispatch to the right loader based on which browser tab is active.
        if self.browser_tabs.currentIndex() == 0:
            item = self.char_list.currentItem()
            if item is None:
                return
            self._load_lines_for_talker(item.data(Qt.ItemDataRole.UserRole))
        else:
            item = self.room_list.currentItem()
            if item is None:
                return
            self._load_lines_for_module(item.data(Qt.ItemDataRole.UserRole))

    def _on_search_changed(self, _text: str):
        self._refresh_lines()

    def _load_lines_for_search(self, query: str):
        """Free-text search across every line in the DB."""
        filter_idx = self.filter_combo.currentIndex()
        where = ("WHERE text LIKE ? AND talker_id NOT IN (97,98) "
                 "COLLATE NOCASE")
        params: list = [f"%{query}%"]
        if filter_idx == 1:
            where += " AND recorded=0"
        elif filter_idx == 2:
            where += " AND recorded=1"

        # When searching, the first column shows the room number so the
        # user can see where each hit came from.
        self.line_table.setHorizontalHeaderLabels(["Room", "Text", "Status"])

        con = sqlite3.connect(self.db_path)
        rows = con.execute(
            f"SELECT module,noun,verb,cond,seq,text,recorded,wav_path "
            f"FROM lines {where} ORDER BY module,noun,verb,cond,seq LIMIT 500",
            params,
        ).fetchall()
        con.close()

        self.line_table.setRowCount(0)
        self._line_data = []
        for r in rows:
            module, noun, verb, cond, seq, text, recorded, wav_path = r
            row_idx = self.line_table.rowCount()
            self.line_table.insertRow(row_idx)
            self.line_table.setItem(row_idx, 0, QTableWidgetItem(str(module)))
            self.line_table.setItem(row_idx, 1, QTableWidgetItem(text))
            status = "Recorded" if recorded else "Unrecorded"
            status_item = QTableWidgetItem(status)
            color = STATUS_COLORS["recorded"] if recorded else STATUS_COLORS["unrecorded"]
            status_item.setForeground(QColor(color))
            self.line_table.setItem(row_idx, 2, status_item)
            self._line_data.append({
                "module": module, "noun": noun, "verb": verb,
                "cond": cond, "seq": seq, "text": text,
                "recorded": recorded, "wav_path": wav_path,
            })
        if len(rows) == 500:
            self.status_bar.showMessage(
                f"Search: showing first 500 matches for '{query}' — refine to see more"
            )
        else:
            self.status_bar.showMessage(
                f"Search: {len(rows)} match(es) for '{query}'"
            )

    def _load_lines_for_talker(self, talker_id: int):
        filter_idx = self.filter_combo.currentIndex()
        where = "WHERE talker_id=?"
        params = [talker_id]
        if filter_idx == 1:
            where += " AND recorded=0"
        elif filter_idx == 2:
            where += " AND recorded=1"

        con = sqlite3.connect(self.db_path)
        rows = con.execute(
            f"SELECT module,noun,verb,cond,seq,text,recorded,wav_path "
            f"FROM lines {where} ORDER BY module,noun,verb,cond,seq",
            params,
        ).fetchall()
        con.close()

        self.line_table.setRowCount(0)
        self._line_data: list[dict] = []

        for r in rows:
            module, noun, verb, cond, seq, text, recorded, wav_path = r
            row_idx = self.line_table.rowCount()
            self.line_table.insertRow(row_idx)

            self.line_table.setItem(row_idx, 0, QTableWidgetItem(str(module)))
            text_item = QTableWidgetItem(text)
            self.line_table.setItem(row_idx, 1, text_item)

            status = "Recorded" if recorded else "Unrecorded"
            status_item = QTableWidgetItem(status)
            color = STATUS_COLORS["recorded"] if recorded else STATUS_COLORS["unrecorded"]
            status_item.setForeground(QColor(color))
            self.line_table.setItem(row_idx, 2, status_item)

            self._line_data.append({
                "module": module, "noun": noun, "verb": verb,
                "cond": cond, "seq": seq, "text": text,
                "recorded": recorded, "wav_path": wav_path,
            })

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_browser_tab_changed(self, index: int):
        # Update the line table's first column header to match the active
        # browser, then re-load whatever is selected in that tab.
        if index == 0:
            self.line_table.setHorizontalHeaderLabels(["Room", "Text", "Status"])
        else:
            self.line_table.setHorizontalHeaderLabels(["Character", "Text", "Status"])
        self._refresh_lines()

    def _on_character_selected(self, current, previous):
        if current is None:
            return
        talker_id = current.data(Qt.ItemDataRole.UserRole)
        self._load_lines_for_talker(talker_id)

    def _on_line_selected(self):
        rows = self.line_table.selectedItems()
        if not rows:
            return
        row_idx = self.line_table.row(rows[0])
        if row_idx >= len(self._line_data):
            return

        line = self._line_data[row_idx]
        self._current_line = line
        self._pending_wav  = None

        # Update studio panel.  Character name comes from the sidebar if
        # we're browsing by character, or from line metadata (looked up
        # from the DB) when we're browsing by room.
        if self.browser_tabs.currentIndex() == 0:
            char_item = self.char_list.currentItem()
            char_name = char_item.text().split("  (")[0] if char_item else "?"
        else:
            char_name = self._lookup_character_name(
                line["module"], line["noun"], line["verb"],
                line["cond"], line["seq"],
            )
        self.studio_context.setText(
            f"{char_name}  ·  Room {line['module']}  ·  "
            f"noun={line['noun']} verb={line['verb']} cond={line['cond']} seq={line['seq']}"
        )
        self.line_text.setText(line["text"])
        self.waveform.clear()
        self.duration_label.setText("")

        # Load existing recording waveform if any
        if line["recorded"] and line.get("wav_path"):
            wav = Path(line["wav_path"])
            if wav.exists():
                self.waveform.set_waveform(Recorder.get_waveform(wav))
                dur = Recorder.get_duration(wav)
                self.duration_label.setText(f"{dur:.1f}s")

        self.btn_record.setEnabled(True)
        self.btn_load.setEnabled(True)
        self.btn_copy_text.setEnabled(True)
        self.btn_play.setEnabled(bool(line["recorded"] and line.get("wav_path")))
        self.btn_accept.setEnabled(False)
        self.btn_discard.setEnabled(False)
        self.btn_remove.setEnabled(bool(line["recorded"]))

    def _pick_game_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select SQ5 Game Directory",
                                                str(self.game_dir))
        if path:
            self.game_dir = Path(path)
            self.game_dir_label.setText(path)
            self.injector = Injector(self.game_dir, self.cache, self.db_path)

    # ------------------------------------------------------------------
    # Recording controls
    # ------------------------------------------------------------------

    def _mic_device_id(self) -> int | None:
        idx = self.mic_combo.currentIndex()
        data = self.mic_combo.itemData(idx)
        return data if data is not None else None

    def _toggle_record(self):
        if self.recorder and self.recorder.isRunning():
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self._current_line is None:
            return
        self._pending_wav = None
        self.waveform.clear()
        self.duration_label.setText("Recording...")
        self.btn_record.setText("■ Stop")
        self.btn_record.setStyleSheet(
            "font-weight:bold; background:#e67e22; color:white; padding:6px 16px;"
        )
        self.btn_play.setEnabled(False)
        self.btn_accept.setEnabled(False)
        self.btn_discard.setEnabled(False)

        self.recorder = RecordThread(self._mic_device_id(), self)
        self.recorder.level_signal.connect(self.waveform.set_live_level)
        self.recorder.stopped.connect(self._on_recording_done)
        self.recorder.start()

    def _stop_recording(self):
        if self.recorder:
            self.recorder.stop_recording()
        self.btn_record.setText("● Record")
        self.btn_record.setStyleSheet(
            "font-weight:bold; background:#c0392b; color:white; padding:6px 16px;"
        )

    def _on_recording_done(self, wav_bytes: bytes):
        if not wav_bytes:
            self.status_bar.showMessage("Recording failed (no audio captured)")
            return
        self._pending_wav = wav_bytes
        self.duration_label.setText("Processing...")

        # Save to temp file to generate waveform
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = Path(tmp.name)

        try:
            wf = Recorder.get_waveform(tmp_path)
            dur = Recorder.get_duration(tmp_path)
            self.waveform.set_waveform(wf)
            self.duration_label.setText(f"{dur:.1f}s")
        finally:
            tmp_path.unlink(missing_ok=True)

        self.btn_play.setEnabled(True)
        self.btn_accept.setEnabled(True)
        self.btn_discard.setEnabled(True)
        self.status_bar.showMessage("Recording captured — review and Accept or Discard")

    def _play_pending(self):
        # Playback uses the system default output (None) — passing the mic
        # device id here crashes sounddevice because mic devices have no
        # output channels.
        try:
            if self._pending_wav:
                Recorder.play_wav_bytes(self._pending_wav, device_id=None)
            elif self._current_line and self._current_line.get("wav_path"):
                wav = Path(self._current_line["wav_path"])
                if wav.exists():
                    Recorder.play_wav(wav)
        except Exception as e:
            QMessageBox.warning(self, "Playback Error", str(e))

    def _accept_recording(self):
        if self._pending_wav is None or self._current_line is None:
            return
        line = self._current_line
        wav_dir = self.wav_dir / str(line["module"])
        wav_dir.mkdir(parents=True, exist_ok=True)
        wav_filename = (
            f"n{line['noun']}_v{line['verb']}_c{line['cond']}_s{line['seq']}.wav"
        )
        wav_path = wav_dir / wav_filename
        save_wav(self._pending_wav, wav_path)

        # Inject into audiocache + game dir
        try:
            self.injector.inject_line(
                line["module"], line["noun"], line["verb"],
                line["cond"], line["seq"], wav_path,
            )
        except Exception as e:
            QMessageBox.warning(self, "Injection Error", str(e))
            return

        self._pending_wav = None
        self.btn_accept.setEnabled(False)
        self.btn_discard.setEnabled(False)
        self.btn_remove.setEnabled(True)
        self.btn_play.setEnabled(True)

        # Refresh line in table
        rows = self.line_table.selectedItems()
        if rows:
            row_idx = self.line_table.row(rows[0])
            self.line_table.item(row_idx, 2).setText("Recorded")
            self.line_table.item(row_idx, 2).setForeground(
                QColor(STATUS_COLORS["recorded"])
            )
            self._line_data[row_idx]["recorded"] = 1
            self._line_data[row_idx]["wav_path"] = str(wav_path)

        self._refresh_characters()
        self.status_bar.showMessage(f"Saved and deployed: {wav_path.name}")

    def _discard_recording(self):
        self._pending_wav = None
        self.waveform.clear()
        self.duration_label.setText("")
        self.btn_play.setEnabled(False)
        self.btn_accept.setEnabled(False)
        self.btn_discard.setEnabled(False)
        self.status_bar.showMessage("Recording discarded")

    def _remove_recording(self):
        if self._current_line is None:
            return
        line = self._current_line
        reply = QMessageBox.question(
            self, "Remove Recording",
            f"Remove the recording for this line?\n\n{line['text'][:80]}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.injector.remove_line(
            line["module"], line["noun"], line["verb"],
            line["cond"], line["seq"],
        )
        self.waveform.clear()
        self.duration_label.setText("")
        self.btn_remove.setEnabled(False)
        self.btn_play.setEnabled(False)

        rows = self.line_table.selectedItems()
        if rows:
            row_idx = self.line_table.row(rows[0])
            self.line_table.item(row_idx, 2).setText("Unrecorded")
            self.line_table.item(row_idx, 2).setForeground(
                QColor(STATUS_COLORS["unrecorded"])
            )
            self._line_data[row_idx]["recorded"] = 0
            self._line_data[row_idx]["wav_path"] = None

        self._refresh_characters()
        self.status_bar.showMessage("Recording removed")

    # ------------------------------------------------------------------
    # Deploy / undeploy
    # ------------------------------------------------------------------

    def _deploy_all(self):
        deployed, _ = self.injector.deploy_all()
        summary = repackage(self.game_dir, self.cache)
        self._refresh_characters()
        self.status_bar.showMessage(
            f"Deployed {deployed} patch files + "
            f"AUDIO/RESOURCE.AUD ({summary['clips_written']} clips, "
            f"{summary['modules_written']} MAP files) to {self.game_dir}"
        )

    def _build_resource_aud(self):
        """Compile a RESOURCE.AUD set into a user-chosen output folder."""
        target = QFileDialog.getExistingDirectory(
            self,
            "Pick a folder to build RESOURCE.AUD into",
            str(self.game_dir),
        )
        if not target:
            return
        target_path = Path(target)
        try:
            summary = repackage(target_path, self.cache)
        except Exception as e:
            QMessageBox.critical(self, "Build Failed", str(e))
            return

        aud_path = target_path / "AUDIO" / "RESOURCE.AUD"
        size_mb  = aud_path.stat().st_size / (1024 * 1024) if aud_path.exists() else 0
        QMessageBox.information(
            self,
            "Build Complete",
            f"Wrote {aud_path}\n"
            f"  {summary['clips_written']} audio clips\n"
            f"  {summary['modules_written']} module map files\n"
            f"  {size_mb:.1f} MB\n\n"
            f"Drop the AUDIO/ folder next to RESOURCE.000 in the game "
            f"install, or distribute it as a release.",
        )
        self.status_bar.showMessage(
            f"Built RESOURCE.AUD at {aud_path} ({size_mb:.1f} MB)"
        )

    def _undeploy_all(self):
        reply = QMessageBox.question(
            self, "Undeploy All",
            "Remove all voice patch files from the game directory?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = self.injector.undeploy_all()
        self._refresh_characters()
        self.status_bar.showMessage(f"Removed {removed} patch files from {self.game_dir}")

    # ------------------------------------------------------------------
    # Room browsing
    # ------------------------------------------------------------------

    def _on_room_selected(self, current, previous):
        if current is None:
            return
        module = current.data(Qt.ItemDataRole.UserRole)
        self._load_lines_for_module(module)

    def _load_lines_for_module(self, module: int):
        """Mirror of _load_lines_for_talker but scoped to a single room."""
        filter_idx = self.filter_combo.currentIndex()
        where = "WHERE module=? AND talker_id NOT IN (97,98)"
        params: list = [module]
        if filter_idx == 1:
            where += " AND recorded=0"
        elif filter_idx == 2:
            where += " AND recorded=1"

        con = sqlite3.connect(self.db_path)
        rows = con.execute(
            f"SELECT module,noun,verb,cond,seq,text,recorded,wav_path,"
            f"COALESCE(character_name,'Unknown-'||talker_id) "
            f"FROM lines {where} ORDER BY noun,verb,cond,seq",
            params,
        ).fetchall()
        con.close()

        self.line_table.setRowCount(0)
        self._line_data = []
        for r in rows:
            module, noun, verb, cond, seq, text, recorded, wav_path, char = r
            row_idx = self.line_table.rowCount()
            self.line_table.insertRow(row_idx)
            # When browsing by room, the "Room" column is uniform — show
            # the character name there instead to keep info density high.
            self.line_table.setItem(row_idx, 0, QTableWidgetItem(char))
            self.line_table.setItem(row_idx, 1, QTableWidgetItem(text))
            status = "Recorded" if recorded else "Unrecorded"
            status_item = QTableWidgetItem(status)
            color = STATUS_COLORS["recorded"] if recorded else STATUS_COLORS["unrecorded"]
            status_item.setForeground(QColor(color))
            self.line_table.setItem(row_idx, 2, status_item)
            self._line_data.append({
                "module": module, "noun": noun, "verb": verb,
                "cond": cond, "seq": seq, "text": text,
                "recorded": recorded, "wav_path": wav_path,
            })

    # ------------------------------------------------------------------
    # Disk rescan
    # ------------------------------------------------------------------

    def _refresh_from_disk(self):
        """
        Walk recordings/<module>/n<noun>_v<verb>_c<cond>_s<seq>.wav and
        update the DB for any orphan WAVs (files that exist but the DB
        doesn't know about).  Also clears `recorded` for DB rows whose
        WAV no longer exists.
        """
        if not self.wav_dir.exists():
            QMessageBox.information(
                self, "Refresh from Disk",
                f"Recordings folder does not exist:\n{self.wav_dir}",
            )
            return

        adopted = stale = 0
        con = sqlite3.connect(self.db_path)
        try:
            # 1) Adopt orphan WAVs.
            for mod_dir in self.wav_dir.iterdir():
                if not mod_dir.is_dir():
                    continue
                try:
                    module = int(mod_dir.name)
                except ValueError:
                    continue
                for wav in mod_dir.glob("n*_v*_c*_s*.wav"):
                    try:
                        parts = wav.stem.split("_")
                        noun = int(parts[0][1:])
                        verb = int(parts[1][1:])
                        cond = int(parts[2][1:])
                        seq  = int(parts[3][1:])
                    except (ValueError, IndexError):
                        continue
                    row = con.execute(
                        "SELECT recorded, wav_path FROM lines "
                        "WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?",
                        (module, noun, verb, cond, seq),
                    ).fetchone()
                    if row is None:
                        continue
                    recorded, existing = row
                    if not recorded or not existing or not Path(existing).exists():
                        con.execute(
                            "UPDATE lines SET recorded=1, wav_path=? "
                            "WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?",
                            (str(wav), module, noun, verb, cond, seq),
                        )
                        adopted += 1

            # 2) Clear `recorded` for rows whose WAV is gone.
            rows = con.execute(
                "SELECT module,noun,verb,cond,seq,wav_path FROM lines "
                "WHERE recorded=1"
            ).fetchall()
            for m, n, v, c, s, wp in rows:
                if not wp or not Path(wp).exists():
                    con.execute(
                        "UPDATE lines SET recorded=0, wav_path=NULL "
                        "WHERE module=? AND noun=? AND verb=? AND cond=? AND seq=?",
                        (m, n, v, c, s),
                    )
                    stale += 1
            con.commit()
        finally:
            con.close()

        self._refresh_characters()
        self._refresh_lines()
        self.status_bar.showMessage(
            f"Disk rescan: adopted {adopted} new WAV(s), cleared {stale} stale entry(ies)."
        )

    # ------------------------------------------------------------------
    # Load audio file (import from outside the app)
    # ------------------------------------------------------------------

    def _copy_line_text(self):
        if self._current_line is None:
            return
        QApplication.clipboard().setText(self._current_line["text"])
        self.status_bar.showMessage("Line text copied to clipboard")

    def _load_audio_file(self):
        if self._current_line is None:
            return
        line = self._current_line

        exts = " ".join(f"*{s}" for s in sorted(SUPPORTED_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(
            self, "Import audio for this line",
            "", f"Audio Files ({exts})",
        )
        if not path:
            return

        try:
            wav_path, is_temp = load_to_wav(Path(path))
        except AudioLoadError as e:
            QMessageBox.warning(self, "Import Error", str(e))
            return

        try:
            # Read into bytes so we can drive the existing preview/accept
            # flow exactly the same way a fresh recording does.
            self._pending_wav = wav_path.read_bytes()
            self.waveform.set_waveform(Recorder.get_waveform(wav_path))
            try:
                dur = Recorder.get_duration(wav_path)
                self.duration_label.setText(f"{dur:.1f}s")
            except Exception:
                self.duration_label.setText("")
        finally:
            if is_temp:
                try:
                    wav_path.unlink()
                except OSError:
                    pass

        self.btn_play.setEnabled(True)
        self.btn_accept.setEnabled(True)
        self.btn_discard.setEnabled(True)
        self.status_bar.showMessage(
            f"Loaded {Path(path).name} — review and Accept or Discard"
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Progress", "sq5_progress.csv", "CSV (*.csv)"
        )
        if not path:
            return
        con = sqlite3.connect(self.db_path)
        rows = con.execute("""
            SELECT module, noun, verb, cond, seq,
                   COALESCE(character_name,'Unknown-'||talker_id),
                   text, recorded
            FROM lines
            WHERE talker_id NOT IN (97,98,99)
            ORDER BY talker_id, module, noun, verb, cond, seq
        """).fetchall()
        con.close()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["module","noun","verb","cond","seq",
                         "character","text","recorded"])
            w.writerows(rows)
        self.status_bar.showMessage(f"Exported {len(rows)} lines to {path}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _populate_mic_combo(self):
        self.mic_combo.clear()
        default_id = Recorder.default_input_device()
        for d in Recorder.list_input_devices():
            label = d["name"]
            if d["id"] == default_id:
                label += "  (default)"
            self.mic_combo.addItem(label, userData=d["id"])
