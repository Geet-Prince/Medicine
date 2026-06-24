#!/usr/bin/env python3
"""
AGY - Advanced AI-powered CLI Agent
Your intelligent terminal companion.
"""

import os, sys, json, time, shutil, threading, subprocess, platform
import datetime, random, re, math, signal
from pathlib import Path

# Force UTF-8 output so box-drawing characters render correctly on Windows
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Suppress pygame hello message
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

# ─────────────────────────────────────────────────────────────────────────────
# Optional dependency imports
# ─────────────────────────────────────────────────────────────────────────────
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import pygame
    pygame.mixer.init()
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ─────────────────────────────────────────────────────────────────────────────
# Config & Storage
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_DIR  = Path.home() / ".agy"
CONFIG_FILE = CONFIG_DIR  / "config.json"
NOTES_FILE  = CONFIG_DIR  / "notes.json"

DEFAULT_CONFIG = {
    "agent_name": "AGY",
    "response_style": "concise",
    "color_theme": "default",
    "music_folder": str(Path.home() / "Music"),
    "default_dir": str(Path.home()),
    "features": {
        "music": True, "filesystem": True, "code": True,
        "tasks": True, "search": True, "notes": True, "sysinfo": True
    }
}

def ensure_config():
    CONFIG_DIR.mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    if not NOTES_FILE.exists():
        NOTES_FILE.write_text(json.dumps([], indent=2))

def load_config():
    ensure_config()
    try:
        return json.loads(CONFIG_FILE.read_text())
    except:
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    CONFIG_DIR.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def load_notes():
    ensure_config()
    try:
        return json.loads(NOTES_FILE.read_text())
    except:
        return []

def save_notes(notes):
    NOTES_FILE.write_text(json.dumps(notes, indent=2))

# ─────────────────────────────────────────────────────────────────────────────
# Terminal Utilities
# ─────────────────────────────────────────────────────────────────────────────
def term_width():
    try:
        return shutil.get_terminal_size().columns
    except:
        return 80

def clear_line():
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()

def move_up(n=1):
    sys.stdout.write(f"\033[{n}A")
    sys.stdout.flush()

def print_line(text=""):
    print(text)

def hr(char="─"):
    return char * term_width()

def box(lines, width=None):
    """Draw a box around a list of text lines."""
    w = width or max((len(l) for l in lines), default=40) + 4
    w = max(w, 20)
    out = ["╭" + "─" * (w - 2) + "╮"]
    for l in lines:
        out.append("│ " + l.ljust(w - 4) + " │")
    out.append("╰" + "─" * (w - 2) + "╯")
    return "\n".join(out)

def double_box(lines, width=None):
    w = width or max((len(l) for l in lines), default=40) + 4
    w = max(w, 20)
    out = ["╔" + "═" * (w - 2) + "╗"]
    for l in lines:
        out.append("║ " + l.ljust(w - 4) + " ║")
    out.append("╚" + "═" * (w - 2) + "╝")
    return "\n".join(out)

def separator_box(lines_top, lines_bottom, width=None):
    """Box with a separator line after top section."""
    all_lines = lines_top + lines_bottom
    w = width or max((len(l) for l in all_lines), default=40) + 4
    w = max(w, 20)
    out = ["╔" + "═" * (w - 2) + "╗"]
    for l in lines_top:
        out.append("║ " + l.ljust(w - 4) + " ║")
    out.append("╠" + "═" * (w - 2) + "╣")
    for l in lines_bottom:
        out.append("║ " + l.ljust(w - 4) + " ║")
    out.append("╚" + "═" * (w - 2) + "╝")
    return "\n".join(out)

def search_box(query, result, source):
    w = max(len(query), len(result[:70]), len(source), 40) + 6
    lines = [
        f"SEARCH: {query}",
        "",
        f"RESULT: {result}",
        "",
        f"SOURCE: {source}",
    ]
    return ("┌" + "─" * (w - 2) + "┐\n" +
            "\n".join("│  " + l.ljust(w - 4) + "  │" for l in lines) +
            "\n└" + "─" * (w - 2) + "┘")

def fmt_time(secs):
    """Format seconds into mm:ss."""
    secs = int(secs)
    return f"{secs // 60}:{secs % 60:02d}"

def progress_bar(current, total, width=12):
    """Return an ASCII progress bar."""
    if total <= 0:
        return "░" * width
    filled = int(width * current / total)
    filled = min(filled, width)
    return "█" * filled + "░" * (width - filled)

# ─────────────────────────────────────────────────────────────────────────────
# Music Player State
# ─────────────────────────────────────────────────────────────────────────────
class MusicPlayer:
    def __init__(self):
        self.queue        = []           # list of {"title":..,"artist":..,"album":..,"path":..,"duration":0}
        self.current_idx  = -1
        self.playing      = False
        self.paused       = False
        self.elapsed      = 0.0         # seconds elapsed in current track
        self.repeat       = False
        self.shuffle      = False
        self._lock        = threading.Lock()
        self._pill_thread = None
        self._timer_thread= None
        self._pill_active = False
        self._pill_line   = ""
        self._last_pill   = ""

    @property
    def current(self):
        if 0 <= self.current_idx < len(self.queue):
            return self.queue[self.current_idx]
        return None

    def _format_pill(self):
        song = self.current
        if not song:
            return ""
        icon    = "❚❚" if self.paused else "▶"
        title   = song.get("title", "Unknown")
        artist  = song.get("artist", "Unknown")
        album   = song.get("album", "")
        total   = song.get("duration", 0) or 0
        elapsed = min(self.elapsed, total) if total else self.elapsed
        t_cur   = fmt_time(elapsed)
        t_tot   = fmt_time(total) if total else "--:--"
        bar     = progress_bar(elapsed, total, 14)

        album_part = f"  |  {album}" if album else ""
        inner  = f" {icon}  {title} — {artist}{album_part}  |  {t_cur} / {t_tot}  {bar}"
        w      = term_width()
        inner  = inner[:w - 4]
        pad    = w - len(inner) - 4
        inner  = inner + " " * max(0, pad)
        pill   = ("╭" + "─" * (len(inner) + 2) + "╮\n" +
                  "│" + " " + inner + " " + "│\n" +
                  "╰" + "─" * (len(inner) + 2) + "╯")
        return pill

    def _pill_loop(self):
        """Continuously redraws the pill at the bottom."""
        while self._pill_active:
            if self.playing or self.paused:
                pill = self._format_pill()
                if pill != self._last_pill:
                    # Move to beginning of pill (3 lines) and redraw
                    sys.stdout.write("\r\033[2K")
                    sys.stdout.write("\033[1A\r\033[2K")
                    sys.stdout.write("\033[1A\r\033[2K")
                    sys.stdout.write(pill + "\n")
                    sys.stdout.flush()
                    self._last_pill = pill
            time.sleep(1)

    def _timer_loop(self):
        """Counts elapsed time and handles track endings."""
        while self._pill_active:
            time.sleep(1)
            if self.playing and not self.paused:
                with self._lock:
                    self.elapsed += 1
                    song = self.current
                    if song:
                        dur = song.get("duration", 0) or 0
                        if dur > 0 and self.elapsed >= dur:
                            self._auto_next()

    def _auto_next(self):
        if self.repeat:
            self.elapsed = 0
            self._play_current()
        elif self.current_idx < len(self.queue) - 1:
            self.current_idx += 1
            self.elapsed = 0
            print(f"\n  Up Next: {self.queue[self.current_idx]['title']} — {self.queue[self.current_idx]['artist']}")
            self._play_current()
        else:
            self.playing = False
            print("\n  Queue finished.")

    def _play_current(self):
        song = self.current
        if not song:
            return
        if HAS_PYGAME and song.get("path") and os.path.exists(song["path"]):
            try:
                pygame.mixer.music.load(song["path"])
                pygame.mixer.music.play()
                self.playing = True
                self.paused  = False
            except Exception as e:
                print(f"  [pygame] Could not play file: {e}")
                self.playing = True  # simulate for demo
        else:
            self.playing = True
            self.paused  = False

    def start_pill(self):
        if not self._pill_active:
            self._pill_active = True
            print("\n\n\n")  # Reserve 3 lines for pill
            self._pill_thread  = threading.Thread(target=self._pill_loop,  daemon=True)
            self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
            self._pill_thread.start()
            self._timer_thread.start()

    def stop_pill(self):
        self._pill_active = False
        self._last_pill   = ""

    def play(self, song_info):
        """Add and immediately play a song."""
        self.queue.insert(self.current_idx + 1, song_info)
        self.current_idx += 1
        self.elapsed = 0
        self._play_current()
        self.start_pill()
        return song_info

    def pause(self):
        if self.playing and not self.paused:
            if HAS_PYGAME:
                try: pygame.mixer.music.pause()
                except: pass
            self.paused  = True
            return True
        return False

    def resume(self):
        if self.paused:
            if HAS_PYGAME:
                try: pygame.mixer.music.unpause()
                except: pass
            self.paused  = False
            self.playing = True
            return True
        return False

    def next_track(self):
        if self.current_idx < len(self.queue) - 1:
            self.current_idx += 1
            self.elapsed = 0
            self._play_current()
            return self.current
        return None

    def prev_track(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self.elapsed = 0
            self._play_current()
            return self.current
        return None

    def stop(self):
        self.playing = False
        self.paused  = False
        if HAS_PYGAME:
            try: pygame.mixer.music.stop()
            except: pass
        self.stop_pill()

    def queue_add(self, song_info):
        self.queue.append(song_info)

    def queue_list(self):
        return self.queue

    def do_shuffle(self):
        if not self.queue:
            return
        cur = self.current
        rest = [q for i, q in enumerate(self.queue) if i != self.current_idx]
        random.shuffle(rest)
        if cur:
            self.queue = [cur] + rest
            self.current_idx = 0
        else:
            self.queue = rest

# ─────────────────────────────────────────────────────────────────────────────
# Reminder system
# ─────────────────────────────────────────────────────────────────────────────
reminders = []  # list of {"at": timestamp, "msg": str}

def start_reminder_watcher():
    def _watch():
        while True:
            now = time.time()
            fired = [r for r in reminders if r["at"] <= now]
            for r in fired:
                print(f"\n  ⏰  REMINDER: {r['msg']}\n")
                reminders.remove(r)
            time.sleep(5)
    t = threading.Thread(target=_watch, daemon=True)
    t.start()

# ─────────────────────────────────────────────────────────────────────────────
# File System Navigator
# ─────────────────────────────────────────────────────────────────────────────
cwd = Path.cwd()

def cmd_ls(args):
    global cwd
    target = cwd / args[0] if args else cwd
    if not target.exists():
        return f"  Path not found: {target}"
    items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    header = f"  Directory: {target}\n"
    lines  = [f"  {'NAME':<30} {'TYPE':<6} {'SIZE':>10}  {'MODIFIED'}"]
    lines.append("  " + "─" * 70)
    for p in items:
        try:
            stat  = p.stat()
            ftype = "DIR" if p.is_dir() else "FILE"
            size  = f"{stat.st_size:,}" if p.is_file() else "—"
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            icon  = "📁 " if p.is_dir() else "📄 "
            lines.append(f"  {icon}{p.name:<28} {ftype:<6} {size:>10}  {mtime}")
        except:
            lines.append(f"  {p.name}")
    return header + "\n".join(lines)

def cmd_cd(args):
    global cwd
    if not args:
        cwd = Path.home()
        return f"  Now in: {cwd}"
    target = (cwd / args[0]).resolve()
    if target.is_dir():
        cwd = target
        return f"  Now in: {cwd}"
    return f"  Not a directory: {args[0]}"

def cmd_pwd():
    return f"  {cwd}"

def cmd_mkdir(args):
    if not args:
        return "  Usage: mkdir <name>"
    p = cwd / args[0]
    p.mkdir(parents=True, exist_ok=True)
    return f"  Created directory: {p}"

def cmd_touch(args):
    if not args:
        return "  Usage: touch <filename>"
    p = cwd / args[0]
    p.touch()
    return f"  Created file: {p}"

def cmd_cat(args):
    if not args:
        return "  Usage: cat <filename>"
    p = cwd / args[0]
    if not p.exists():
        return f"  File not found: {args[0]}"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"  Error reading file: {e}"

def cmd_rm(args):
    if not args:
        return "  Usage: rm <file>"
    p = cwd / args[0]
    if not p.exists():
        return f"  Not found: {args[0]}"
    ans = input(f"  Delete '{p}'? (yes/no): ").strip().lower()
    if ans == "yes":
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"  Deleted: {p}"
    return "  Cancelled."

def cmd_cp(args):
    if len(args) < 2:
        return "  Usage: cp <source> <destination>"
    src = cwd / args[0]
    dst = cwd / args[1]
    if not src.exists():
        return f"  Source not found: {args[0]}"
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return f"  Copied {src} → {dst}"

def cmd_mv(args):
    if len(args) < 2:
        return "  Usage: mv <source> <destination>"
    src = cwd / args[0]
    dst = cwd / args[1]
    if not src.exists():
        return f"  Source not found: {args[0]}"
    shutil.move(str(src), str(dst))
    return f"  Moved {src} → {dst}"

def cmd_open(args):
    if not args:
        return "  Usage: open <filename>"
    p = cwd / args[0]
    if not p.exists():
        return f"  Not found: {args[0]}"
    try:
        if platform.system() == "Windows":
            os.startfile(str(p))
        elif platform.system() == "Darwin":
            subprocess.call(["open", str(p)])
        else:
            subprocess.call(["xdg-open", str(p)])
        return f"  Opened: {p}"
    except Exception as e:
        return f"  Error: {e}"

# ─────────────────────────────────────────────────────────────────────────────
# System Information
# ─────────────────────────────────────────────────────────────────────────────
def cmd_sysinfo():
    lines = []
    lines.append(f"  OS:       {platform.system()} {platform.release()} ({platform.version()[:40]})")
    lines.append(f"  Machine:  {platform.machine()}  |  Python: {platform.python_version()}")
    lines.append(f"  Shell:    {os.environ.get('SHELL', os.environ.get('COMSPEC', 'unknown'))}")
    lines.append(f"  Time:     {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    if HAS_PSUTIL:
        cpu    = psutil.cpu_percent(interval=1)
        mem    = psutil.virtual_memory()
        disk   = psutil.disk_usage(str(Path.home()))
        uptime = time.time() - psutil.boot_time()
        uh, ur = divmod(int(uptime), 3600)
        um, us = divmod(ur, 60)
        lines.append(f"  CPU:      {cpu:.1f}%  {'█' * int(cpu / 5)}{'░' * (20 - int(cpu / 5))}")
        lines.append(f"  RAM:      {mem.used / 1e9:.1f} GB / {mem.total / 1e9:.1f} GB  ({mem.percent:.0f}%)")
        lines.append(f"  Disk:     {disk.used / 1e9:.1f} GB / {disk.total / 1e9:.1f} GB  ({disk.percent:.0f}%)")
        lines.append(f"  Uptime:   {uh}h {um}m {us}s")
    else:
        lines.append("  (Install psutil for detailed CPU/RAM/Disk info:  pip install psutil)")
    # Network check
    try:
        import socket
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        net = "Connected ✓"
    except:
        net = "Disconnected ✗"
    lines.append(f"  Network:  {net}")

    w = max(len(l) for l in lines) + 4
    print("┌" + "─" * (w - 2) + "┐")
    print("│" + "  SYSTEM INFORMATION".center(w - 2) + "│")
    print("├" + "─" * (w - 2) + "┤")
    for l in lines:
        print("│" + l.ljust(w - 2) + "│")
    print("└" + "─" * (w - 2) + "┘")

# ─────────────────────────────────────────────────────────────────────────────
# Notes
# ─────────────────────────────────────────────────────────────────────────────
def cmd_note(args, notes):
    if not args:
        return "  Usage: note add <text> | note list | note delete <n>"
    sub = args[0].lower()
    if sub == "add":
        text = " ".join(args[1:])
        if not text:
            return "  Provide some text to save."
        entry = {"id": len(notes) + 1, "text": text,
                 "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        notes.append(entry)
        save_notes(notes)
        return f"  Note #{entry['id']} saved."
    elif sub == "list":
        if not notes:
            return "  No notes saved yet."
        lines = ["  SAVED NOTES", "  " + "─" * 50]
        for n in notes:
            lines.append(f"  #{n['id']}  [{n['ts']}]  {n['text']}")
        return "\n".join(lines)
    elif sub == "delete":
        if len(args) < 2:
            return "  Usage: note delete <number>"
        try:
            nid = int(args[1])
        except:
            return "  Invalid note number."
        before = len(notes)
        notes[:] = [n for n in notes if n["id"] != nid]
        save_notes(notes)
        return f"  Deleted note #{nid}." if len(notes) < before else f"  Note #{nid} not found."
    elif sub == "export":
        path = CONFIG_DIR / "notes_export.txt"
        path.write_text("\n".join(f"#{n['id']} [{n['ts']}] {n['text']}" for n in notes))
        return f"  Exported to {path}"
    return "  Unknown note command."

# ─────────────────────────────────────────────────────────────────────────────
# Reminder
# ─────────────────────────────────────────────────────────────────────────────
def cmd_remind(parts):
    # "remind me in 10 minutes to <msg>" or "remind me 5 seconds to <msg>"
    text = " ".join(parts)
    m = re.search(r"in\s+(\d+)\s+(second|minute|hour)s?\s+to\s+(.+)", text, re.IGNORECASE)
    if m:
        amount = int(m.group(1))
        unit   = m.group(2).lower()
        msg    = m.group(3)
        secs   = amount * {"second": 1, "minute": 60, "hour": 3600}[unit]
        reminders.append({"at": time.time() + secs, "msg": msg})
        return f"  Reminder set: '{msg}' in {amount} {unit}(s)."
    return "  Usage: remind me in <N> minutes/seconds/hours to <task>"

# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────
def cmd_settings(args, cfg):
    if not args or args[0] == "open":
        lines = [
            "  SETTINGS",
            "  " + "─" * 50,
            f"  agent_name      = {cfg['agent_name']}",
            f"  response_style  = {cfg['response_style']}  (verbose / concise / minimal)",
            f"  color_theme     = {cfg['color_theme']}",
            f"  music_folder    = {cfg['music_folder']}",
            f"  default_dir     = {cfg['default_dir']}",
            "",
            "  To change:  settings set <key> <value>",
            f"  Config file: {CONFIG_FILE}",
        ]
        return "\n".join(lines)
    if args[0] == "set" and len(args) >= 3:
        key, val = args[1], " ".join(args[2:])
        if key in cfg:
            cfg[key] = val
            save_config(cfg)
            return f"  Set {key} = {val}  (saved)"
        return f"  Unknown setting: {key}"
    return "  Usage: settings open | settings set <key> <value>"

# ─────────────────────────────────────────────────────────────────────────────
# Web Search
# ─────────────────────────────────────────────────────────────────────────────
def cmd_search(query):
    if not HAS_REQUESTS:
        return "  Install requests to use web search:  pip install requests"
    try:
        # Use DuckDuckGo instant answer API (no key required)
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1},
            timeout=8
        )
        data = resp.json()
        abstract = data.get("AbstractText", "").strip()
        source   = data.get("AbstractURL", data.get("AbstractSource", "DuckDuckGo"))
        if not abstract:
            # Try related topics
            topics = data.get("RelatedTopics", [])
            for t in topics:
                if isinstance(t, dict) and t.get("Text"):
                    abstract = t["Text"]
                    source   = t.get("FirstURL", "DuckDuckGo")
                    break
        if not abstract:
            abstract = "No direct answer found. Try a more specific query."
            source   = "https://duckduckgo.com/?q=" + query.replace(" ", "+")
        # Wrap long abstract
        words = abstract.split()
        lines = []
        cur   = ""
        for w in words:
            if len(cur) + len(w) + 1 > 68:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            lines.append(cur)
        result_text = "\n│  ".join(lines)
        w = 76
        print("┌" + "─" * (w - 2) + "┐")
        print(f"│  SEARCH: {query[:60]:<{w-12}}│")
        print("├" + "─" * (w - 2) + "┤")
        print(f"│  RESULT:{'':>2}{result_text:<{w - 12}}│")
        print("├" + "─" * (w - 2) + "┤")
        print(f"│  SOURCE: {source[:w-14]:<{w-12}}│")
        print("└" + "─" * (w - 2) + "┘")
        return ""
    except Exception as e:
        return f"  Search error: {e}"

# ─────────────────────────────────────────────────────────────────────────────
# Code assistant (explains / fixes / writes code)
# ─────────────────────────────────────────────────────────────────────────────
CODE_TEMPLATES = {
    "hello world python": ('python', '# Hello World in Python\nprint("Hello, World!")'),
    "hello world javascript": ('javascript', '// Hello World in JavaScript\nconsole.log("Hello, World!");'),
    "http server python": ('python', '''# Simple HTTP server in Python
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hello from Python HTTP server!")

server = HTTPServer(("0.0.0.0", 8000), Handler)
print("Serving on http://localhost:8000")
server.serve_forever()'''),
    "read file python": ('python', '''# Read a file in Python
with open("filename.txt", "r", encoding="utf-8") as f:
    content = f.read()
print(content)'''),
    "list files python": ('python', '''# List files in a directory
import os
for name in os.listdir("."):
    print(name)'''),
}

def cmd_code(args):
    if not args:
        return ("  Usage:\n"
                "  code write <description>   - Generate code\n"
                "  code explain <code>        - Explain code\n"
                "  code fix <error>           - Help fix a bug\n"
                "  code run <file.py>         - Run a script")
    sub = args[0].lower()

    if sub == "run":
        if len(args) < 2:
            return "  Provide a file to run."
        fpath = cwd / args[1]
        if not fpath.exists():
            return f"  File not found: {args[1]}"
        print(f"\n  Running: python {fpath}\n  " + "─" * 40)
        try:
            result = subprocess.run(
                [sys.executable, str(fpath)],
                capture_output=True, text=True, timeout=30
            )
            out = result.stdout or ""
            err = result.stderr or ""
            if out:
                print("  OUTPUT:\n" + "\n".join("  " + l for l in out.splitlines()))
            if err:
                print("  STDERR:\n" + "\n".join("  " + l for l in err.splitlines()))
            rc = result.returncode
            return f"\n  Exited with code {rc}"
        except subprocess.TimeoutExpired:
            return "  Script timed out after 30 seconds."
        except Exception as e:
            return f"  Error: {e}"

    if sub == "write":
        desc = " ".join(args[1:]).lower()
        # Check template
        for key, (lang, code) in CODE_TEMPLATES.items():
            if key in desc:
                print(f"\n  Generated {lang} code:\n")
                print("  " + "─" * 50)
                for line in code.splitlines():
                    print("  " + line)
                print("  " + "─" * 50)
                return ""
        # Generic helpful message
        return (f"  Generating code for: {' '.join(args[1:])}\n\n"
                "  Tip: For AI-powered code generation, connect AGY to an API key.\n"
                "  Available templates: hello world python, http server python,\n"
                "                       read file python, list files python")

    if sub == "explain":
        snippet = " ".join(args[1:])
        print(f"\n  Code Analysis:\n  {'─' * 50}")
        print(f"  Snippet: {snippet[:80]}")
        print( "  " + "─" * 50)
        # Basic static analysis
        if "def " in snippet:
            print("  ▸ Contains a function definition")
        if "class " in snippet:
            print("  ▸ Contains a class definition")
        if "import " in snippet:
            print("  ▸ Imports external modules")
        if "for " in snippet or "while " in snippet:
            print("  ▸ Contains a loop")
        if "if " in snippet:
            print("  ▸ Contains conditional logic")
        if "try" in snippet:
            print("  ▸ Contains error handling (try/except)")
        print("\n  For deep AI explanation, connect AGY to an LLM API key.")
        return ""

    if sub == "fix":
        error = " ".join(args[1:])
        print(f"\n  Bug Analysis:\n  {'─' * 50}")
        print(f"  Error: {error}")
        print( "  " + "─" * 50)
        # Common error patterns
        if "NameError" in error:
            print("  ▸ NameError: A variable or function is referenced before it is defined.")
            print("    Fix: Check spelling, ensure the variable is assigned before use.")
        elif "TypeError" in error:
            print("  ▸ TypeError: Wrong type passed to a function.")
            print("    Fix: Check function arguments — convert types if needed (str(), int()...).")
        elif "IndexError" in error:
            print("  ▸ IndexError: List index is out of range.")
            print("    Fix: Check len(list) before accessing index, use try/except.")
        elif "KeyError" in error:
            print("  ▸ KeyError: Dictionary key does not exist.")
            print("    Fix: Use dict.get('key', default) or check 'if key in dict'.")
        elif "ImportError" in error or "ModuleNotFoundError" in error:
            print("  ▸ Module not found.")
            print("    Fix: Run  pip install <module_name>  to install it.")
        elif "SyntaxError" in error:
            print("  ▸ SyntaxError: Code is not valid Python.")
            print("    Fix: Check for missing colons (:), brackets, or quotes.")
        else:
            print("  ▸ Could not auto-diagnose. Paste the full traceback for better help.")
        return ""

    return "  Unknown code subcommand. Use: write / explain / fix / run"

# ─────────────────────────────────────────────────────────────────────────────
# Task Runner
# ─────────────────────────────────────────────────────────────────────────────
def cmd_run(args):
    if not args:
        return "  Usage: run <shell command>"
    cmd_str = " ".join(args)
    print(f"\n  Running: {cmd_str}")
    print("  " + "─" * 50)
    try:
        result = subprocess.run(
            cmd_str, shell=True, capture_output=True, text=True, timeout=60,
            cwd=str(cwd)
        )
        out = result.stdout or ""
        err = result.stderr or ""
        if out:
            for line in out.splitlines():
                print("  " + line)
        if err:
            print("  STDERR:")
            for line in err.splitlines():
                print("  " + line)
        return f"  Completed (exit code {result.returncode})"
    except subprocess.TimeoutExpired:
        return "  Command timed out after 60 seconds."
    except Exception as e:
        return f"  Error: {e}"

# ─────────────────────────────────────────────────────────────────────────────
# Music queue display
# ─────────────────────────────────────────────────────────────────────────────
def show_queue(player):
    q = player.queue_list()
    if not q:
        return "  Queue is empty."
    lines = ["  MUSIC QUEUE", "  " + "─" * 60]
    for i, s in enumerate(q):
        marker = " [now playing]" if i == player.current_idx else ""
        lines.append(f"  {i+1:2}. {s.get('title','?')} — {s.get('artist','?')}{marker}")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────────
HELP_TEXT = """
  FEATURES & COMMANDS
  ────────────────────────────────────────────────────────────

  CHAT
    Just type anything naturally to talk to AGY.

  MUSIC PLAYER
    play <song name or file path>   Start playing a song
    pause                           Pause playback
    resume                          Resume playback
    next                            Skip to next song
    previous                        Go to previous song
    stop                            Stop playback
    shuffle on / off                Shuffle the queue
    repeat on / off                 Loop current song
    queue add <song>                Add a song to the queue
    queue list                      Show the full queue

  FILE SYSTEM
    ls [folder]                     List directory contents
    cd <folder>                     Change directory
    pwd                             Show current directory
    mkdir <name>                    Create a directory
    touch <file>                    Create an empty file
    cat <file>                      Print file contents
    rm <file>                       Delete a file
    cp <src> <dst>                  Copy a file
    mv <src> <dst>                  Move / rename a file
    open <file>                     Open in default app

  CODE ASSISTANT
    code write <description>        Generate code
    code explain <snippet>          Explain code
    code fix <error message>        Help fix a bug
    code run <script.py>            Run a Python script

  TASK RUNNER
    run <shell command>             Run any shell command

  WEB SEARCH
    search <query>                  Search the web

  NOTES & REMINDERS
    note add <text>                 Save a note
    note list                       Show all notes
    note delete <number>            Delete a note
    note export                     Export notes to file
    remind me in <N> minutes to <task>

  SYSTEM INFO
    sysinfo                         System dashboard

  SETTINGS
    settings open                   View all settings
    settings set <key> <value>      Change a setting

  OTHER
    help                            Show this help
    clear                           Clear the terminal
    exit / quit                     Exit AGY
"""

# ─────────────────────────────────────────────────────────────────────────────
# Welcome Screen
# ─────────────────────────────────────────────────────────────────────────────
def print_welcome(cfg):
    name = cfg.get("agent_name", "AGY")
    w    = 44
    print("")
    print("  ╔" + "═" * (w - 2) + "╗")
    print("  ║" + f"  {name} CLI AGENT".center(w - 2) + "║")
    print("  ║" + "  Your intelligent terminal AI".center(w - 2) + "║")
    print("  ╠" + "═" * (w - 2) + "╣")
    print("  ║" + "  Type a command or ask me anything".center(w - 2) + "║")
    print("  ║" + "  Type  help  to see all features".center(w - 2) + "║")
    print("  ╚" + "═" * (w - 2) + "╝")
    print("")
    if not HAS_PSUTIL:
        print("  Tip: pip install psutil   for system monitoring")
    if not HAS_PYGAME:
        print("  Tip: pip install pygame   for local audio playback")
    if not HAS_REQUESTS:
        print("  Tip: pip install requests for web search")
    print("")

# ─────────────────────────────────────────────────────────────────────────────
# Music helper — build a song info dict from name/path
# ─────────────────────────────────────────────────────────────────────────────
def resolve_song(name_or_path):
    p = Path(name_or_path)
    if p.exists() and p.is_file():
        # Real file
        duration = 0
        if HAS_PYGAME:
            try:
                snd = pygame.mixer.Sound(str(p))
                duration = snd.get_length()
            except: pass
        return {
            "title":    p.stem,
            "artist":   "Local File",
            "album":    "",
            "path":     str(p),
            "duration": duration
        }
    # Treat as song name — mock entry (in production wire to yt-dlp / geet backend)
    return {
        "title":    name_or_path,
        "artist":   "Unknown Artist",
        "album":    "",
        "path":     None,
        "duration": 0
    }

# ─────────────────────────────────────────────────────────────────────────────
# Main REPL
# ─────────────────────────────────────────────────────────────────────────────
def main():
    cfg    = load_config()
    notes  = load_notes()
    player = MusicPlayer()

    # Change to configured default directory if set
    global cwd
    default_d = cfg.get("default_dir", "")
    if default_d and Path(default_d).is_dir():
        cwd = Path(default_d)

    start_reminder_watcher()
    print_welcome(cfg)

    try:
        while True:
            try:
                prompt = f"\n  [{cwd.name}] {cfg['agent_name']} > "
                raw    = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n  Goodbye!\n")
                player.stop()
                sys.exit(0)

            if not raw:
                continue

            tokens = raw.split()
            cmd    = tokens[0].lower()
            args   = tokens[1:]

            # ── Exit ─────────────────────────────────────────────────────────
            if cmd in ("exit", "quit", "bye"):
                print("\n  Goodbye!\n")
                player.stop()
                break

            # ── Clear ────────────────────────────────────────────────────────
            elif cmd == "clear":
                os.system("cls" if platform.system() == "Windows" else "clear")

            # ── Help ─────────────────────────────────────────────────────────
            elif cmd == "help":
                print(HELP_TEXT)

            # ── File System ──────────────────────────────────────────────────
            elif cmd == "ls":
                print(cmd_ls(args))
            elif cmd == "cd":
                print(cmd_cd(args))
            elif cmd == "pwd":
                print(cmd_pwd())
            elif cmd == "mkdir":
                print(cmd_mkdir(args))
            elif cmd == "touch":
                print(cmd_touch(args))
            elif cmd == "cat":
                print(cmd_cat(args))
            elif cmd == "rm":
                print(cmd_rm(args))
            elif cmd == "cp":
                print(cmd_cp(args))
            elif cmd == "mv":
                print(cmd_mv(args))
            elif cmd == "open":
                print(cmd_open(args))

            # ── System Info ──────────────────────────────────────────────────
            elif cmd == "sysinfo":
                cmd_sysinfo()

            # ── Music: play ──────────────────────────────────────────────────
            elif cmd == "play":
                if not args:
                    print("  Usage: play <song name or file path>")
                else:
                    song_name = " ".join(args)
                    song_info = resolve_song(song_name)
                    player.play(song_info)
                    print(f"\n  Now playing: {song_info['title']} — {song_info['artist']}")
                    if not song_info.get("path"):
                        print("  Note: For real playback, provide a local audio file path.")

            elif cmd == "pause":
                if player.pause():
                    print("  Paused.")
                elif player.paused:
                    print("  Already paused. Type  resume  to continue.")
                else:
                    print("  Nothing is playing.")

            elif cmd == "resume":
                if player.resume():
                    print("  Resumed.")
                else:
                    print("  Nothing is paused.")

            elif cmd == "next":
                t = player.next_track()
                if t:
                    print(f"  Skipped to: {t['title']} — {t['artist']}")
                else:
                    print("  No next track in queue.")

            elif cmd == "previous":
                t = player.prev_track()
                if t:
                    print(f"  Back to: {t['title']} — {t['artist']}")
                else:
                    print("  Already at the start of the queue.")

            elif cmd == "stop":
                ans = input("  Stop playback and clear player? (yes/no): ").strip().lower()
                if ans == "yes":
                    player.stop()
                    print("  Stopped.")

            elif cmd == "shuffle":
                val = args[0].lower() if args else "on"
                player.shuffle = val == "on"
                if player.shuffle:
                    player.do_shuffle()
                print(f"  Shuffle {'on — queue shuffled' if player.shuffle else 'off'}.")

            elif cmd == "repeat":
                val = args[0].lower() if args else "on"
                player.repeat = val == "on"
                print(f"  Repeat {'on' if player.repeat else 'off'}.")

            # ── Queue ────────────────────────────────────────────────────────
            elif cmd == "queue":
                if not args:
                    print(show_queue(player))
                elif args[0].lower() == "list":
                    print(show_queue(player))
                elif args[0].lower() == "add":
                    song_name = " ".join(args[1:])
                    if not song_name:
                        print("  Provide a song name.")
                    else:
                        info = resolve_song(song_name)
                        player.queue_add(info)
                        print(f"  Added to queue: {info['title']}")
                else:
                    print("  Usage: queue list | queue add <song>")

            # ── Web Search ───────────────────────────────────────────────────
            elif cmd == "search":
                query = " ".join(args)
                if not query:
                    print("  Usage: search <query>")
                else:
                    result = cmd_search(query)
                    if result:
                        print(result)

            # ── Notes ────────────────────────────────────────────────────────
            elif cmd == "note":
                print(cmd_note(args, notes))

            # ── Remind ───────────────────────────────────────────────────────
            elif cmd == "remind":
                print(cmd_remind(args))

            # ── Code ─────────────────────────────────────────────────────────
            elif cmd == "code":
                print(cmd_code(args))

            # ── Task Runner ──────────────────────────────────────────────────
            elif cmd == "run":
                print(cmd_run(args))

            # ── Settings ─────────────────────────────────────────────────────
            elif cmd == "settings":
                print(cmd_settings(args, cfg))

            # ── Natural chat fallback ─────────────────────────────────────────
            else:
                # Simple smart responses for natural language
                rl = raw.lower()
                if any(w in rl for w in ["hello", "hi ", "hey", "howdy"]):
                    print(f"  Hey there! I'm {cfg['agent_name']}. How can I help you?")
                elif any(w in rl for w in ["how are you", "how r u", "you okay"]):
                    print("  Running at 100%! What do you need?")
                elif "what time" in rl or "current time" in rl:
                    print(f"  {datetime.datetime.now().strftime('%H:%M:%S on %A, %B %d %Y')}")
                elif "what can you do" in rl or "what do you do" in rl:
                    print("  Type  help  to see everything I can do.")
                elif "thank" in rl:
                    print("  Anytime. Anything else?")
                elif "who are you" in rl or "what are you" in rl:
                    print(f"  I'm {cfg['agent_name']} — your intelligent terminal AI. Type  help  to get started.")
                elif "date" in rl or "today" in rl:
                    print(f"  {datetime.datetime.now().strftime('%A, %B %d %Y')}")
                else:
                    # Echo the command and suggest help
                    print(f"  I got: \"{raw}\"")
                    print("  Not sure what that means — type  help  to see all available commands.")

    except Exception as e:
        print(f"\n  [AGY Error] {e}")
        import traceback; traceback.print_exc()

    finally:
        player.stop()


if __name__ == "__main__":
    main()
