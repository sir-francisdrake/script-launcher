# Script Launcher

A small, dependency-free launcher for your personal PowerShell tools. Drop `.ps1`
files into `scripts\`, and they show up automatically in a keyboard-driven menu.
No registry, no config edits — just add files.

- **Standard library only** (Python) — no `pip install` needed.
- **Dynamic discovery** — new scripts appear on the next launch (or press `R` to refresh).
- **True TUI** by default, with paging and search for large collections.
- **Optional GUI** (`--gui`) and plain text mode (`--cli`).
- **Scripts self-elevate** via UAC when they need administrator rights.

---

## Requirements

| Component | Notes |
|-----------|-------|
| Windows | 10 / 11 / Server |
| Python | 3.9 or newer (launcher only; uses the standard library) |
| PowerShell | `powershell.exe` (Windows PowerShell 5.1) or `pwsh.exe` (PowerShell 7+) |

The launcher runs scripts with:

```
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<script>.ps1"
```

It prefers `powershell.exe` and falls back to `pwsh.exe`.

---

## Running

```powershell
python admin_menu.py          # default: arrow-key TUI
python admin_menu.py --cli    # plain numbered text menu
python admin_menu.py --gui    # Tkinter window (optional)
```

### TUI controls

| Key | Action |
|-----|--------|
| `Up` / `Down` | Move the selection |
| `PgUp` / `PgDn` | Move a page at a time |
| `Home` / `End` | Jump to the first / last item |
| `/` | Search (filter by name, description, file, or category) |
| `Enter` | Run the selected script |
| `1`–`9` | Quick-run the nth item in the current (filtered) list |
| `F` | Pin / unpin the selected script (favorites) |
| `S` | Cycle sort order: `category` → `name` → `modified` |
| `R` | Rescan `scripts\` for new or removed files |
| `Q` or `0` | Quit |
| `Ctrl+C` | Quit |

Search: press `/`, type to filter, `Enter` to keep the filter, `Esc` to clear it.
Favorites always sort to the top and are marked with `*` (TUI/CLI) or ★ (GUI).

---

## Folder layout

```
admin_menu.py        The launcher
config.json          Saved preferences (created automatically)
logs\                Daily log files
scripts\             Your PowerShell scripts live here
  disk_space.ps1
  network_check.ps1
  system_info.ps1
  network\           (optional) subfolders become categories/tags
    ping.ps1
```

Discovery is **recursive**: any `.ps1` file anywhere under `scripts\` is picked up.
A script's **category** is its path relative to `scripts\`:

| File | Category tag |
|------|--------------|
| `scripts\disk_space.ps1` | *(none — top-level)* |
| `scripts\network\ping.ps1` | `[network]` |
| `scripts\network\deep\trace.ps1` | `[network/deep]` |

Search matches category names too (typing `network` finds everything under `network\`).

---

## Creating a script

### 1. Add the metadata header

The launcher reads two header comments — **case-insensitive**, anywhere in the
**first 20 lines** of the file:

```powershell
# Name: Display Name Shown In Menu
# Description: One short line describing what this does
```

- **Name** — how the entry appears in the menu.
- **Description** — the dim subtitle under the name.

If omitted, the launcher falls back to:

- **Name**: the filename with `_`/`-` replaced by spaces, Title Cased
  (`check_dns.ps1` → `Check Dns`).
- **Description**: `Run <filename>.ps1`.

Metadata is sanitized (ANSI escapes and control characters stripped, truncated to
160 chars) before display, so unusual file contents can't corrupt the menu.

### 2. Minimal template

Save as `scripts\hello.ps1`:

```powershell
# Name: Hello World
# Description: Example script for the launcher

Write-Host "`n=== Hello from the launcher ===" -ForegroundColor Cyan
Write-Host "Current user: $env:USERDOMAIN\$env:USERNAME"

Read-Host "`nPress Enter to close this window"
```

### 3. Template that needs administrator rights

Most useful admin tasks should request elevation themselves. Add this block
right after the metadata header, **before** any work:

```powershell
# Name: My Admin Task
# Description: Does something that requires elevation

# Self-elevate to administrator if needed
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not running as administrator. Requesting elevation (UAC)..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

# ... your admin work here ...

Read-Host "`nPress Enter to close this window"
```

That is the exact pattern used by the bundled scripts. Notes:

- Only include this block if the script actually needs admin. Read-only scripts
  can run as the current user.
- The elevated run happens in a **new window**. The original process launches it
  and exits immediately, so the launcher's "finished in 0.0s" message refers to
  the dispatcher — the elevated window keeps running on its own.
- `-Verb RunAs` triggers the UAC prompt; the user can cancel it.

### 4. Conventions and tips

- **End interactive scripts with `Read-Host "`nPress Enter to close this window"`**
  so output stays on screen instead of the window closing instantly.
- **Use `$PSScriptRoot`** for any files next to the script. The working directory
  is not guaranteed to be the `scripts\` folder. For example:
  ```powershell
  $dataFile = Join-Path $PSScriptRoot "targets.txt"
  ```
- **No arguments.** The launcher runs scripts with no parameters. Hard-code
  sensible defaults or read a small config file next to the script.
- **Exit codes matter.** A non-zero exit code is reported as a warning:
  `[WARN] <name> exited with code N`. Use `exit 1` to signal failure.
- **Keep names short** so they fit on one line in the menu; the width adapts to
  the terminal (40–90 columns).
- **Error handling** is your friend — an unhandled terminating error will surface
  as an error message and a non-zero exit code.

### 5. Verify

Run the launcher and press `R` to rescan (or just restart it). Your script should
appear with its name, description, and category tag.

---

## Favorites and preferences

Preferences are stored in `config.json`:

```json
{
  "last_filter": "",
  "sort": "category",
  "favorites": [
    "disk_space.ps1",
    "network/ping.ps1"
  ]
}
```

| Field | Meaning |
|-------|---------|
| `last_filter` | The search query restored on next launch |
| `sort` | `category`, `name`, or `modified` |
| `favorites` | Pinned scripts, keyed by path relative to `scripts\` (forward slashes) |

- Favorite keys are **relative paths**, so moving a script requires updating its
  entry (or just re-pin it).
- A corrupt or unreadable `config.json` is ignored; defaults are used and a
  warning is written to the log.
- Delete the file to reset all preferences.

---

## Logging

Each launcher run appends to `logs\admin_menu_YYYYMMDD.log` (one file per day),
recording script starts, finishes, exit codes, and elapsed time. Useful for
answering "what did I run, and did it work?"

---

## Security notes

- Only `.ps1` files **inside `scripts\`** are ever executed. Each launch
  re-resolves the path and verifies it is contained in `scripts\`, blocking path
  traversal.
- Metadata and category text are sanitized before being written to the terminal,
  preventing escape-sequence injection from crafted script headers or folder names.
- Scripts run with `-ExecutionPolicy Bypass` for that process only. **Treat
  everything in `scripts\` as trusted code** — it executes with your privileges,
  and self-elevating scripts run as administrator after a UAC prompt.
- Consider restricting write access to `scripts\` so other users can't drop
  scripts into it.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Script doesn't appear | Not a `.ps1`, or outside `scripts\`. Press `R` to rescan. |
| Name shows as filename | `# Name:` missing or past line 20, or misspelled. |
| "finished in 0.0s" but nothing happened | Script self-elevated; look for the separate elevated window. |
| Window closes instantly | Add `Read-Host` at the end. |
| "execution of scripts is disabled" | Shouldn't happen (`-ExecutionPolicy Bypass` is set); check group policy. |
| TUI looks wrong | Resize the window — it redraws on resize. Minimum sensible height ~12 rows. |
| `ImportError: msvcrt` | The TUI needs a Windows console; `--cli` or `--gui` still work. |

---

## Bundled example scripts

| Script | Category | Description |
|--------|----------|-------------|
| `disk_space.ps1` | — | Free/used space per fixed drive |
| `network_check.ps1` | — | DNS resolution and TCP connectivity checks |
| `system_info.ps1` | — | Basic OS, hardware, and PowerShell version info |

All three demonstrate the metadata header and the self-elevation pattern.
