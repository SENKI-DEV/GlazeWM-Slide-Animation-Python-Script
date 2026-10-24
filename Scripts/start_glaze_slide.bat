@echo off & timeout /t 5 /nobreak >nul & start "" "C:\Users\kian\AppData\Local\Programs\Python\Python38\pythonw.exe" "C:\Users\kian\Scripts\glaze_slide_hotkeys.py" & start "" "C:\Users\kian\AppData\Local\Programs\Python\Python38\pythonw.exe" -x "%~f0" & start "" "C:\Users\kian\AppData\Local\Programs\Python\Python38\pythonw.exe" "C:\Users\kian\Scripts\revolver_overlay.py" & exit /b
"""
GlazeWM smooth window animations.
Single file: cmd runs line 1 (launches pythonw on this same file, then exits),
Python skips line 1 (-x) and runs everything below.

How it works: a WinEvent hook watches for window position/size changes.
When GlazeWM snaps a window to a new tile, the window is put back at its old
rect and tweened to the new one with SetWindowPos.
"""
import base64
import ctypes
import json
import math
import os
import socket
import struct
import sys
import tempfile
import time
import threading
from collections import defaultdict, deque
from ctypes import wintypes, byref, create_unicode_buffer

# ------------------------------ settings ------------------------------
DURATION_MS = 180          # animation length
FPS = 60                   # animation frame rate
EASING = "cubic"           # feel of moves/resizes: cubic | back | elastic | bounce
OPEN_EASING = "back"       # feel of the open animation (back = slight overshoot)
OPEN_STYLE = "grow"        # grow | pop | rise | drop | unfold | swipe
OPEN_FADE = True           # new windows also fade in
OPEN_ANIMATION = True      # new windows grow into their tile instead of just appearing
OPEN_SCALE = 0.6           # size they start at (0.6 = 60% of the tile, centred in it)
OPEN_DURATION_MS = 260     # length of the open animation
MIN_DELTA_PX = 12          # ignore moves/resizes smaller than this (drags, jitter)
RECONCILE = True           # after animating, check windows against GlazeWM's layout and fix drift
RECONCILE_DELAY_MS = 150   # quiet time before that check
FIX_MIN_PX = 24            # only correct windows that are at least this far off target
VALIDATE_PX = 12           # sanity check: at least one window must already match within this
CONSOLE_MOVE_ONLY = True   # terminals/cmd re-flow their text on every resize: tween their position only, resize once
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty", "VirtualConsoleClass"}
DEBUG = False               # write decisions to start_glaze_slide.log next to this file
GLAZE_HOST, GLAZE_PORT = "127.0.0.1", 6123
EXCLUDE_PROCESSES = {"zebar.exe"}
EXCLUDE_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def ease_cubic(t):
    return 1 - (1 - t) ** 3


def ease_back(t):
    """Overshoots the target slightly, then settles."""
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_elastic(t):
    """Springy wobble around the target (works best with DURATION_MS ~ 450+)."""
    if t <= 0 or t >= 1:
        return t
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * (2 * math.pi / 3)) + 1


def ease_bounce(t):
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


EASINGS = {"cubic": ease_cubic, "back": ease_back, "elastic": ease_elastic, "bounce": ease_bounce}


# ------------------------------ win32 setup ------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
winmm = ctypes.WinDLL("winmm")

HWND, DWORD, UINT, BOOL = wintypes.HWND, wintypes.DWORD, wintypes.UINT, wintypes.BOOL

user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, UINT]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsWindow.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.GetAncestor.argtypes = [HWND, UINT]
user32.GetAncestor.restype = HWND
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.SetWindowLongW.argtypes = [HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetLayeredWindowAttributes.argtypes = [HWND, DWORD, ctypes.c_ubyte, DWORD]
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]
kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, BOOL, BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, DWORD]
kernel32.WaitForSingleObject.restype = DWORD

WinEventProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, DWORD, HWND, wintypes.LONG,
                                  wintypes.LONG, DWORD, DWORD)
user32.SetWinEventHook.argtypes = [DWORD, DWORD, ctypes.c_void_p, WinEventProc,
                                   DWORD, DWORD, DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
EnumWindowsProc = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)

EVENT_OBJECT_LOCATIONCHANGE = 0x800B
EVENT_OBJECT_UNCLOAKED = 0x8018
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
# NOZORDER|NOACTIVATE|NOOWNERZORDER|ASYNCWINDOWPOS|NOSENDCHANGING
# NOSENDCHANGING matters: it stops apps from clamping the size to their minimum width,
# which is what GlazeWM does too. Without it, windows get stuck wider than their tile.
SWP_FLAGS = 0x0004 | 0x0010 | 0x0200 | 0x4000 | 0x0400

# ------------------------------ state ------------------------------
lock = threading.RLock()
rects = {}                                        # hwnd -> last known rect
anims = {}                                        # hwnd -> running animation
own = defaultdict(lambda: deque(maxlen=16))       # rects we set ourselves (ignored)
state = {"dirty": None, "rounds": 0, "last": 0.0}  # when to run the next reconcile check
first_seen = {}                                   # hwnd -> when we first saw a new window
opened = set()                                    # windows whose open animation already ran
faded = set()                                     # windows we temporarily made layered for a fade-in


# Signalled by glaze_slide_hotkeys.py while it plays its workspace slide.
# While it is set we stay out of the way so the two scripts don't fight.
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


slide_event = _open_event(kernel32, "GlazeWM_Slide_Active")
_sup = {"since": None}
FLAG_FILE = os.path.join(tempfile.gettempdir(), "glaze_slide_active.flag")
_flag_cache = {"t": 0.0, "on": False}


def _file_flag():
    """Fallback signal written by the slide script (used if the named event isn't shared)."""
    now = time.time()
    if now - _flag_cache["t"] > 0.01:
        _flag_cache["t"] = now
        try:
            with open(FLAG_FILE, "r") as f:
                on = f.read(1) == "1"
            _flag_cache["on"] = on and (now - os.path.getmtime(FLAG_FILE)) < 10.0
        except (OSError, ValueError):
            _flag_cache["on"] = False
    return _flag_cache["on"]


def slide_active():
    by_event = kernel32.WaitForSingleObject(slide_event, 0) == 0
    by_file = (not by_event) and _file_flag()
    if by_event or by_file:
        if _sup["since"] is None:
            _sup["since"] = time.time()
            log("slide flag seen via", "event" if by_event else "file")
        return time.time() - _sup["since"] < 10.0  # safety: ignore a flag left stuck by a crashed slide script
    _sup["since"] = None
    return False


# ------------------------------ helpers ------------------------------
def get_rect(hwnd):
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def set_rect(hwnd, rect):
    l, t, r, b = rect
    user32.SetWindowPos(hwnd, None, l, t, r - l, b - t, SWP_FLAGS)


def class_name(hwnd):
    buf = create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def proc_name(hwnd):
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = create_unicode_buffer(520)
        size = DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, byref(size)):
            return buf.value.replace("/", "\\").split("\\")[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


_meta = {}   # hwnd -> (class name, process name); looked up once per window


def meta(hwnd):
    m = _meta.get(hwnd)
    if m is None:
        m = (class_name(hwnd), proc_name(hwnd))
        _meta[hwnd] = m
    return m


def is_candidate(hwnd):
    """Only normal, visible, uncloaked top-level app windows."""
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    if user32.GetAncestor(hwnd, 2) != hwnd:  # GA_ROOT
        return False
    ex = user32.GetWindowLongW(hwnd, -20)    # GWL_EXSTYLE
    if ex & 0x00000080 or ex & 0x08000000:   # TOOLWINDOW / NOACTIVATE
        return False
    if user32.GetWindowTextLengthW(hwnd) == 0:
        return False
    cloaked = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, 14, byref(cloaked), 4)  # DWMWA_CLOAKED
    if cloaked.value:
        return False
    cls, proc = meta(hwnd)
    if cls in EXCLUDE_CLASSES or proc in EXCLUDE_PROCESSES:
        return False
    return True


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _read_message(sock):
    """Read one (possibly fragmented) websocket message -> (opcode, payload bytes)."""
    data, opcode = b"", None
    while True:
        b0, b1 = _recv_exact(sock, 2)
        fin, op, ln = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        if b1 & 0x80:
            _recv_exact(sock, 4)  # servers don't mask; skip the key if one is sent
        data += _recv_exact(sock, ln)
        if op != 0:
            opcode = op
        if fin:
            return opcode, data


def glaze_request(msg):
    """Send one message to GlazeWM's IPC websocket (stdlib only); return the parsed reply or None."""
    try:
        s = socket.create_connection((GLAZE_HOST, GLAZE_PORT), timeout=2.0)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall(("GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
                       "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                       "Sec-WebSocket-Version: 13\r\n\r\n"
                       % (GLAZE_HOST, GLAZE_PORT, key)).encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(1024)
                if not chunk:
                    return None
                buf += chunk
            payload = msg.encode()
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            s.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + masked)  # text frame (<126 bytes)
            reply = None
            for _ in range(20):
                op, data = _read_message(s)
                if op == 0x1:
                    try:
                        obj = json.loads(data.decode("utf-8"))
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and obj.get("messageType") == "client_response":
                        reply = obj
                        break
                elif op == 0x8:
                    break
            try:
                s.sendall(bytes([0x88, 0x80]) + os.urandom(4))  # close frame
            except OSError:
                pass
            return reply
        finally:
            s.close()
    except Exception as exc:
        log("glaze_request failed:", repr(exc))
        return None


def frame_bounds(hwnd):
    """Visible window frame (without the invisible resize borders)."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, byref(r), ctypes.sizeof(r)) != 0:  # EXTENDED_FRAME_BOUNDS
        return None
    return (r.left, r.top, r.right, r.bottom)


def log(*args):
    if DEBUG:
        try:
            with open(os.path.splitext(os.path.abspath(__file__))[0] + ".log", "a") as f:
                f.write("%.3f %s\n" % (time.time(), " ".join(str(a) for a in args)))
        except Exception:
            pass


def shrunk(rect, scale):
    """rect scaled down around its own centre."""
    l, t, r, b = rect
    cx, cy = (l + r) / 2.0, (t + b) / 2.0
    w, h = (r - l) * scale, (b - t) * scale
    return (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2))


def open_start(new):
    """Where a new window starts before animating into its tile."""
    l, t, r, b = new
    w, h = r - l, b - t
    if OPEN_STYLE == "pop":      # starts bigger and settles in
        return shrunk(new, 1.25)
    if OPEN_STYLE == "rise":     # comes up from below
        dy = int(h * 0.3)
        return (l, t + dy, r, b + dy)
    if OPEN_STYLE == "drop":     # drops in from above
        dy = int(h * 0.3)
        return (l, t - dy, r, b - dy)
    if OPEN_STYLE == "unfold":   # unfolds downward from its top edge
        return (l, t, r, t + max(30, int(h * 0.08)))
    if OPEN_STYLE == "swipe":    # swipes in from the right
        dx = int(w * 0.6)
        return (l + dx, t, r + dx, b)
    return shrunk(new, OPEN_SCALE)  # "grow"


def fade_start(hwnd):
    """Make the window layered and fully transparent. False if it's already layered."""
    try:
        ex = user32.GetWindowLongW(hwnd, -20)
        if ex & 0x00080000:  # WS_EX_LAYERED: the app manages its own transparency
            return False
        user32.SetWindowLongW(hwnd, -20, ex | 0x00080000)
        user32.SetLayeredWindowAttributes(hwnd, 0, 0, 2)  # LWA_ALPHA
        faded.add(hwnd)
        return True
    except Exception:
        return False


def fade_end(hwnd):
    """Restore normal (opaque, non-layered) state."""
    faded.discard(hwnd)
    try:
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, ex & ~0x00080000)
    except Exception:
        pass


def begin_anim(hwnd, old, new, snap=True, dur=None, ease=None, fade=False):
    """Start tweening hwnd from old to new. Caller must hold `lock`."""
    if CONSOLE_MOVE_ONLY and meta(hwnd)[0] in CONSOLE_CLASSES:
        # start at the final size so only the position is tweened (one resize, not one per frame)
        old = (old[0], old[1], old[0] + (new[2] - new[0]), old[1] + (new[3] - new[1]))
    fading = bool(fade and fade_start(hwnd))
    if snap:
        set_rect(hwnd, old)  # GlazeWM already moved it to `new`; put it back first
    log("anim", hex(hwnd), old, "->", new, "(snap)" if snap else "(fix)")
    own[hwnd].append(old)
    rects[hwnd] = old
    anims[hwnd] = {"start": old, "end": new, "cur": old, "t0": time.perf_counter(),
                   "dur": dur or DURATION_MS / 1000.0,
                   "ease": ease or EASINGS.get(EASING, ease_cubic), "fading": fading}


def finish_all():
    """Snap every running tween to its final rect and restore opacity. Caller must hold `lock`."""
    for hwnd, a in list(anims.items()):
        if user32.IsWindow(hwnd):
            set_rect(hwnd, a["end"])
            rects[hwnd] = a["end"]
    anims.clear()
    for h in list(faded):
        fade_end(h)


def reconcile():
    """Ask GlazeWM where tiling windows belong and fix any that ended up somewhere else.

    This repairs windows we left at a stale position when GlazeWM changed the layout again
    while an animation was running (e.g. several windows opened in quick succession).
    """
    reply = glaze_request("query windows")
    if not reply or reply.get("success") is False:
        log("reconcile: no usable reply from GlazeWM:", str(reply)[:300])
        return
    wins = (reply.get("data") or {}).get("windows") or []
    if not wins:
        log("reconcile: reply has no windows:", str(reply)[:300])
    else:
        log("reconcile: sample window from GlazeWM:", str(wins[0])[:400])
    plans = []
    for w in wins:
        try:
            hwnd = int(w["handle"])
            if (w.get("state") or {}).get("type") != "tiling":
                continue
            if str(w.get("displayState", "shown")).lower() in ("hidden", "hiding"):
                continue
            if not is_candidate(hwnd):
                continue
            cur, fb = get_rect(hwnd), frame_bounds(hwnd)
            if not cur or not fb:
                continue
            x, y, ww, hh = w["x"], w["y"], w["width"], w["height"]
            # layout rect -> real window rect (adds the invisible resize borders back)
            target = (x + cur[0] - fb[0], y + cur[1] - fb[1],
                      x + ww + cur[2] - fb[2], y + hh + cur[3] - fb[3])
            plans.append((hwnd, cur, tuple(int(round(v)) for v in target)))
        except Exception as exc:
            log("reconcile: could not read window", repr(exc), str(w)[:200])
            continue

    def err(c, t):
        return max(abs(a - b) for a, b in zip(c, t))

    def same_size(c, t):
        return (abs((c[2] - c[0]) - (t[2] - t[0])) <= VALIDATE_PX
                and abs((c[3] - c[1]) - (t[3] - t[1])) <= VALIDATE_PX)

    # Only trust GlazeWM's numbers if a window already sits where they say, OR has exactly the
    # size they say. The workspace slide only moves windows (never resizes them), so a window
    # stranded at a slide offset keeps its size. Without this, a workspace whose only window got
    # stranded could never be repaired, because no window matched.
    if not any(err(c, t) <= VALIDATE_PX or same_size(c, t) for _, c, t in plans):
        log("reconcile: skipped, layout numbers don't match any window", plans)
        return
    fixes = [(h, c, t) for h, c, t in plans if err(c, t) >= FIX_MIN_PX]
    log("reconcile: %d windows, %d to fix" % (len(plans), len(fixes)), fixes)
    with lock:
        if slide_active():   # a workspace slide started while we were asking GlazeWM: these numbers are stale
            log("reconcile: slide in progress, dropping fixes")
            return
        for hwnd, cur, target in fixes:
            if hwnd not in anims:
                begin_anim(hwnd, cur, target, snap=False)


# ------------------------------ event hook ------------------------------
def on_event(hook, event, hwnd, id_object, id_child, thread, event_time):
    try:
        if not hwnd or id_object != 0 or id_child != 0:
            return
        if slide_active():
            return
        if not is_candidate(hwnd):
            return
        new = get_rect(hwnd)
        if new is None:
            return
        with lock:
            if event == EVENT_OBJECT_UNCLOAKED:  # window just became visible: resync, no animation
                anims.pop(hwnd, None)
                rects[hwnd] = new
                state["dirty"] = time.perf_counter()
                return
            if new in own[hwnd]:
                return  # this is our own animation frame
            state["dirty"] = time.perf_counter()
            state["rounds"] = 0
            anim = anims.get(hwnd)
            old = anim["cur"] if anim else rects.get(hwnd)
            dragging = bool(user32.GetAsyncKeyState(0x01) & 0x8000)  # left mouse down
            small = old is None or max(abs(a - b) for a, b in zip(old, new)) < MIN_DELTA_PX
            if dragging or small:
                if anim:
                    set_rect(hwnd, new)  # don't leave a half-animated window behind
                if old is None:
                    first_seen[hwnd] = time.perf_counter()  # brand new window
                anims.pop(hwnd, None)
                rects[hwnd] = new
                return
            dur = None
            if (OPEN_ANIMATION and not anim and hwnd not in opened and hwnd in first_seen
                    and time.perf_counter() - first_seen[hwnd] < 2.0):
                # a new window being placed by GlazeWM for the first time: grow it into its tile
                old = open_start(new)
                dur = OPEN_DURATION_MS / 1000.0
                opened.add(hwnd)
                begin_anim(hwnd, old, new, dur=dur, fade=OPEN_FADE,
                           ease=EASINGS.get(OPEN_EASING, ease_back))
                return
            # GlazeWM just snapped the window: put it back and animate to the new rect
            begin_anim(hwnd, old, new)
    except Exception:
        pass


# ------------------------------ animator thread ------------------------------
def animator():
    frame = 1.0 / FPS
    last_prune = time.time()
    was_sliding = False
    while True:
        time.sleep(frame)
        with lock:
            if slide_active():          # workspace slide in progress: hands off
                finish_all()            # but never leave a window stuck half-animated or half-faded
                was_sliding = True
                continue
            if was_sliding:             # slide finished: resync all window rects
                was_sliding = False
                own.clear()
                seed_rects()
                state["dirty"] = time.perf_counter()   # and have the layout checked afterwards
                state["rounds"] = 0
            now = time.perf_counter()
            done = []
            for hwnd, a in anims.items():
                if not user32.IsWindow(hwnd):
                    done.append(hwnd)
                    continue
                t = min(1.0, (now - a["t0"]) / a["dur"])
                e = a["ease"](t)
                cur = tuple(int(round(s + (d - s) * e))
                            for s, d in zip(a["start"], a["end"]))
                cur = (cur[0], cur[1], max(cur[2], cur[0] + 100), max(cur[3], cur[1] + 60))  # overshoot guard
                if a["fading"]:
                    user32.SetLayeredWindowAttributes(hwnd, 0, int(255 * min(1.0, t / 0.6)), 2)
                a["cur"] = cur
                own[hwnd].append(cur)
                set_rect(hwnd, cur)
                rects[hwnd] = cur
                if t >= 1.0:
                    done.append(hwnd)
            for hwnd in done:
                anims.pop(hwnd, None)
                own[hwnd] = deque([rects.get(hwnd)], maxlen=16)
            if done:
                state["dirty"] = now
            for h in [h for h in faded if h not in anims]:  # never leave a window faded
                fade_end(h)
            # Once everything has settled, compare windows with GlazeWM's layout.
            if (RECONCILE and state["dirty"] is not None and not anims
                    and now - state["dirty"] >= RECONCILE_DELAY_MS / 1000.0
                    and state["rounds"] < 3 and now - state["last"] > 0.5
                    and not (user32.GetAsyncKeyState(0x01) & 0x8000)):
                state["dirty"] = None
                state["rounds"] += 1
                state["last"] = now
                log("reconcile check starting")
                threading.Thread(target=reconcile, daemon=True).start()
            if time.time() - last_prune > 10:  # forget closed windows
                last_prune = time.time()
                for h in [h for h in rects if not user32.IsWindow(h)]:
                    rects.pop(h, None)
                    own.pop(h, None)
                for h in [h for h in _meta if not user32.IsWindow(h)]:
                    _meta.pop(h, None)
                for h in [h for h in first_seen if not user32.IsWindow(h)]:
                    first_seen.pop(h, None)
                    opened.discard(h)


# ------------------------------ main ------------------------------
def seed_rects():
    def cb(hwnd, lparam):
        try:
            if is_candidate(hwnd):
                r = get_rect(hwnd)
                if r:
                    rects[hwnd] = r
        except Exception:
            pass
        return True
    user32.EnumWindows(EnumWindowsProc(cb), 0)


def main():
    log("animator started; slide event handle:", slide_event, "winerror:", ctypes.get_last_error())
    # single instance
    mutex = kernel32.CreateMutexW(None, False, "GlazeWM_Window_Animator")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)

    # physical pixel coordinates, same as GlazeWM
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()
    winmm.timeBeginPeriod(1)  # finer sleep resolution for smoother frames

    seed_rects()
    threading.Thread(target=animator, daemon=True).start()

    callback = WinEventProc(on_event)  # keep a reference or it gets garbage collected
    hooks = [user32.SetWinEventHook(ev, ev, None, callback, 0, 0,
                                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
             for ev in (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_UNCLOAKED)]
    if not all(hooks):
        sys.exit(1)

    msg = wintypes.MSG()
    while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(byref(msg))
        user32.DispatchMessageW(byref(msg))


if __name__ == "__main__":
    main()