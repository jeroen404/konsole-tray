# konsole-tray

A small KDE system tray app that gives you a spotlight-style search window
for finding and jumping to any open Konsole tab across all your Konsole
windows. Search by tab title or by the command currently running in that
tab (so `ssh foo` tabs are easy to spot). Clicking a result raises the
containing Konsole window and switches to that tab.

## Compatibility

Only works on KDE Plasma with KWin. Only tested on Wayland (Plasma 6). It
may work on X11 / Plasma 5 but nothing has been checked there.

## Build a .deb

```
dpkg-buildpackage -us -uc -b
```

## Making remote tab titles useful

The tray search shows each tab's title and its currently-running command.
For local tabs Konsole produces titles like `404.eu: nvtop` out of the
box. For remote (SSH) sessions you need to help it along by having the
remote shell emit the tab title via escape sequences.

### Remote `~/.bashrc`

```bash
case $TERM in
    xterm*|rxvt*|konsole*)
        PROMPT_COMMAND='printf "\033]0;%s: bash\007" "${HOSTNAME%%.*}"'
        trap 'printf "\033]0;%s: %s\007" "${HOSTNAME%%.*}" "${BASH_COMMAND%% *}"' DEBUG
        ;;
esac
```

`PROMPT_COMMAND` resets the title to `host: bash` when the prompt
returns; the `DEBUG` trap updates it to `host: <cmd>` each time a command
runs. `${BASH_COMMAND%% *}` strips arguments — drop that suffix if you
want the whole command line in the title.

### Konsole profile

Settings → Edit Current Profile → Tabs → **Remote Tab Title Format**: set
it to `%w`. That tells Konsole to display whatever title the remote shell
set via escape sequence, instead of its default `(%u) %H` format.

You don't need a separate remote profile — Konsole auto-switches between
"local" and "remote" tab title formats within the same profile based on
whether it's receiving title escape sequences.

## How it works

Everything happens through D-Bus — it doesn't link against any KDE
libraries, it just shells out to `qdbus6`.

### Enumerating tabs

1. List bus names starting with `org.kde.konsole-` — each running
   Konsole process registers one, with its pid as a suffix.
2. For each service, enumerate object paths and keep the ones matching
   `/Windows/N` — one path per Konsole window.
3. For each window, call `org.kde.konsole.Window.sessionList` to get the
   session ids of its tabs.
4. For each session, fetch `Session.title(1)` (the tab title) and
   `Session.foregroundProcessId` in parallel across a thread pool.
5. Finally, one batched `ps -p <all pids> -o pid=,args=` resolves every
   foreground PID to a command line — this is what lets you search tabs
   by the command running in them, and turns the slow per-session `ps`
   into a single call.

### Activating a tab

Two D-Bus calls, one KWin script:

1. `Window.currentSession()` on the target window to learn which session
   is *currently* on top there.
2. Fetch that session's `title(1)` and `title(0)` — these are what KWin
   has cached as the window caption right now.
3. Run a KWin script via `org.kde.KWin /Scripting` that walks
   `workspace.windowList()`, filters by `window.pid`, matches the
   caption against the candidates, and sets `workspace.activeWindow`.
   The script has a fallback: if no caption matches and the pid owns
   exactly one window, raise that one — otherwise do nothing (better
   than picking the wrong window).
4. Once the window is active, call `Window.setCurrentSession(targetId)`
   to switch to the desired tab.

The KWin script itself is written to a temp file, loaded via
`Scripting.loadScript`, run, stopped, and unloaded — that's the only
interface KWin gives scripts from outside.

### Why raise first, then switch

Konsole updates its internal tab state the moment `setCurrentSession`
arrives, but it only pushes the new window caption to KWin when the
window is actually activated. If the tool switched tabs first and then
tried to match the window by the new tab's title, KWin's cached caption
would still reflect the previously active tab, and the right window
couldn't be found. Raising first — matching on whatever caption KWin
actually has — sidesteps that race entirely.
