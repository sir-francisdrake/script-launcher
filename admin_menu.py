import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False

# --------------------------------------------------
# Configuration
# --------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = BASE_DIR / "scripts"
LOG_DIR = BASE_DIR / "logs"

LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / f"admin_menu_{datetime.now():%Y%m%d}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("admin_menu")

# --------------------------------------------------
# User preferences (config.json)
# --------------------------------------------------
CONFIG_FILE = BASE_DIR / "config.json"
SORT_MODES = ("category", "name", "modified")
DEFAULT_CONFIG = {"last_filter": "", "sort": "category", "favorites": []}


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.is_file():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config["last_filter"] = str(data.get("last_filter", ""))[:80]
                sort_mode = data.get("sort", "category")
                config["sort"] = sort_mode if sort_mode in SORT_MODES else "category"
                favorites = data.get("favorites", [])
                if isinstance(favorites, list):
                    config["favorites"] = [str(item) for item in favorites if isinstance(item, str)]
    except (OSError, ValueError):
        logger.warning("Could not read %s; using defaults.", CONFIG_FILE)
    return config


def save_config(config):
    try:
        CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("Could not write %s.", CONFIG_FILE)


# --------------------------------------------------
# Console appearance (stdlib only)
# --------------------------------------------------
USE_COLOR = os.environ.get("NO_COLOR") is None


def _ansi(code):
    return code if USE_COLOR else ""


R = _ansi("\033[0m")
BOLD = _ansi("\033[1m")
DIM = _ansi("\033[2m")
CYAN = _ansi("\033[36m")
YELLOW = _ansi("\033[33m")
GREEN = _ansi("\033[32m")
RED = _ansi("\033[31m")
WHITE = _ansi("\033[97m")
REVERSE = _ansi("\033[7m")


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize(value, limit=160):
    """Strip ANSI escapes and control characters from untrusted metadata."""
    if not value:
        return value
    value = _ANSI_RE.sub("", value)
    value = _CONTROL_RE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _visible_len(text):
    return len(re.sub(r"\x1b\[[0-9;]*m", "", text))


def _pad(text, width):
    padding = width - _visible_len(text)
    if padding < 0:
        padding = 0
    return text + " " * padding


def terminal_box_width():
    """Adapt the menu width to the current terminal."""
    cols = shutil.get_terminal_size((80, 24)).columns
    width = cols - 2
    if width < 40:
        width = 40
    if width > 90:
        width = 90
    return width


def enable_vt():
    """Enable ANSI/VT processing on Windows consoles when possible."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def clear_screen():
    if os.name == "nt":
        os.system("cls")
    else:
        print("\033[2J\033[H", end="")


# --------------------------------------------------
# Dynamic script discovery
# --------------------------------------------------
def _read_metadata(path):
    name = None
    description = None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for i, line in enumerate(handle):
                if i >= 20:
                    break
                stripped = line.strip()
                lower = stripped.lower()
                if lower.startswith("# name:"):
                    name = stripped.split(":", 1)[1].strip()
                elif lower.startswith("# description:"):
                    description = stripped.split(":", 1)[1].strip()
    except OSError:
        pass
    return _sanitize(name), _sanitize(description)


def _pretty_name(path):
    pretty = path.stem.replace("_", " ").replace("-", " ")
    cleaned = _sanitize(pretty) if pretty else _sanitize(path.name)
    return cleaned.title() if cleaned else _sanitize(path.name) or path.name


def script_id(path):
    """Stable identifier for favorites: path relative to the scripts folder."""
    try:
        return Path(path).resolve().relative_to(SCRIPT_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def discover_scripts():
    """Discover .ps1 scripts recursively; subfolders become categories/tags."""
    scripts = []
    if not SCRIPT_DIR.is_dir():
        return scripts

    base = SCRIPT_DIR.resolve()
    for path in sorted(SCRIPT_DIR.rglob("*.ps1")):
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(base):
                continue
        except (ValueError, OSError):
            continue
        if not resolved.is_file():
            continue

        try:
            relative = resolved.relative_to(base)
            category = relative.parent.as_posix()
            if category == ".":
                category = "root"
            category = _sanitize(category) or "root"
            mtime = resolved.stat().st_mtime
        except (ValueError, OSError):
            category = "root"
            mtime = 0.0

        name, description = _read_metadata(path)
        if not name:
            name = _pretty_name(path)
        if not description:
            description = f"Run {_sanitize(path.name)}"

        scripts.append(
            {
                "name": name,
                "file": path.name,
                "description": description,
                "path": str(resolved),
                "category": category,
                "id": script_id(resolved),
                "mtime": mtime,
            }
        )
    return scripts


def sort_scripts(scripts, sort_mode, favorites):
    """Sort scripts, always keeping favorites/pinned entries first."""
    def key(script):
        pinned = script["id"] in favorites
        if sort_mode == "name":
            return (not pinned, script["name"].lower(), script["category"].lower())
        if sort_mode == "modified":
            return (not pinned, -script["mtime"], script["name"].lower())
        return (not pinned, script["category"].lower(), script["name"].lower())

    return sorted(scripts, key=key)


def validate_script(script):
    """Resolve and safety-check a discovered script."""
    base = SCRIPT_DIR.resolve()
    candidate = Path(script["path"]).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("Unsafe script path (path traversal blocked).")
    if candidate.suffix.lower() != ".ps1":
        raise ValueError("Only .ps1 scripts are permitted.")
    if not candidate.is_file():
        raise FileNotFoundError(f"Script not found: {candidate}")
    return candidate


# --------------------------------------------------
# CLI mode
# --------------------------------------------------
def print_menu(scripts, favorites):
    clear_screen()
    width = terminal_box_width()
    content_width = width - 4

    def line(content=""):
        return f"{CYAN}|{R}  {_pad(content, content_width)}  {CYAN}|{R}"

    def top():
        return f"{CYAN}+{'-' * (width - 2)}+{R}"

    print()
    print(top())
    print(line(f"{BOLD}{WHITE}Script Launcher{R}"))
    print(line(f"{DIM}PowerShell launcher (standard-library only){R}"))
    print(top())
    print(line())
    if not scripts:
        print(line(f"{YELLOW}No .ps1 scripts found in scripts\\{R}"))
        print(line())
    else:
        for index, script in enumerate(scripts, start=1):
            star = "*" if script["id"] in favorites else " "
            show_tag = script["category"] and script["category"] != "root"
            tag = f" {DIM}[{script['category']}]{R}" if show_tag else ""
            print(line(f"{star} {YELLOW}{index}.{R} {BOLD}{script['name']}{R}{tag}"))
            print(line(f"     {DIM}{script['description']}{R}"))
            print(line())
    print(line(f"{GREEN}0.{R} {BOLD}Exit{R}"))
    print(line())
    print(top())
    print()


def run_script(script):
    try:
        script_path = validate_script(script)
    except (ValueError, FileNotFoundError) as exc:
        logger.error("Validation failed for %s: %s", script["name"], exc)
        print(f"\n{RED}[ERROR]{R} {exc}")
        return

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        logger.error("No PowerShell executable found on PATH.")
        print(f"\n{RED}[ERROR]{R} PowerShell is not available on this system.")
        return

    logger.info(
        "Starting script: %s (%s) as user %s",
        script["name"],
        script_path,
        os.environ.get("USERNAME", "unknown"),
    )
    print(f"\n{CYAN}>> Launching {BOLD}{script['name']}{R}{CYAN}...{R}")
    start = datetime.now()
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script_path)],
        )
        elapsed = (datetime.now() - start).total_seconds()
        logger.info(
            "Finished script: %s | exit_code=%s | elapsed=%.2fs",
            script["name"],
            result.returncode,
            elapsed,
        )
        if result.returncode != 0:
            print(f"\n{YELLOW}[WARN]{R} Script exited with code {result.returncode}.")
        else:
            print(f"\n{GREEN}[OK]{R} Finished in {elapsed:.2f}s.")
    except OSError as exc:
        logger.exception("Failed to launch %s", script["name"])
        print(f"\n{RED}[ERROR]{R} Failed to launch script: {exc}")


def run_cli():
    enable_vt()
    logger.info("Admin menu started (CLI mode).")
    config = load_config()
    sort_mode = config["sort"]
    favorites = set(config["favorites"])
    while True:
        scripts = sort_scripts(discover_scripts(), sort_mode, favorites)
        print_menu(scripts, favorites)
        try:
            prompt = f"{GREEN}Select an option:{R} "
            choice = input(prompt).strip()
        except EOFError:
            break

        if choice == "0":
            logger.info("Admin menu exiting.")
            print(f"\n{CYAN}Goodbye.{R}")
            break

        if not choice.isdigit():
            print(f"\n{RED}[ERROR]{R} Invalid selection. Please choose a listed option.")
            try:
                input(f"{DIM}Press Enter to continue...{R}")
            except EOFError:
                break
            continue

        index = int(choice) - 1
        if index < 0 or index >= len(scripts):
            print(f"\n{RED}[ERROR]{R} Invalid selection. Please choose a listed option.")
            try:
                input(f"{DIM}Press Enter to continue...{R}")
            except EOFError:
                break
            continue

        run_script(scripts[index])
        try:
            input(f"\n{DIM}Press Enter to return to the menu...{R}")
        except EOFError:
            break


# --------------------------------------------------
# GUI mode
# --------------------------------------------------
def run_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, scrolledtext
    except ImportError:
        print("Tkinter is not available; falling back to CLI mode.")
        run_cli()
        return

    root = tk.Tk()
    root.title("Script Launcher")
    root.geometry("640x720")
    root.minsize(520, 500)
    root.configure(bg="#0f172a")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TButton", font=("Segoe UI", 10), padding=8)
    style.configure(
        "Action.TButton",
        font=("Segoe UI", 11, "bold"),
        foreground="#f8fafc",
    )
    style.map(
        "Action.TButton",
        background=[("active", "#2563eb"), ("!disabled", "#1d4ed8")],
        foreground=[("!disabled", "#f8fafc")],
    )
    style.configure("Tool.TButton", font=("Segoe UI", 10, "bold"))

    # Header
    header = tk.Frame(root, bg="#1e293b")
    header.pack(fill="x")
    tk.Label(
        header,
        text="Script Launcher",
        font=("Segoe UI", 20, "bold"),
        fg="#f8fafc",
        bg="#1e293b",
    ).pack(padx=22, pady=(18, 2), anchor="w")
    tk.Label(
        header,
        text="PowerShell launcher  •  standard library only",
        font=("Segoe UI", 10),
        fg="#94a3b8",
        bg="#1e293b",
    ).pack(padx=22, pady=(0, 16), anchor="w")

    # Toolbar
    toolbar = tk.Frame(root, bg="#0f172a")
    toolbar.pack(fill="x", padx=22, pady=12)
    ttk.Button(toolbar, text="Refresh", command=lambda: build_buttons(),
               style="Tool.TButton").pack(side="left")
    ttk.Button(toolbar, text="Exit", command=root.destroy,
               style="Exit.TButton").pack(side="right")

    button_refs = []

    config = load_config()
    favorites = set(config["favorites"])
    sort_mode = config["sort"]

    status_var = tk.StringVar(value="Ready.")

    def append_log(message):
        text_widget.config(state="normal")
        text_widget.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        text_widget.see("end")
        text_widget.config(state="disabled")

    def set_buttons_state(state):
        for button in button_refs:
            try:
                button.config(state=state)
            except tk.TclError:
                pass

    def toggle_favorite(script):
        if script["id"] in favorites:
            favorites.discard(script["id"])
            status_var.set(f"Unpinned {script['name']}.")
        else:
            favorites.add(script["id"])
            status_var.set(f"Pinned {script['name']}.")
        config["favorites"] = sorted(favorites)
        save_config(config)
        build_buttons()

    def launch(script):
        def worker():
            def on_start():
                status_var.set(f"Launching {script['name']}...")
                set_buttons_state("disabled")
                append_log(f">> Launching {script['name']}...")

            root.after(0, on_start)
            start_time = time.time()

            try:
                powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
                if not powershell:
                    raise FileNotFoundError("PowerShell is not available on this system.")
                script_path = validate_script(script)

                logger.info(
                    "Starting script: %s (%s) as user %s",
                    script["name"],
                    script_path,
                    os.environ.get("USERNAME", "unknown"),
                )
                result = subprocess.run(
                    [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(script_path)],
                )
                elapsed = time.time() - start_time
                logger.info(
                    "Finished script: %s | exit_code=%s | elapsed=%.2fs",
                    script["name"],
                    result.returncode,
                    elapsed,
                )

                def on_done():
                    if result.returncode != 0:
                        status_var.set(f"Warning: exit code {result.returncode}")
                        append_log(f"[WARN] Exited with code {result.returncode} ({elapsed:.2f}s)")
                    else:
                        status_var.set("Finished.")
                        append_log(f"[OK] Finished in {elapsed:.2f}s")
                    set_buttons_state("normal")

                root.after(0, on_done)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to launch %s", script["name"])

                def on_error():
                    status_var.set("Error")
                    append_log(f"[ERROR] {exc}")
                    set_buttons_state("normal")

                root.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    status_bar = tk.Label(
        root,
        textvariable=status_var,
        font=("Segoe UI", 10, "bold"),
        fg="#e2e8f0",
        bg="#0f172a",
        anchor="w",
    )
    status_bar.pack(fill="x", padx=22, pady=(8, 3))

    frame = tk.Frame(root, bg="#0f172a")
    frame.pack(fill="both", expand=True, padx=22, pady=(0, 16))

    def build_buttons():
        for child in frame.winfo_children():
            child.destroy()
        button_refs.clear()
        scripts_cache = sort_scripts(discover_scripts(), sort_mode, favorites)

        if not scripts_cache:
            tk.Label(
                frame,
                text="No .ps1 scripts found in scripts\\",
                font=("Segoe UI", 11, "bold"),
                fg="#facc15",
                bg="#0f172a",
            ).pack(anchor="w", pady=8)
            return

        for index, script in enumerate(scripts_cache, start=1):
            card = tk.Frame(
                frame,
                bg="#1e293b",
                bd=0,
                highlightthickness=1,
                highlightbackground="#334155",
            )
            card.pack(fill="x", pady=5)

            star = "★" if script["id"] in favorites else "☆"
            show_tag = script["category"] and script["category"] != "root"
            tag = f"   [{script['category']}]" if show_tag else ""

            row = tk.Frame(card, bg="#1e293b")
            row.pack(fill="x", padx=12, pady=(10, 3))

            pin_button = ttk.Button(
                row,
                text=star,
                width=3,
                style="Tool.TButton",
                command=lambda current=script: toggle_favorite(current),
            )
            pin_button.pack(side="right", padx=(6, 0))

            button = ttk.Button(
                row,
                text=f"{index}.   {script['name']}{tag}",
                style="Action.TButton",
                command=lambda current=script: launch(current),
            )
            button.pack(side="left", fill="x", expand=True)
            button_refs.append(button)

            description = tk.Label(
                card,
                text=script["description"],
                font=("Segoe UI", 9),
                fg="#94a3b8",
                bg="#1e293b",
                anchor="w",
                justify="left",
                wraplength=560,
            )
            description.pack(fill="x", padx=14, pady=(0, 10))

    text_widget = scrolledtext.ScrolledText(
        root,
        height=9,
        state="disabled",
        bg="#111827",
        fg="#cbd5e1",
        insertbackground="#f8fafc",
        font=("Consolas", 9),
        wrap="word",
        relief="flat",
        bd=0,
    )
    # Move text below status; repack order: remove and re-add at bottom
    text_widget.pack_forget()
    # We'll pack after toolbar? Instead pack now at bottom
    text_widget.pack(fill="both", expand=False, padx=22, pady=(0, 16))

    build_buttons()
    append_log("Admin menu started (GUI mode).")

    # Bind refresh button callback
    for widget in toolbar.winfo_children():
        if isinstance(widget, ttk.Button) and widget.cget("text") == "Refresh":
            widget.config(command=build_buttons)

    root.mainloop()


def run_tui():
    if os.name != "nt" or not HAS_MSVCRT:
        print("Interactive TUI requires a Windows console; starting CLI mode.")
        run_cli()
        return

    config = load_config()
    selected = 0
    scripts = discover_scripts()
    query = config["last_filter"]
    sort_mode = config["sort"]
    favorites = set(config["favorites"])
    focus_id = None
    search_mode = False
    message = "Ready."
    message_ok = True

    def persist():
        config["last_filter"] = query
        config["sort"] = sort_mode
        config["favorites"] = sorted(favorites)
        save_config(config)

    def read_key():
        ch = msvcrt.getch()
        if ch == b"\x03":  # Ctrl+C
            return "quit"
        if ch in (b"\x00", b"\xe0"):
            ch2 = msvcrt.getch()
            mapping = {
                b"H": "up",
                b"P": "down",
                b"K": "left",
                b"M": "right",
                b"I": "pageup",
                b"Q": "pagedown",
                b"G": "home",
                b"O": "end",
            }
            return mapping.get(ch2)
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b"\x1b":
            return "esc"
        if ch in (b"\x08", b"\x7f"):
            return "backspace"
        try:
            text = ch.decode("utf-8", errors="ignore")
        except Exception:
            return None
        if not text:
            return None
        return ("char", text)

    def wait_key(current_size):
        """Block for a keypress, but return early when the terminal is resized."""
        while True:
            if msvcrt.kbhit():
                return read_key()
            if shutil.get_terminal_size((80, 24)) != current_size:
                return "resize"
            time.sleep(0.1)

    def matches(script):
        q = query.lower()
        if not q:
            return True
        return (
            q in script["name"].lower()
            or q in script["description"].lower()
            or q in script["file"].lower()
            or q in script["category"].lower()
        )

    def execute(script):
        nonlocal message, message_ok
        clear_screen()
        width = terminal_box_width()
        print(f"{BOLD}{WHITE}{'Launching ' + script['name']:^{width}}{R}")
        print(f"{CYAN}{'-' * width}{R}")
        print()

        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            message = "[ERROR] PowerShell is not available on this system."
            message_ok = False
            return
        try:
            script_path = validate_script(script)
            start_time = time.time()
            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(script_path)],
            )
            elapsed = time.time() - start_time
            if result.returncode != 0:
                message = f"[WARN] {script['name']} exited with code {result.returncode} ({elapsed:.2f}s)."
                message_ok = False
            else:
                message = f"[OK] {script['name']} finished in {elapsed:.2f}s."
                message_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to launch %s", script["name"])
            message = f"[ERROR] {exc}"
            message_ok = False

    running = True
    while running:
        size = shutil.get_terminal_size((80, 24))
        rows = size.lines
        width = terminal_box_width()
        items_per_page = max(1, (rows - 10) // 2)

        ordered = sort_scripts(scripts, sort_mode, favorites)
        visible = [script for script in ordered if matches(script)]
        total = len(visible)
        pages = max(1, (total + items_per_page - 1) // items_per_page)
        if focus_id:
            for index, script in enumerate(visible):
                if script["id"] == focus_id:
                    selected = index
                    break
            focus_id = None
        if total:
            selected = min(selected, total - 1)
            page = min(selected // items_per_page, pages - 1)
        else:
            selected = 0
            page = 0
        start = page * items_per_page
        end = min(start + items_per_page, total)

        clear_screen()
        rule = f"{CYAN}{'-' * width}{R}"
        print(f"{BOLD}{WHITE}{'Script Launcher'.center(width)}{R}")
        print(rule)

        if query:
            count_label = f'{total} of {len(scripts)} match "{query}"'
        else:
            count_label = f"{total} script{'s' if total != 1 else ''} available"
        if pages > 1:
            count_label = f"{count_label}   (Page {page + 1}/{pages})"
        print(f"{DIM}{count_label.center(width)}{R}")
        print()

        if total == 0:
            empty = "No matching scripts." if query else "No .ps1 scripts found in scripts\\"
            print(f"{YELLOW}{empty.center(width)}{R}")
        else:
            for rank in range(start, end):
                script = visible[rank]
                marker = ">" if rank == selected else " "
                star = "*" if script["id"] in favorites else " "
                show_tag = script["category"] and script["category"] != "root"
                tag = f"  [{script['category']}]" if show_tag else ""
                line = f" {star}{marker} {rank + 1}. {script['name']}{tag}"
                if rank == selected:
                    print(f"{REVERSE}{line.center(width)[:width]}{R}")
                else:
                    print(line.center(width)[:width])
                description = f"       {script['description']}"
                print(f"{DIM}{description.center(width)[:width]}{R}")

        print()
        print(rule)
        if search_mode:
            print(f"{CYAN}{f'Search: {query}_'[:width]}{R}")
        elif query:
            print(f"{DIM}{f'Filter: {query}   (/ edit  Esc clear)'[:width]}{R}")
        help_line_1 = "Up/Down Move   PgUp/PgDn Page   Home/End   / Search"
        help_line_2 = f"F Pin/Unpin   S Sort [{sort_mode}]   Enter Run   R Refresh   Q Quit"
        print(f"{DIM}{help_line_1.center(width)[:width]}{R}")
        print(f"{DIM}{help_line_2.center(width)[:width]}{R}")
        if message:
            color = GREEN if message_ok else (YELLOW if message.startswith("[WARN]") else RED)
            print(f"{color}{message[:width]}{R}")

        sys.stdout.flush()
        key = wait_key(size)
        if key == "resize":
            continue

        if search_mode:
            if key == "quit":
                running = False
            elif key == "enter":
                search_mode = False
                message = f'Filter applied: "{query}".' if query else "Search cleared."
                persist()
                message_ok = True
            elif key == "esc":
                query = ""
                search_mode = False
                selected = 0
                message = "Search cleared."
                persist()
                message_ok = True
            elif key == "backspace":
                query = query[:-1]
                selected = 0
            elif isinstance(key, tuple) and key[0] == "char":
                char = key[1]
                if char.isprintable():
                    query += char
                    selected = 0
            continue

        if key == "quit":
            running = False
        elif key == "up" and total:
            selected = (selected - 1) % total
        elif key == "down" and total:
            selected = (selected + 1) % total
        elif key == "pageup" and total:
            selected = (selected - items_per_page) % total
        elif key == "pagedown" and total:
            selected = (selected + items_per_page) % total
        elif key == "home" and total:
            selected = 0
        elif key == "end" and total:
            selected = total - 1
        elif key == "enter" and total:
            execute(visible[selected])
        elif key == "esc":
            if query:
                query = ""
                selected = 0
                message = "Filter cleared."
                persist()
                message_ok = True
        elif isinstance(key, tuple) and key[0] == "char":
            char = key[1]
            if char == "/":
                search_mode = True
                message = "Type to filter. Enter to keep, Esc to clear."
                message_ok = True
            elif char == "0" or char.lower() == "q":
                running = False
            elif char.lower() == "r":
                scripts = discover_scripts()
                selected = 0
                message = f"Refreshed script list ({len(scripts)} found)."
                message_ok = True
            elif char.lower() == "f" and total:
                current = visible[selected]
                if current["id"] in favorites:
                    favorites.discard(current["id"])
                    message = f"Unpinned {current['name']}."
                else:
                    favorites.add(current["id"])
                    message = f"Pinned {current['name']}."
                focus_id = current["id"]
                persist()
                message_ok = True
            elif char.lower() == "s":
                sort_mode = SORT_MODES[(SORT_MODES.index(sort_mode) + 1) % len(SORT_MODES)]
                if total:
                    focus_id = visible[selected]["id"]
                persist()
                message = f"Sort: {sort_mode}."
                message_ok = True
            elif char.isdigit() and char != "0" and total:
                number = int(char)
                if 1 <= number <= total:
                    selected = number - 1
                    execute(visible[selected])
        elif key is None:
            time.sleep(0.05)

    persist()
    clear_screen()
    print(f"{CYAN}Goodbye.{R}")


def main():
    if "--gui" in sys.argv:
        run_gui()
        return
    if "--cli" in sys.argv:
        run_cli()
        return
    if os.name != "nt" or not HAS_MSVCRT:
        print("Interactive TUI requires a Windows console; starting CLI mode.")
        run_cli()
        return
    run_tui()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye.")
        sys.exit(0)
