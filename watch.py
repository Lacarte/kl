"""
Log Viewer
==========
Interactive console UI to browse keylogger logs by date.

Controls:
  Up/Down or K/J  - navigate dates / scroll log
  Enter           - open selected date
  Backspace / Esc - back to date list
  X               - stop the running keylogger
  Q               - quit
"""

import ctypes
import ctypes.wintypes
import msvcrt
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR  = Path(__file__).parent
LOGS_DIR  = BASE_DIR / "logs"
PID_FILE  = BASE_DIR / "keylogger.pid"

# ── ANSI colours ──────────────────────────────────────────────────────────────

RST   = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
WHITE = "\033[97m"
GRAY  = "\033[90m"
RED   = "\033[31m"
BG_CYAN  = "\033[46m"
BG_GRAY  = "\033[100m"
BLACK = "\033[30m"


def is_keylogger_running():
    """Check if the keylogger process is alive via its PID file."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except (ValueError, OSError):
        return False


MAIN_SCRIPT = str(BASE_DIR / "main.py")


def _ps_query():
    """Single PowerShell call to get all python.exe process info as CSV."""
    import subprocess
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NoLogo", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
         "| Select-Object ProcessId,ExecutablePath,CommandLine "
         "| ConvertTo-Csv -NoTypeInformation"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    import csv, io
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
        # Match: full path to main.py in command, OR venv python + command ends with main.py
        if main_lower in cmd_lower or (base_lower in exe.lower() and cmd_lower.endswith("main.py")):
            found.append((pid, exe or "unknown", cmd or "unknown"))
    return found


def get_keylogger_status():
    """Return a status message with PID, process name, and executable path."""
    try:
        procs = _ps_query()
    except Exception:
        procs = []
    if not procs:
        return f"{RED}Not running{RST}"
    lines = [f"{GREEN}{BOLD}Yes — running ({len(procs)} process{'es' if len(procs) > 1 else ''}){RST}"]
    for pid, exe, cmd in procs:
        lines.append(f"  PID:     {WHITE}{pid}{RST}")
        lines.append(f"  Exe:     {WHITE}{exe}{RST}")
        lines.append(f"  Command: {WHITE}{cmd}{RST}")
        if len(procs) > 1:
            lines.append("")
    return "\n".join(lines)


def stop_keylogger():
    """Find and kill all keylogger processes in a single PowerShell call."""
    import subprocess
    main_escaped = MAIN_SCRIPT.replace("'", "''").lower()
    base_escaped = str(BASE_DIR).replace("'", "''").lower()
    my_pid = os.getpid()
    # Match: full main.py path in CommandLine, OR venv python + command ends with main.py
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
    return f"Stopped {len(pids)} process{'es' if len(pids) > 1 else ''}: PID {', '.join(pids)}"


def enable_virtual_terminal():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.wintypes.DWORD()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    kernel32.SetConsoleMode(handle, mode.value | 0x0004)


def term_size():
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except OSError:
        return 80, 24


def clear():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def read_key():
    """Read a keypress. Returns special keys as strings like 'up', 'down', etc."""
    ch = msvcrt.getwch()
    if ch == "\x00" or ch == "\xe0":
        ext = msvcrt.getwch()
        mapping = {"H": "up", "P": "down", "K": "left", "M": "right",
                   "G": "home", "O": "end", "I": "pgup", "Q": "pgdn"}
        return mapping.get(ext, "")
    if ch == "\r":
        return "enter"
    if ch == "\x08":
        return "backspace"
    if ch == "\x1b":
        return "esc"
    return ch


# ── Discover log files ───────────────────────────────────────────────────────

def get_log_dates():
    """Return list of (date_str, file_path, line_count) sorted newest first."""
    if not LOGS_DIR.exists():
        return []
    dates = []
    for f in sorted(LOGS_DIR.glob("*.log"), reverse=True):
        name = f.stem  # e.g. "2026-02-19"
        try:
            datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        try:
            count = sum(1 for _ in open(f, encoding="utf-8", errors="replace"))
        except Exception:
            count = 0
        dates.append((name, f, count))
    return dates


def read_log_file(path):
    """Read all lines from a log file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f]
    except Exception as e:
        return [f"[Error reading log: {e}]"]


# ── Date list screen ─────────────────────────────────────────────────────────

def render_date_list(dates, cursor):
    cols, rows = term_size()
    out = ["\033[H"]

    # header
    title = " LOG VIEWER "
    pad_l = (cols - len(title)) // 2
    pad_r = cols - pad_l - len(title)
    out.append(f"{CYAN}{BOLD}{'─' * pad_l}{title}{'─' * pad_r}{RST}")
    out.append("")
    # keylogger status
    if is_keylogger_running():
        status = f"{GREEN}{BOLD}● RUNNING{RST}"
    else:
        status = f"{GRAY}○ STOPPED{RST}"

    out.append(f"  {YELLOW}{BOLD}Select a date to view logs:{RST}    Keylogger: {status}")
    out.append(f"  {DIM}Up/Down = navigate │ Enter = open │ R = status │ X = stop │ Q = quit{RST}")
    out.append("")

    if not dates:
        out.append(f"  {DIM}No log files found in {LOGS_DIR}{RST}")
        out.append(f"  {DIM}Run the keylogger first to generate logs.{RST}")
    else:
        # visible window
        header_used = len(out) + 3
        visible = max(1, rows - header_used)
        # keep cursor in view
        start = max(0, cursor - visible + 1)
        end = min(len(dates), start + visible)

        for i in range(start, end):
            date_str, _, count = dates[i]
            # format nicely
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                label = dt.strftime("%A, %B %d, %Y")
            except ValueError:
                label = date_str

            entries_label = f"{count} entries"

            if i == cursor:
                out.append(
                    f"  {BG_CYAN}{BLACK}{BOLD}  ▶ {label}  "
                    f"({entries_label})  {RST}\033[K"
                )
            else:
                out.append(
                    f"    {WHITE}○ {label}  "
                    f"{GRAY}({entries_label}){RST}\033[K"
                )

    # fill remaining
    current = len(out) + 2
    for _ in range(max(0, rows - current)):
        out.append("\033[K")

    out.append("")
    out.append(f"  {DIM}Logs: {LOGS_DIR}{RST}\033[K")

    sys.stdout.write("\n".join(out))
    sys.stdout.flush()


def show_toast(message, colour=YELLOW):
    """Flash a one-line message at the bottom of the screen for 1.5s."""
    cols, rows = term_size()
    sys.stdout.write(f"\033[{rows};1H  {colour}{BOLD}{message}{RST}\033[K")
    sys.stdout.flush()
    import time
    time.sleep(1.5)


def show_status_popup(status_text):
    """Show a multi-line status box and wait for any key to dismiss."""
    cols, rows = term_size()
    lines = status_text.splitlines()
    # draw from the middle of the screen
    box_h = len(lines) + 4
    start_row = max(1, (rows - box_h) // 2)
    out = []
    out.append(f"\033[{start_row};1H")
    out.append(f"  {CYAN}{BOLD}{'─' * (cols - 4)}{RST}\033[K")
    out.append(f"  {YELLOW}{BOLD} Keylogger Status{RST}\033[K")
    out.append(f"  {CYAN}{'─' * (cols - 4)}{RST}\033[K")
    for line in lines:
        out.append(f"  {line}\033[K")
    out.append(f"  {CYAN}{'─' * (cols - 4)}{RST}\033[K")
    out.append(f"  {DIM}Press any key to dismiss{RST}\033[K")
    sys.stdout.write("\n".join(out))
    sys.stdout.flush()
    read_key()


# ── Log detail screen ────────────────────────────────────────────────────────

def colour_line(line, cols):
    """Apply colour based on entry type."""
    trunc = line[:cols - 6] + "…" if len(line) > cols - 6 else line
    if "Shortcut:" in line:
        return f"    {CYAN}{trunc}{RST}"
    if "Enter key" in line:
        return f"    {GREEN}{trunc}{RST}"
    if "Keystroke:" in line:
        return f"    {WHITE}{trunc}{RST}"
    if "[Error]" in line:
        return f"    {RED}{trunc}{RST}"
    return f"    {GRAY}{trunc}{RST}"


def render_log_detail(date_str, lines, scroll, search_text=""):
    cols, rows = term_size()
    out = ["\033[H"]

    # header
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        label = dt.strftime("%A, %B %d, %Y")
    except ValueError:
        label = date_str

    title = f" {label} "
    pad_l = (cols - len(title)) // 2
    pad_r = cols - pad_l - len(title)
    out.append(f"{CYAN}{BOLD}{'─' * pad_l}{title}{'─' * pad_r}{RST}")
    out.append("")
    out.append(
        f"  {DIM}Total: {len(lines)} entries │ "
        f"Up/Down/PgUp/PgDn = scroll │ Backspace/Esc = back │ R = status │ X = stop │ Q = quit{RST}"
    )

    if search_text:
        out.append(f"  {YELLOW}Filter: {search_text}{RST}\033[K")
    out.append("")

    # log entries
    header_used = len(out) + 2
    visible = max(1, rows - header_used)

    # filter
    if search_text:
        filtered = [l for l in lines if search_text.lower() in l.lower()]
    else:
        filtered = lines

    total = len(filtered)
    # clamp scroll
    max_scroll = max(0, total - visible)
    scroll = max(0, min(scroll, max_scroll))

    window = filtered[scroll: scroll + visible]

    if window:
        for line in window:
            out.append(colour_line(line, cols) + "\033[K")
    else:
        out.append(f"    {DIM}No entries{' matching filter' if search_text else ''}.{RST}\033[K")

    # fill
    rendered_entries = len(window) if window else 1
    current = len(out) + 2
    for _ in range(max(0, rows - current)):
        out.append("\033[K")

    # footer
    out.append("")
    pct = int((scroll + visible) / total * 100) if total > 0 else 100
    pct = min(pct, 100)
    out.append(
        f"  {DIM}Lines {scroll + 1}-{min(scroll + visible, total)} "
        f"of {total} ({pct}%) │ / = filter{RST}\033[K"
    )

    sys.stdout.write("\n".join(out))
    sys.stdout.flush()
    return scroll


# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    enable_virtual_terminal()
    sys.stdout.write("\033[?25l")  # hide cursor
    sys.stdout.flush()

    try:
        _run()
    finally:
        sys.stdout.write("\033[?25h")  # show cursor
        clear()

def _run():
    dates = get_log_dates()
    cursor = 0
    mode = "dates"  # "dates" or "detail"
    scroll = 0
    detail_lines = []
    detail_date = ""
    search_text = ""

    clear()

    while True:
        if mode == "dates":
            dates = get_log_dates()
            if cursor >= len(dates):
                cursor = max(0, len(dates) - 1)
            render_date_list(dates, cursor)

            key = read_key()
            if key in ("q", "Q"):
                return
            elif key in ("r", "R"):
                show_status_popup(get_keylogger_status())
                clear()
            elif key in ("x", "X"):
                msg = stop_keylogger()
                show_toast(msg)
            elif key == "up" or key in ("k", "K"):
                cursor = max(0, cursor - 1)
            elif key == "down" or key in ("j", "J"):
                cursor = min(len(dates) - 1, cursor + 1) if dates else 0
            elif key == "enter" and dates:
                detail_date, path, _ = dates[cursor]
                detail_lines = read_log_file(path)
                scroll = 0
                search_text = ""
                mode = "detail"
                clear()

        elif mode == "detail":
            scroll = render_log_detail(detail_date, detail_lines, scroll, search_text)

            key = read_key()
            if key in ("q", "Q"):
                return
            elif key in ("r", "R"):
                show_status_popup(get_keylogger_status())
                clear()
            elif key in ("x", "X"):
                msg = stop_keylogger()
                show_toast(msg)
            elif key in ("backspace", "esc"):
                if search_text:
                    search_text = ""
                else:
                    mode = "dates"
                    clear()
            elif key == "up" or key in ("k", "K"):
                scroll = max(0, scroll - 1)
            elif key == "down" or key in ("j", "J"):
                scroll += 1
            elif key == "pgup":
                _, rows = term_size()
                scroll = max(0, scroll - (rows - 8))
            elif key == "pgdn":
                _, rows = term_size()
                scroll += rows - 8
            elif key == "home":
                scroll = 0
            elif key == "end":
                scroll = len(detail_lines)
            elif key == "/":
                # enter filter mode
                sys.stdout.write("\033[?25h")  # show cursor for typing
                sys.stdout.flush()
                search_text = _read_filter()
                sys.stdout.write("\033[?25l")  # hide cursor again
                sys.stdout.flush()
                scroll = 0


def _read_filter():
    """Read a filter string from the user with backspace support."""
    cols, rows = term_size()
    buf = ""
    while True:
        # show prompt at bottom
        sys.stdout.write(f"\033[{rows};1H")
        sys.stdout.write(f"  {YELLOW}Filter: {buf}{RST}\033[K")
        sys.stdout.flush()

        key = read_key()
        if key == "enter":
            return buf
        elif key == "esc":
            return ""
        elif key == "backspace":
            buf = buf[:-1]
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            buf += key
    return buf


if __name__ == "__main__":
    main()
