import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


@dataclass
class KonsoleTab:
    service: str
    session_id: int
    window_path: str
    title: str
    command: str


@dataclass
class KonsoleWindow:
    service: str
    window_path: str
    pid: str
    tabs: list[KonsoleTab] = field(default_factory=list)


def _run(args: list[str], timeout: float = 2.0) -> str:
    """Run a command and return stripped stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def list_konsole_services() -> list[str]:
    output = _run(["qdbus6"])
    if not output:
        return []
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("org.kde.konsole-")
    ]


def _list_object_paths(service: str) -> list[str]:
    output = _run(["qdbus6", service])
    if not output:
        return []
    return [line.strip() for line in output.splitlines()]


def list_windows(service: str) -> list[str]:
    paths = _list_object_paths(service)
    return [p for p in paths if p.startswith("/Windows/")]


def list_sessions_for_window(service: str, window_path: str) -> list[int]:
    output = _run([
        "qdbus6", service, window_path,
        "org.kde.konsole.Window.sessionList",
    ])
    if not output:
        return []
    result = []
    for line in output.splitlines():
        line = line.strip()
        if line.isdigit():
            result.append(int(line))
    return result


def get_session_title(service: str, session_id: int) -> str:
    return _run([
        "qdbus6", service, f"/Sessions/{session_id}",
        "org.kde.konsole.Session.title", "1",
    ])


def get_foreground_pid(service: str, session_id: int) -> int:
    output = _run([
        "qdbus6", service, f"/Sessions/{session_id}",
        "org.kde.konsole.Session.foregroundProcessId",
    ])
    try:
        return int(output)
    except ValueError:
        return -1



def _get_all_commands(pids: list[int]) -> dict[int, str]:
    """Batch-fetch commands for all PIDs in one ps call."""
    valid = [p for p in pids if p > 0]
    if not valid:
        return {}
    pid_args = ",".join(str(p) for p in valid)
    output = _run(["ps", "-p", pid_args, "-o", "pid=,args="])
    result = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            try:
                result[int(parts[0])] = parts[1]
            except ValueError:
                pass
    return result


def _fetch_session_info(service: str, session_id: int) -> tuple[int, str, int]:
    """Fetch title and fg PID for a single session (runs in thread pool)."""
    title = get_session_title(service, session_id)
    fg_pid = get_foreground_pid(service, session_id)
    return session_id, title, fg_pid


def get_all_tabs() -> list[KonsoleWindow]:
    windows = []
    # Collect the structure first (few calls)
    session_jobs: list[tuple[str, str, list[int]]] = []
    for service in list_konsole_services():
        for win_path in list_windows(service):
            session_ids = list_sessions_for_window(service, win_path)
            session_jobs.append((service, win_path, session_ids))

    # Fetch all session titles + fg PIDs in parallel
    all_results: dict[tuple[str, int], tuple[str, int]] = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {}
        for service, _, session_ids in session_jobs:
            for sid in session_ids:
                key = (service, sid)
                futures[pool.submit(_fetch_session_info, service, sid)] = key
        for future in futures:
            sid, title, fg_pid = future.result()
            key = futures[future]
            all_results[key] = (title, fg_pid)

    # Batch-fetch all commands in one ps call
    all_pids = [fg_pid for title, fg_pid in all_results.values()]
    commands = _get_all_commands(all_pids)

    # Build the result
    for service, win_path, session_ids in session_jobs:
        pid = service.removeprefix("org.kde.konsole-")
        win = KonsoleWindow(service=service, window_path=win_path, pid=pid)
        for sid in session_ids:
            title, fg_pid = all_results.get((service, sid), ("", -1))
            win.tabs.append(KonsoleTab(
                service=service,
                session_id=sid,
                window_path=win_path,
                title=title,
                command=commands.get(fg_pid, ""),
            ))
        windows.append(win)
    return windows


def set_current_session(service: str, window_path: str, session_id: int) -> None:
    _run([
        "qdbus6", service, window_path,
        "org.kde.konsole.Window.setCurrentSession", str(session_id),
    ])


def _run_kwin_script(js: str, name: str) -> None:
    """Load, run, and clean up a KWin script."""
    _run([
        "qdbus6", "org.kde.KWin", "/Scripting",
        "org.kde.kwin.Scripting.unloadScript", name,
    ])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(js)
        script_path = f.name

    try:
        script_id = _run([
            "qdbus6", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.loadScript", script_path, name,
        ])
        if script_id and script_id != "-1":
            _run([
                "qdbus6", "org.kde.KWin", f"/Scripting/Script{script_id}",
                "org.kde.kwin.Script.run",
            ])
            _run([
                "qdbus6", "org.kde.KWin", f"/Scripting/Script{script_id}",
                "org.kde.kwin.Script.stop",
            ])
        _run([
            "qdbus6", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.unloadScript", name,
        ])
    finally:
        os.unlink(script_path)


def raise_window(pid: int, candidates: list[str]) -> None:
    """Raise a Konsole window on Wayland via KWin scripting.

    `candidates` is ordered most-specific-first. Each candidate is tried
    as a caption substring across all of the given pid's windows before
    moving on to the next, so a more specific caption always wins over a
    less specific one regardless of window order. Only falls back to
    "first window of pid" when that pid has exactly one window, to avoid
    raising the wrong window when a Konsole process owns several.
    """
    escaped = [c.replace("\\", "\\\\").replace('"', '\\"') for c in candidates if c]
    js_arr = "[" + ",".join(f'"{s}"' for s in escaped) + "]"
    _run_kwin_script(f"""\
var candidates = {js_arr};
var windows = workspace.windowList();
var pidMatches = [];
for (var i = 0; i < windows.length; i++) {{
    if (windows[i].pid === {pid}) pidMatches.push(windows[i]);
}}
// Match by candidate specificity, not window order: try the most specific
// candidate against every window before falling back to a less specific one.
// (Otherwise a generic candidate like "claude" matches whichever Konsole
// window happens to come first in workspace order — the wrong one.)
var target = null;
for (var j = 0; j < candidates.length && target === null; j++) {{
    for (var i = 0; i < pidMatches.length; i++) {{
        if (pidMatches[i].caption.indexOf(candidates[j]) !== -1) {{
            target = pidMatches[i];
            break;
        }}
    }}
}}
if (target === null && pidMatches.length === 1) target = pidMatches[0];
if (target) workspace.activeWindow = target;
""", "FocusKonsole")



def activate_tab(tab: KonsoleTab) -> None:
    """Raise the Konsole window and then switch to the target tab.

    Order matters: KWin updates its cached caption lazily (only on
    window activation), so we raise first — matching on the title
    of whichever session is currently active in the target window —
    and switch tabs afterwards.
    """
    try:
        pid = int(tab.service.removeprefix("org.kde.konsole-"))
    except ValueError:
        return

    current_sid_str = _run([
        "qdbus6", tab.service, tab.window_path,
        "org.kde.konsole.Window.currentSession",
    ])
    try:
        current_sid = int(current_sid_str)
    except ValueError:
        current_sid = tab.session_id

    # title(1) is the displayed title — the string KWin uses as the window
    # caption, so it's the reliable disambiguator. title(0) (the NameRole) is
    # often a generic constant shared across many windows (e.g. every Claude
    # tab reports "claude"), which would match the wrong window; only fall back
    # to it when there is no displayed title at all.
    candidates: list[str] = []
    displayed = _run([
        "qdbus6", tab.service, f"/Sessions/{current_sid}",
        "org.kde.konsole.Session.title", "1",
    ])
    if displayed:
        candidates.append(displayed)
    else:
        name = _run([
            "qdbus6", tab.service, f"/Sessions/{current_sid}",
            "org.kde.konsole.Session.title", "0",
        ])
        if name:
            candidates.append(name)

    raise_window(pid, candidates)
    set_current_session(tab.service, tab.window_path, tab.session_id)
