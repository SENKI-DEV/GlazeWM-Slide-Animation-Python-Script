"""
glaze_slide_hotkeys.py - slide-out + slide-in animation for GlazeWM workspace
switches using Alt+1 ... Alt+9 (Windows only).

This script OWNS the Alt+1..9 hotkeys using its own keyboard hook. Remove those
"focus workspace" bindings from GlazeWM's config.yaml, and start this script
AFTER GlazeWM (a hook installed later sees keys first).

Flow when you press Alt+N:
  1. Slide the current workspace's windows out.
  2. Tell GlazeWM to focus workspace N (over its IPC websocket).
  3. Put the old windows back at their real positions (GlazeWM has hidden them).
  4. Slide the new workspace's windows in.

Setup:
    pip install websockets
    python glaze_slide_hotkeys.py        (pythonw.exe to run without a console)

Assumes workspaces are named "1".."9" (the GlazeWM default) and a single monitor.
"""

import asyncio
import ctypes
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from ctypes import wintypes

import websockets

# ----------------------------- settings ------------------------------------
URI = "ws://127.0.0.1:6123"   # GlazeWM IPC default

OUT_DURATION = 0.12           # seconds for the slide-out
OUT_DISTANCE = 0.30           # fraction of workspace width
IN_DURATION = 0.22            # seconds for the slide-in
IN_DISTANCE = 0.40            # fraction of workspace width

# The revolver overlay connects here (localhost only) when you click or scroll it, so those
# switches get the same slide animation as Alt+1..9. Set to 0 to turn this off.
CONTROL_PORT = 48721

DEBUG = False

# Command formats to try, in order (GlazeWM changed its command syntax between
# versions). The first one that works is remembered.
FOCUS_FORMATS = [
    "command focus --workspace {n}",
    'command "focus workspace {n}"',
    'command "focus --workspace {n}"',
]
# ---------------------------------------------------------------------------

if sys.platform != "win32":
    sys.exit("This script only runs on Windows.")

user32 = ctypes.windll.user32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass
try:
    _dwm_flush = ctypes.windll.dwmapi.DwmFlush
except Exception:
    _dwm_flush = None

# Named event watched by the window animator (start_glaze_slide.bat) so it
# doesn't try to animate our slide moves. Signalled while a slide is running.
_kernel32 = ctypes.windll.kernel32
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = ctypes.c_void_p
_kernel32.SetEvent.argtypes = [ctypes.c_void_p]
_kernel32.ResetEvent.argtypes = [ctypes.c_void_p]
def _open_event(kernel32, name):
    """Create/open the named event with an open (NULL) DACL, so an elevated and a normal process can both use it."""
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        class SA(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                        ("bInheritHandle", wintypes.BOOL)]
        advapi32.InitializeSecurityDescriptor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        advapi32.SetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                                       ctypes.c_void_p, wintypes.BOOL]
        sd = ctypes.create_string_buffer(256)
        if advapi32.InitializeSecurityDescriptor(sd, 1) and advapi32.SetSecurityDescriptorDacl(sd, True, None, False):
            sa = SA(ctypes.sizeof(SA), ctypes.addressof(sd), False)
            handle = kernel32.CreateEventW(ctypes.addressof(sa), True, False, name)
            if handle:
                return handle
    except Exception:
        pass
    return kernel32.CreateEventW(None, True, False, name)


_slide_event = _open_event(_kernel32, "GlazeWM_Slide_Active")

# Second channel for the same signal, in case the named event can't be shared between the two
# processes (e.g. one is elevated). The animator reads this file as well.
_FLAG_FILE = os.path.join(tempfile.gettempdir(), "glaze_slide_active.flag")


def slide_flag(on):
    if on:
        _kernel32.SetEvent(_slide_event)
    else:
        _kernel32.ResetEvent(_slide_event)
    try:
        with open(_FLAG_FILE, "w") as f:
            f.write("1" if on else "0")
    except OSError:
        pass

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
FLAGS = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE

MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

user32.BeginDeferWindowPos.argtypes = [ctypes.c_int]
user32.BeginDeferWindowPos.restype = ctypes.c_void_p
user32.DeferWindowPos.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
user32.DeferWindowPos.restype = ctypes.c_void_p
user32.EndDeferWindowPos.argtypes = [ctypes.c_void_p]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]


def log(*args):
    if DEBUG:
        print("[glaze_slide]", *args, flush=True)


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in(t):
    return t ** 3


# ----------------------------- window helpers ------------------------------

def collect_handles(node, out):
    if node.get("type") == "window" and "handle" in node:
        out.append(node["handle"])
    for child in node.get("children", []):
        collect_handles(child, out)


def get_origins(handles):
    origins = {}
    for h in handles:
        rect = wintypes.RECT()
        if user32.IsWindow(h) and user32.GetWindowRect(h, ctypes.byref(rect)):
            origins[h] = (rect.left, rect.top)
    return origins


def restore(origins):
    for h, (x, y) in origins.items():
        if user32.IsWindow(h):
            user32.SetWindowPos(h, None, x, y, 0, 0, FLAGS)


def _place_all(origins, off):
    """Move every window one by one. Slower than a batch, but one bad window can't spoil the rest."""
    for h, (x, y) in origins.items():
        if user32.IsWindow(h):
            user32.SetWindowPos(h, None, x + off, y, 0, 0, FLAGS)


def animate(origins, start_off, end_off, duration, ease, abort=None):
    """Move every window horizontally from origin+start_off to origin+end_off.

    Always finishes with every window at exactly origin+end_off, even if a window
    vanished, refused to move (elevated / hung), or `abort()` says a newer key
    press is waiting. Previously an early return could leave windows stuck
    partway across the screen.
    """
    if not origins:
        return
    use_defer = True
    t0 = time.perf_counter()
    while True:
        t = min((time.perf_counter() - t0) / duration, 1.0)
        if abort is not None and abort():
            t = 1.0                              # a newer press is waiting: jump to the end
        if t >= 1.0:
            _place_all(origins, end_off)         # exact final positions, never batched
            return
        off = int(start_off + (end_off - start_off) * ease(t))

        done = False
        if use_defer:
            hdwp = user32.BeginDeferWindowPos(len(origins))
            ok = bool(hdwp)
            if ok:
                for h, (x, y) in origins.items():
                    if not user32.IsWindow(h):
                        continue
                    hdwp = user32.DeferWindowPos(hdwp, h, None, x + off, y, 0, 0, FLAGS)
                    if not hdwp:                 # handle is invalid after a failure: don't End it
                        ok = False
                        break
                if ok:
                    user32.EndDeferWindowPos(hdwp)
                    done = True
            if not ok:
                use_defer = False                # fall back to one-by-one for the rest
        if not done:
            _place_all(origins, off)

        if _dwm_flush is not None:
            _dwm_flush()
        else:
            time.sleep(0.004)


async def settled_origins(handles, timeout=0.08):
    """Window positions once GlazeWM has finished placing them (two identical reads in a row)."""
    prev = get_origins(handles)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        await asyncio.sleep(0.016)
        cur = get_origins(handles)
        if cur == prev:
            return cur
        prev = cur
    return prev


# ----------------------------- GlazeWM IPC ---------------------------------

async def send(ws, text):
    await ws.send(text)
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("messageType") == "client_response":
            return msg


async def query_workspaces(ws):
    msg = await send(ws, "query workspaces")
    return (msg.get("data") or {}).get("workspaces", [])


_focus_fmt = {"value": None}


async def focus_workspace(ws, n):
    fmts = FOCUS_FORMATS if _focus_fmt["value"] is None else [_focus_fmt["value"]]
    for fmt in fmts:
        resp = await send(ws, fmt.format(n=n))
        if resp.get("success", True) and not resp.get("error"):
            _focus_fmt["value"] = fmt
            return True
        log("command rejected:", fmt.format(n=n), resp.get("error"))
    print("[glaze_slide] GlazeWM rejected every focus command format; "
          "edit FOCUS_FORMATS to match your config.yaml.", flush=True)
    return False


def ws_number(w):
    try:
        return int(w.get("name"))
    except (TypeError, ValueError):
        return 0


async def run_blocking(fn, *args):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, fn, *args)


async def handle(ws, n, queue):
    target_name = str(n)
    workspaces = await query_workspaces(ws)
    current = next((w for w in workspaces if w.get("hasFocus")), None)
    target = next((w for w in workspaces if w.get("name") == target_name), None)

    if current is None or current.get("name") == target_name:
        await focus_workspace(ws, n)
        return

    width = current.get("width") or 1920
    direction = 1 if n > ws_number(current) else -1

    out_handles, in_handles = [], []
    collect_handles(current, out_handles)
    if target is not None:
        collect_handles(target, in_handles)

    def abort():
        return not queue.empty()          # a newer key press is waiting: finish this one instantly

    out_origins = get_origins(out_handles)
    in_origins = {}
    log(f"{current.get('name')} -> {target_name} "
        f"(out {len(out_origins)}, in {len(in_handles)})")

    try:
        # 1. slide the current workspace out
        await run_blocking(animate, out_origins, 0,
                           -direction * int(width * OUT_DISTANCE), OUT_DURATION, ease_in, abort)

        # 2. let GlazeWM do the real switch
        await focus_workspace(ws, n)

        # 3. old windows are hidden now; put them back where GlazeWM expects them
        restore(out_origins)

        # 4. slide the new workspace in (skipped when more key presses are already queued)
        in_origins = await settled_origins(in_handles)
        if in_origins and not abort():
            await run_blocking(animate, in_origins,
                               direction * int(width * IN_DISTANCE), 0, IN_DURATION, ease_out, abort)
    except BaseException:
        restore(out_origins)              # never leave windows displaced if anything goes wrong
        restore(in_origins)
        raise


# ----------------------------- hotkeys -------------------------------------

# Low-level keyboard hook. Hooks installed later run first, so as long as this
# script starts AFTER GlazeWM, it sees Alt+1..9 before GlazeWM's own hook does.
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_ALTDOWN = 0x20
LLKHF_INJECTED = 0x10
KEYEVENTF_KEYUP = 0x0002
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
ctypes.windll.kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_swallowed = set()     # digit keys whose key-down we swallowed (so we swallow key-up too)
_hook_ref = {}         # keeps the callback alive


def _key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def hotkey_thread(loop, queue):
    """Installs the keyboard hook and forwards Alt+1..9 presses to the asyncio queue."""

    def proc(nCode, wParam, lParam):
        try:
            if nCode == 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = kb.vkCode
                if 0x31 <= vk <= 0x39 and not (kb.flags & LLKHF_INJECTED):
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        plain_alt = (
                            (kb.flags & LLKHF_ALTDOWN)
                            and not _key_down(0x10)                    # shift
                            and not _key_down(0x11)                    # ctrl
                            and not _key_down(0x5B) and not _key_down(0x5C)  # win keys
                        )
                        if plain_alt:
                            if vk not in _swallowed:                   # ignore auto-repeat
                                _swallowed.add(vk)
                                log(f"hook caught Alt+{vk - 0x30}")
                                loop.call_soon_threadsafe(queue.put_nowait, vk - 0x30)
                            # Stop apps from treating a lone Alt tap as "open menu bar".
                            user32.keybd_event(0xFF, 0, 0, 0)
                            user32.keybd_event(0xFF, 0, KEYEVENTF_KEYUP, 0)
                            return 1
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP) and vk in _swallowed:
                        _swallowed.discard(vk)
                        return 1
        except Exception:
            traceback.print_exc()
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    callback = HOOKPROC(proc)
    _hook_ref["cb"] = callback
    hook = user32.SetWindowsHookExW(
        WH_KEYBOARD_LL, callback, ctypes.windll.kernel32.GetModuleHandleW(None), 0
    )
    if not hook:
        print(f"[glaze_slide] Could not install keyboard hook (error "
              f"{ctypes.GetLastError()}).", flush=True)
        return
    log("keyboard hook installed; waiting for Alt+1..9")

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    print("[glaze_slide] keyboard hook message loop ended", flush=True)


async def main():
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    threading.Thread(target=hotkey_thread, args=(loop, queue), daemon=True).start()

    async def on_client(reader, writer):
        """One line containing a workspace number 1-9, sent by the revolver overlay."""
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=1.0)
            text = data.decode("ascii", "ignore").strip()
            if text.isdigit() and 1 <= int(text) <= 9:
                log(f"overlay requested workspace {text}")
                queue.put_nowait(int(text))
        except Exception:
            pass
        finally:
            writer.close()

    if CONTROL_PORT:
        try:
            await asyncio.start_server(on_client, "127.0.0.1", CONTROL_PORT)
            log(f"listening for the overlay on 127.0.0.1:{CONTROL_PORT}")
        except OSError as exc:
            print(f"[glaze_slide] could not open control port {CONTROL_PORT}: {exc!r}", flush=True)

    while True:
        try:
            async with websockets.connect(URI) as ws:
                log("connected to GlazeWM; Alt+1..9 active")
                while True:
                    n = await queue.get()
                    while not queue.empty():      # only act on the latest key press
                        n = queue.get_nowait()
                    log(f"Alt+{n} pressed")
                    slide_flag(True)                       # tell the animator to stand down
                    await asyncio.sleep(0.045)             # give it a few frames to snap any running tween
                    try:
                        try:
                            await handle(ws, n, queue)
                        except websockets.ConnectionClosed:
                            raise
                        except Exception:
                            traceback.print_exc()
                            # never leave you stuck: still do the plain switch
                            await focus_workspace(ws, n)
                    finally:
                        await asyncio.sleep(0.2)           # let trailing move events settle
                        if queue.empty():                  # keep the flag up while a burst is still queued
                            slide_flag(False)
        except Exception as exc:
            slide_flag(False)
            print("[glaze_slide] connection problem, retrying in 3s:", repr(exc), flush=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        pass