"""
Revolver cylinder workspace overlay for GlazeWM (corner quadrant edition).

The cylinder's center sits exactly on a corner of your screen, so only one
quarter of it is visible. By default that's the bottom-right quadrant of a
cylinder centered on the top-left corner. Switching workspace spins the
cylinder so the focused chamber lands on the diagonal "firing" position;
neighbouring chambers roll in and out of view past the screen edges.

    filled green + glow = focused workspace (same highlight YASB gives it)
    lighter chamber     = workspace has windows
    faint chamber       = empty workspace

Controls
    left click chamber   switch to that workspace
    mouse wheel          previous / next workspace
    right click          quit menu

Run:   pythonw revolver_overlay.py          (or `python` to see errors)
Demo:  python revolver_overlay.py --demo    (no GlazeWM needed)
"""
import array
import json
import math
import os
import random
import struct
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

try:
    import winsound          # Windows only; used for the cylinder sounds
except ImportError:
    winsound = None


def _log_crash(exc_type, exc, tb):
    """pythonw has no console, so write any error next to the script instead of losing it."""
    import traceback
    try:
        with open(Path(__file__).with_name("revolver_overlay.log"), "a", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(exc_type, exc, tb)) + "\n")
    except Exception:
        pass


sys.excepthook = _log_crash

from PySide6.QtCore import (
    QEasingCurve, QObject, QPointF, QRectF, Qt, QTimer, QUrl,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath,
    QPainterPathStroker, QPen, QPolygonF, QRadialGradient, QRegion, QTransform,
)
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
except Exception:            # QtMultimedia missing: fall back to winsound
    QAudioSink = None
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QMenu, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

CONFIG_PATH = Path(__file__).with_name("revolver_config.json")
DEFAULTS = {
    "chambers": 6,            # number of workspaces / chambers
    # --- fit to your YASB bar (these match your config.yaml: height 36, windows_app_bar) ---
    "bar_margin": 0,          # gap between screen edge and the bar window
    "bar_height": 36,         # bars.primary-bar.dimensions.height
    "flat_cut": False,        # False = full circle. True = cut flat at the bottom of your widget groups at rest
    "pill_height": 24,        # height of a widget group: bar_height - padding top/bottom (4+4) - group margin (2+2)
    "chamber_size": 22,       # chamber diameter at base_scale 1.0
    "base_scale": 1.4,        # resting size multiplier (1.0 = fits the bar exactly)
    "gap": 4,                 # space between the cylinder and the bar's first group
    "radius": 90,             # only used when bar_height is 0
    "accent_color": "#94e2d5",           # focused chamber, its glow and the firing marker
    "chamber_color": "#45475a",          # chambers (workspaces with windows)
    "chamber_rim_color": "#89b4fa",      # chamber outline / hover
    "text_color": "#cdd6f4",             # workspace numbers
    "body_color": [17, 18, 19, 255],     # = --base, the fill of .grouped-widgets .container
    "border_color": [69, 71, 90, 255],   # = --surface1, its border
    "hide_in_fullscreen": True,          # the bar hides behind fullscreen apps; so should this
    "port": 6123,             # GlazeWM IPC port
    "slide_port": 48721,      # glaze_slide_hotkeys.py control port, so click/scroll get the slide animation (0 = off)
    "spin_ms": 520,           # base spin duration (the turn to the right chamber)
    "extra_spins": 2,         # full extra rotations before landing (0 = just turn to the chamber)
    "long_spin_min_distance": 2,  # ...but only when jumping at least this many workspaces (1->3 is 2)
    "spin_ms_per_extra": 450, # added time for each extra rotation
    "settle_overshoot_deg": 12,  # how far past the chamber it swings before settling back
    "hover_pop": True,        # hovering the cylinder shows it at its pop size
    "sound": True,            # ratchet clicks while it spins, a heavier clack when it locks in
    "sound_volume": 0.6,      # 0.0 - 1.0
    "pop_scale": 5.0,         # while spinning, grows to this many times its resting size (1.0 = off)
    "pop_grow_ms": 150,       # time to grow
    "pop_shrink_ms": 280,     # time to settle back after the spin ends
    "workspace_names": None,  # e.g. ["1","2","3"]; null = "1".."N"
    "font": "JetBrainsMono NFP",
}

# Palette taken from your styles.css (Catppuccin Mocha + your bar's colours)
C_BODY = QColor(20, 21, 30, 225)          # rgba(20,21,30,.85) like .glazewm-workspaces
C_BORDER = QColor("#45475a")              # --surface1
C_TRACK = QColor(88, 91, 112, 90)         # --surface2, faint
C_GREEN = QColor("#94e2d5")               # --green (your active-workspace accent)
C_TEXT = QColor("#cdd6f4")                # --text
C_TEXT_DIM = QColor("#6c7086")            # --overlay0
C_TEXT_DARK = QColor("#11111b")

# chamber states, copied from your old .ws-btn rules
FILL_EMPTY = QColor(45, 47, 64, 102)      # rgba(45,47,64,.4)
RIM_EMPTY = QColor(108, 112, 134, 64)     # rgba(108,112,134,.25)
FILL_LOADED = QColor(69, 71, 90, 128)     # .populated  rgba(69,71,90,.5)
RIM_LOADED = QColor(137, 180, 250, 77)    #             rgba(137,180,250,.3)
FILL_HOVER = QColor(137, 180, 250, 64)    # :hover      rgba(137,180,250,.25)
RIM_HOVER = QColor(137, 180, 250, 128)    #             rgba(137,180,250,.5)
RIM_ACTIVE = QColor(255, 255, 255, 235)   # active border #ffffff

# (center x fraction, center y fraction, firing angle in degrees clockwise from 12 o'clock)
CORNERS = {
    "top-left":     (0, 0, 135.0),
    "top-right":    (1, 0, 225.0),
    "bottom-left":  (0, 1, 45.0),
    "bottom-right": (1, 1, 315.0),
}


def fullscreen_active():
    """True when a fullscreen app / game / presentation is running (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        state = ctypes.c_int()
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return state.value in (2, 3, 4)   # BUSY, D3D_FULL_SCREEN, PRESENTATION_MODE
    except Exception:
        pass
    return False


# --------------------------------------------------------------------------
# pure helpers (no Qt)
# --------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    if not cfg["workspace_names"]:
        cfg["workspace_names"] = [str(i + 1) for i in range(cfg["chambers"])]
    cfg["chambers"] = len(cfg["workspace_names"])
    if cfg["corner"] not in CORNERS:
        cfg["corner"] = "top-left"
    return cfg


def qcolor(v):
    """QColor from a config value: [r, g, b, a] list or '#rrggbb' / '#aarrggbb' string."""
    if isinstance(v, (list, tuple)):
        return QColor(*[int(x) for x in v])
    c = QColor(str(v))
    return c if c.isValid() else QColor("#ff00ff")


def color_to_cfg(c):
    return c.name(QColor.NameFormat.HexArgb)


def auto_text_color(c):
    """Dark text on light fills, light text on dark fills."""
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor("#11111b") if lum > 150 else QColor("#ffffff")


def save_config(cfg):
    try:
        out = {k: cfg[k] for k in DEFAULTS if k in cfg}
        if out.get("workspace_names") == [str(i + 1) for i in range(cfg["chambers"])]:
            out["workspace_names"] = None
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    except Exception as exc:
        _note(f"saving settings failed: {exc!r}")


def shortest_target(current_angle, wanted_angle):
    """Unwrapped angle reaching `wanted_angle` (mod 360) by the shortest turn."""
    delta = ((wanted_angle - current_angle + 180.0) % 360.0) - 180.0
    return current_angle + delta


def back_s_for_overshoot(frac):
    """Parameter s for Qt's OutBack easing so it overshoots by `frac` of the total distance.

    OutBack(t) = (t-1)^2 * ((s+1)(t-1) + s) + 1, whose default s=1.70158 overshoots 10%.
    A long spin needs a much smaller s, or it would swing a whole chamber past the target.
    """
    def peak(s):
        return max((t - 1) ** 2 * ((s + 1) * (t - 1) + s) for t in (i / 200.0 for i in range(201)))
    lo, hi = 0.0, 1.70158 * 3
    for _ in range(24):
        mid = (lo + hi) / 2.0
        if peak(mid) < frac:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def spin_timeline(start, target, fire, step, duration_ms, s, samples=1500):
    """When (ms into the spin) chambers pass the firing position, and when the target locks in.

    Uses the same OutBack easing as the animation, so sound and picture line up.
    Returns (tick_times_ms, land_ms).
    """
    def ease(t):
        t -= 1.0
        return t * t * ((s + 1.0) * t + s) + 1.0
    ticks, land, prev = [], None, start
    for i in range(1, samples + 1):
        p = i / float(samples)
        a = start + (target - start) * ease(p)
        k0 = math.floor((prev - fire) / step + 1e-9)
        k1 = math.floor((a - fire) / step + 1e-9)
        if k0 != k1:
            boundary = fire + (k1 if a > prev else k0) * step
            if abs(boundary - target) < 0.01:
                land = p * duration_ms         # first arrival; the overshoot's return is ignored
                break
            ticks.append(p * duration_ms)
        prev = a
    return ticks, (land if land is not None else float(duration_ms))


def chamber_center(i, n, angle_deg, cx, cy, ring_r):
    theta = math.radians(i * (360.0 / n) + angle_deg)
    return cx + ring_r * math.sin(theta), cy - ring_r * math.cos(theta)


def parse_workspaces(workspaces, names):
    """GlazeWM workspace list -> (focused_index or None, set of loaded indices)."""
    index = {name: i for i, name in enumerate(names)}
    focused = displayed = None
    loaded = set()
    for w in workspaces:
        i = index.get(str(w.get("name")))
        if i is None:
            continue
        if w.get("children"):
            loaded.add(i)
        if w.get("hasFocus"):
            focused = i
        if w.get("isDisplayed") and displayed is None:
            displayed = i
    return (focused if focused is not None else displayed), loaded


# --------------------------------------------------------------------------
# sounds: synthesised at startup, so there are no audio files to ship
# --------------------------------------------------------------------------
def _synth(kind, volume, seed):
    """Short mechanical click ("tick") or the heavier lock-up "clack", as 16-bit mono PCM."""
    sr = 44100
    rnd = random.Random(seed)
    if kind == "clack":
        n, tone_hz = int(sr * 0.20), 760.0
    else:
        n, tone_hz = int(sr * 0.035), 1900.0 + 220.0 * (seed % 3)
    out, lp = [], 0.0
    for i in range(n):
        t = i / sr
        lp += 0.35 * (rnd.uniform(-1.0, 1.0) - lp)            # softened noise = the "snick"
        if kind == "clack":
            v = (lp * math.exp(-t / 0.006)
                 + 0.60 * math.sin(2 * math.pi * tone_hz * t) * math.exp(-t / 0.030)
                 + 0.30 * math.sin(2 * math.pi * 3100.0 * t) * math.exp(-t / 0.060)   # metallic ring
                 + 0.20 * math.sin(2 * math.pi * 4700.0 * t) * math.exp(-t / 0.045))
        else:
            v = (lp * math.exp(-t / 0.0022)
                 + 0.45 * math.sin(2 * math.pi * tone_hz * t) * math.exp(-t / 0.0035)
                 + 0.25 * math.sin(2 * math.pi * 900.0 * t) * math.exp(-t / 0.006))
        out.append(v)
    peak = max(abs(x) for x in out) or 1.0
    scale = max(0.0, min(1.0, volume)) * 0.9 * 32767 / peak
    return b"".join(struct.pack("<h", int(x * scale)) for x in out)


def _note(text):
    """Append a line to revolver_overlay.log (pythonw has no console)."""
    try:
        with open(Path(__file__).with_name("revolver_overlay.log"), "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


class LiveMixer:
    """A small always-open audio stream that mixes click sounds in as they are triggered.

    The stream never closes, so the audio device is never asleep when a spin starts (a sleeping
    device swallows short sounds), latency is the same every time, and clicks can overlap.
    """
    RATE = 44100
    TARGET_MS = 28           # how far ahead of the speakers we keep the buffer filled

    def __init__(self, ticks, clack, gain):
        self.ticks, self.clack, self.gain = ticks, clack, gain
        self.voices = []
        self._i = 0
        self.sink = self.io = None
        self.timer = QTimer()
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.pump)
        self.devices = QMediaDevices()
        self.devices.audioOutputsChanged.connect(self.open)    # e.g. headphones plugged in
        self.open()
        self.ok = self.io is not None

    def open(self, *_):
        self.timer.stop()
        if self.sink is not None:
            self.sink.stop()
        self.sink = self.io = None
        fmt = QAudioFormat()
        fmt.setSampleRate(self.RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull() or not dev.isFormatSupported(fmt):
            _note("sound: no usable default audio output for 44.1 kHz mono 16-bit")
            return
        self.sink = QAudioSink(dev, fmt)
        self.sink.setBufferSize(8192)
        self.io = self.sink.start()                            # push mode: we write the samples
        if self.io is None:
            self.sink = None
            return
        self.timer.start()

    def trigger(self, kind):
        if self.io is None:
            return
        if kind == "clack":
            snd = self.clack
        else:
            snd = self.ticks[self._i % 3]                      # rotate variants so it isn't robotic
            self._i += 1
        self.voices.append([snd, 0])

    def pump(self):
        if self.io is None or self.sink is None:
            return
        queued = self.sink.bufferSize() - self.sink.bytesFree()
        n = (int(self.RATE * self.TARGET_MS / 1000) * 2 - queued) // 2
        if n <= 0:
            return
        if not self.voices:
            self.io.write(bytes(n * 2))                        # keep the stream alive with silence
            return
        buf, live = [0] * n, []
        for snd, pos in self.voices:
            end = min(pos + n, len(snd))
            for i, v in enumerate(snd[pos:end]):
                buf[i] += v
            if end < len(snd):
                live.append([snd, end])
        self.voices = live
        g = self.gain
        self.io.write(array.array("h", [max(-32768, min(32767, int(v * g))) for v in buf]).tobytes())


class Sounds:
    """Click sounds for the spin.

    Preferred: LiveMixer (QtMultimedia), triggered live as each chamber passes.
    Fallback: one pre-mixed WAV per spin through winsound, which is less consistent because
    winsound cuts off the previous sound and has variable start latency.
    """
    RATE = 44100

    def __init__(self, enabled, volume):
        self.ok = False
        self.live = None
        self.cache = {}
        self._reported = False
        self._inited = False
        self.volume = max(0.0, min(1.0, float(volume)))
        self.muted = not enabled
        if enabled:
            self._init_backend()

    def _init_backend(self):
        self._inited = True
        try:
            def samples(kind, seed):
                a = array.array("h")
                a.frombytes(_synth(kind, 1.0, seed))           # full level; volume is applied when mixing
                return list(a)
            self.ticks = [samples("tick", i) for i in range(3)]
            self.clack = samples("clack", 7)
        except Exception as exc:
            _note(f"sound synthesis failed: {exc!r}")
            return
        if QAudioSink is not None:
            try:
                mixer = LiveMixer(self.ticks, self.clack, self.volume)
                if mixer.ok:
                    self.live, self.ok = mixer, True
                    _note("sound backend: live mixer (QAudioSink)")
                    return
            except Exception as exc:
                _note(f"live mixer unavailable: {exc!r}")
        if winsound:
            try:
                self.folder = Path(tempfile.gettempdir()) / "revolver_sounds"
                self.folder.mkdir(exist_ok=True)
                for old in self.folder.glob("*.wav"):
                    try:
                        old.unlink()
                    except OSError:
                        pass
                self.ok = True
                _note("sound backend: winsound (pre-mixed per spin)")
            except Exception as exc:
                _note(f"sound setup failed: {exc!r}")

    def set_enabled(self, on):
        self.muted = not on
        if on and not self._inited:
            self._init_backend()

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, float(v)))
        if self.live is not None:
            self.live.gain = self.volume
        self.cache.clear()

    def trigger(self, kind):
        if self.live is not None and not self.muted:
            self.live.trigger(kind)

    # ---- winsound fallback ----
    def _render(self, ticks_ms, land_ms):
        rate_ms = self.RATE / 1000.0
        n = int((land_ms + 260.0) * rate_ms) + len(self.clack)
        buf = [0] * n
        events = [(t, self.ticks[j % 3]) for j, t in enumerate(ticks_ms)] + [(land_ms, self.clack)]
        for t, snd in events:
            off = int(t * rate_ms)
            for i, v in enumerate(snd):
                buf[off + i] += v
        g = self.volume
        return array.array("h", [max(-32768, min(32767, int(v * g))) for v in buf]).tobytes()

    def prepare(self, ticks_ms, land_ms):
        """Render (once) and return the WAV path for this spin's click pattern."""
        key = (tuple(int(t / 4) for t in ticks_ms), int(land_ms / 4), round(self.volume, 2))
        path = self.cache.get(key)
        if path is None:
            path = str(self.folder / f"spin_{abs(hash(key))}.wav")
            tmp = path + ".tmp"
            with wave.open(tmp, "wb") as w:                    # write aside, then rename: never play a half-written file
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.RATE)
                w.writeframes(self._render(ticks_ms, land_ms))
            os.replace(tmp, path)
            self.cache[key] = path
        return path

    def play_spin(self, ticks_ms, land_ms):
        if not self.ok or self.live is not None or self.muted:
            return
        try:
            path = self.prepare(ticks_ms, land_ms)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception as exc:
            if not self._reported:
                self._reported = True
                _note(f"playing sound failed: {exc!r}")


# --------------------------------------------------------------------------
# settings panel (opens when you left-click the cylinder body, not a chamber)
# --------------------------------------------------------------------------
PANEL_QSS = """
QDialog { background-color: #111213; }
QLabel, QCheckBox { color: #cdd6f4; font-size: 12px; }
QPushButton { background-color: #282936; color: #cdd6f4; border: 1px solid #45475a;
              border-radius: 6px; padding: 4px 10px; }
QPushButton:hover { border-color: #94e2d5; }
QSlider::groove:horizontal { height: 4px; background: #45475a; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #94e2d5; border-radius: 2px; }
QSlider::handle:horizontal { background: #cdd6f4; width: 12px; margin: -5px 0; border-radius: 6px; }
QSpinBox { background-color: #282936; color: #cdd6f4; border: 1px solid #45475a;
           border-radius: 4px; padding: 2px 4px; }
"""


class SettingsPanel(QDialog):
    COLORS = (("Focused (accent)", "accent_color"), ("Body", "body_color"), ("Border", "border_color"),
              ("Chambers", "chamber_color"), ("Chamber outline", "chamber_rim_color"),
              ("Numbers", "text_color"))
    KEYS = ("accent_color", "body_color", "border_color", "chamber_color", "chamber_rim_color",
            "text_color", "base_scale", "pop_scale", "extra_spins", "long_spin_min_distance",
            "spin_ms_per_extra", "flat_cut", "hover_pop", "sound", "sound_volume")

    def __init__(self, rev):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.rev = rev
        self._refreshers = []
        self._pending = {}
        self.setWindowTitle("Revolver settings")
        self.setStyleSheet(PANEL_QSS)
        self.setFont(QFont(rev.cfg["font"], 9))

        self._debounce = QTimer(self)             # apply slider drags a moment after they settle
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(70)
        self._debounce.timeout.connect(self._flush)

        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        for label, key in self.COLORS:
            btn = QPushButton()
            btn.setFixedSize(70, 22)
            btn.clicked.connect(lambda _=False, k=key: self._pick(k))
            form.addRow(label, btn)
            self._refreshers.append(lambda b=btn, k=key: self._paint_swatch(b, k))

        self._slider(form, "Size", "base_scale", 60, 250, 100, lambda v: f"{v:.2f}x")
        self._slider(form, "Pop size", "pop_scale", 10, 80, 10, lambda v: f"{v:.1f}x")
        self._spin(form, "Extra spins", "extra_spins", 0, 6, "")
        self._spin(form, "Long spin from", "long_spin_min_distance", 1, 8, " workspaces away")
        self._slider(form, "Time per extra spin", "spin_ms_per_extra", 100, 900, 1, lambda v: f"{v:.0f} ms")
        self._slider(form, "Volume", "sound_volume", 0, 100, 100, lambda v: f"{v * 100:.0f}%")
        self._check(form, "Sound", "sound")
        self._check(form, "Show pop size on hover", "hover_pop")
        self._check(form, "Flat bottom cut", "flat_cut")

        self.info = QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        row = QHBoxLayout()
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(close)
        root.addLayout(row)
        self.refresh()

    # ---- building blocks ----
    def _slider(self, form, label, key, lo, hi, div, fmt):
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        val = QLabel()
        val.setMinimumWidth(56)
        row = QHBoxLayout()
        row.addWidget(sl)
        row.addWidget(val)

        def changed(v):
            real = v / float(div)
            val.setText(fmt(real))
            self._queue(key, real)

        def refresh():
            sl.blockSignals(True)
            sl.setValue(int(round(self.rev.cfg[key] * div)))
            sl.blockSignals(False)
            val.setText(fmt(self.rev.cfg[key]))

        sl.valueChanged.connect(changed)
        form.addRow(label, row)
        self._refreshers.append(refresh)

    def _spin(self, form, label, key, lo, hi, suffix):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSuffix(suffix)
        sp.valueChanged.connect(lambda v: self._queue(key, int(v)))
        form.addRow(label, sp)

        def refresh():
            sp.blockSignals(True)
            sp.setValue(int(self.rev.cfg[key]))
            sp.blockSignals(False)
        self._refreshers.append(refresh)

    def _check(self, form, label, key):
        cb = QCheckBox(label)
        cb.toggled.connect(lambda on: self._queue(key, bool(on)))
        form.addRow(cb)

        def refresh():
            cb.blockSignals(True)
            cb.setChecked(bool(self.rev.cfg[key]))
            cb.blockSignals(False)
        self._refreshers.append(refresh)

    def _paint_swatch(self, btn, key):
        c = qcolor(self.rev.cfg[key])
        btn.setStyleSheet(f"background-color: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()});"
                          " border: 1px solid #585b70; border-radius: 4px;")
        btn.setToolTip(f"{key}: {color_to_cfg(c)}")

    # ---- behaviour ----
    def refresh(self):
        for fn in self._refreshers:
            fn()
        self._update_info()

    def _update_info(self):
        side = self.rev.cfg["corner"].split("-")[1]
        self.info.setText(f"Keep {self.rev.yasb_padding()} px free at the bar's {side} end "
                          f"(YASB padding.{side}) so the cylinder doesn't cover your widgets.")

    def _pick(self, key):
        start = qcolor(self.rev.cfg[key])
        c = QColorDialog.getColor(start, self, "Choose color",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.rev.apply_setting(key, color_to_cfg(c))
            self.refresh()

    def _queue(self, key, value):
        self._pending[key] = value
        self._debounce.start()

    def _flush(self):
        pending, self._pending = self._pending, {}
        for key, value in pending.items():
            self.rev.apply_setting(key, value)
        self._update_info()

    def _reset(self):
        for key in self.KEYS:
            self.rev.apply_setting(key, DEFAULTS[key])
        self.refresh()


# --------------------------------------------------------------------------
# GlazeWM IPC (WebSocket)
# --------------------------------------------------------------------------
class GlazeIpc(QObject):
    state = Signal(object, object)   # focused index (or None), set of loaded indices
    online = Signal(bool)

    EVENTS = ("focus_changed workspace_activated workspace_deactivated "
              "window_managed window_unmanaged focused_container_moved")

    def __init__(self, port, names, parent=None):
        super().__init__(parent)
        self.names = names
        self.url = QUrl(f"ws://127.0.0.1:{port}")
        self.ws = QWebSocket()
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_text)

        self.retry = QTimer(self)
        self.retry.setInterval(2000)
        self.retry.timeout.connect(self._try_connect)

        # safety net in case an event is missed or the sub syntax differs
        self.poll = QTimer(self)
        self.poll.setInterval(1500)
        self.poll.timeout.connect(self.query)

    def start(self):
        self._try_connect()
        self.retry.start()
        self.poll.start()

    def _try_connect(self):
        if self.ws.state() == QAbstractSocket.SocketState.UnconnectedState:
            self.ws.open(self.url)

    def _on_connected(self):
        self.online.emit(True)
        self.ws.sendTextMessage(f"sub -e {self.EVENTS}")
        self.query()

    def _on_disconnected(self):
        self.online.emit(False)

    def send(self, command):
        if self.ws.isValid():
            self.ws.sendTextMessage(command)

    def query(self):
        self.send("query workspaces")

    def focus_workspace(self, index):
        self.send(f"command focus --workspace {self.names[index]}")

    def _on_text(self, msg):
        try:
            d = json.loads(msg)
        except Exception:
            return
        data = d.get("data")
        if isinstance(data, dict) and "workspaces" in data:
            focused, loaded = parse_workspaces(data["workspaces"], self.names)
            self.state.emit(focused, loaded)
        elif d.get("messageType") == "event_subscription":
            self.query()


# --------------------------------------------------------------------------
# the overlay widget
# --------------------------------------------------------------------------
class Revolver(QWidget):
    def __init__(self, cfg, ipc=None):
        super().__init__()
        self.cfg = cfg
        self.ipc = ipc
        self.n = cfg["chambers"]
        self.names = cfg["workspace_names"]
        self.step = 360.0 / self.n
        self.focused = 0
        self.loaded = set()
        self.hover = -1
        self.is_online = False

        self.fire = CORNERS[cfg["corner"]][2]
        self.scale = 1.0
        self._popped = False
        self.angle = self.fire                      # chamber 0 starts in firing position
        self._last_index = 0                        # workspace the cylinder last turned to
        self._build_palette()

        self.setWindowTitle("RevolverOverlay")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._apply_geometry()

        if cfg["hide_in_fullscreen"]:
            self._fs_timer = QTimer(self)
            self._fs_timer.timeout.connect(self._check_fullscreen)
            self._fs_timer.start(700)

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(cfg["spin_ms"])
        self.anim.setEasingCurve(QEasingCurve.OutBack)   # slight overshoot = mechanical snap
        self.anim.valueChanged.connect(self._on_angle)
        self.anim.finished.connect(self._on_spin_finished)

        self.scale_anim = QVariantAnimation(self)        # the "pop": grow while spinning, settle after
        self.scale_anim.valueChanged.connect(self._on_scale)

        self.sounds = Sounds(cfg["sound"], cfg["sound_volume"])
        self._target = None          # angle the current spin is heading for
        self._landed = True          # True once the current spin has reached its chamber
        if self.sounds.ok and self.sounds.live is None:      # winsound fallback needs pre-rendered spins
            threading.Thread(target=self._prewarm_sounds, daemon=True).start()
        self.hover_pop = False       # popped because the mouse is over the cylinder
        self._inside_polls = 0
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._poll_hover)
        self._hover_timer.start(30)

        if ipc:
            ipc.state.connect(self.set_state)
            ipc.online.connect(self._set_online)

    def _apply_geometry(self):
        """(Re)compute sizes, window size/position, click mask and outline from the config."""
        cfg = self.cfg
        fx, fy, _ = CORNERS[cfg["corner"]]
        if cfg["bar_height"] > 0:
            # put the focused chamber's center on the bar's center line
            # (base_scale > 1 makes everything proportionally bigger, so the focused
            #  chamber then sits a little below that line)
            k = float(cfg["base_scale"])
            d = cfg["bar_margin"] + cfg["bar_height"] / 2.0
            self.ring_r = d / math.sin(math.radians(45)) * k
            self.hole_r = min(cfg["chamber_size"] / 2.0 * k,
                              self.ring_r * math.sin(math.pi / self.n) * 0.9)
            R = int(math.ceil(self.ring_r + self.hole_r + 5 * k))
            # cut the cylinder flat at the bottom edge of your widget groups
            if cfg["flat_cut"] and cfg["pill_height"] > 0:
                H = int(round((d + cfg["pill_height"] / 2.0) * k))
                # never cut through the focused chamber: it sits ring_r*sin45 from the edge
                H = max(H, int(math.ceil(self.ring_r * math.sin(math.radians(45)) + self.hole_r + 3)))
                H = min(H, R)
            else:
                H = R                      # full quarter circle
        else:
            R = int(cfg["radius"])
            self.ring_r = R * 0.62
            self.hole_r = min(self.ring_r * math.sin(math.pi / self.n) * 0.82,
                              (R - 2 - self.ring_r) * 0.85)
            H = R
        self.R = R
        self.W, self.H = R, H                       # resting size of the visible shape
        self.pop = max(1.0, float(cfg["pop_scale"]))
        self.WW = int(math.ceil(self.W * self.pop)) + 2   # window has room to grow
        self.WH = int(math.ceil(self.R * self.pop)) + 2
        self.body_r = R - 2.0
        self.cx, self.cy = fx * self.WW, fy * self.WH
        self.setFixedSize(self.WW, self.WH)
        self._place()

        # only the quarter disc captures clicks; the rest of the window is click-through
        disc = QRegion(int(self.cx - R), int(self.cy - R), 2 * R, 2 * R, QRegion.RegionType.Ellipse)
        x0 = self.WW - self.W if fx else 0
        y0 = self.WH - self.H if fy else 0
        self.rest_mask = disc.intersected(QRegion(x0, y0, self.W, self.H))
        if self._popped:
            self.clearMask()
        else:
            self.setMask(self.rest_mask)
        self.shape = self._build_shape()
        self.update()

    def yasb_padding(self):
        return self.W + int(self.cfg["gap"])

    def _build_palette(self):
        c = self.cfg

        def a(col, alpha):
            x = QColor(col)
            x.setAlpha(alpha)
            return x
        accent, body, border = qcolor(c["accent_color"]), qcolor(c["body_color"]), qcolor(c["border_color"])
        chamber, rim, text = qcolor(c["chamber_color"]), qcolor(c["chamber_rim_color"]), qcolor(c["text_color"])
        self.pal = {
            "body": body, "border": border, "accent": accent,
            "track": a(border, 90),
            "axle": border.darker(180),
            "glow0": a(accent, 130), "glow1": a(accent, 0),
            "active_rim": QColor(255, 255, 255, 235),
            "on_accent": auto_text_color(accent),
            "hover_fill": a(rim, 64), "hover_rim": a(rim, 128),
            "loaded_fill": a(chamber, 128), "loaded_rim": a(rim, 77),
            "empty_fill": a(chamber, 70), "empty_rim": a(text, 40),
            "text": text, "text_dim": a(text, 110),
        }

    GEOMETRY_KEYS = ("base_scale", "pop_scale", "flat_cut")
    COLOR_KEYS = ("accent_color", "body_color", "border_color", "chamber_color",
                  "chamber_rim_color", "text_color")

    def apply_setting(self, key, value):
        """Change one setting live and save it to revolver_config.json."""
        self.cfg[key] = value
        if key in self.GEOMETRY_KEYS:
            self._apply_geometry()
        elif key in self.COLOR_KEYS:
            self._build_palette()
        elif key == "sound":
            self.sounds.set_enabled(bool(value))
            if self.sounds.ok and self.sounds.live is None:
                threading.Thread(target=self._prewarm_sounds, daemon=True).start()
        elif key == "sound_volume":
            self.sounds.set_volume(value)
        self.update()
        save_config(self.cfg)

    def open_settings(self):
        panel = getattr(self, "_panel", None)
        if panel is None:
            panel = self._panel = SettingsPanel(self)
        panel.refresh()
        size = panel.sizeHint()
        if self.cfg["corner"].endswith("right"):
            x = self.x() + self.WW - self.W - size.width() - 24
        else:
            x = self.x() + self.W + 24
        if self.cfg["corner"].startswith("bottom"):
            y = self.y() + self.WH - self.H + self.H - size.height() - 24
        else:
            y = self.y() + 24
        panel.move(x, y)
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def _inside_shape(self, pos):
        """Is a window-coordinate point on the visible (possibly enlarged) cylinder?"""
        k = self.scale or 1.0
        p = QPointF(self.cx + (pos.x() - self.cx) / k, self.cy + (pos.y() - self.cy) / k)
        return self._current_shape().contains(p)

    def _place(self):
        screens = QGuiApplication.screens()
        g = screens[min(int(self.cfg["screen"]), len(screens) - 1)].geometry()
        right = self.cfg["corner"].endswith("right")
        bottom = self.cfg["corner"].startswith("bottom")
        x = g.left() + g.width() - self.WW - self.cfg["offset_x"] if right else g.left() + self.cfg["offset_x"]
        y = g.top() + g.height() - self.WH - self.cfg["offset_y"] if bottom else g.top() + self.cfg["offset_y"]
        self.move(x, y)

    def _build_shape(self, cut=None, rounded=True):
        """Quarter disc; with flat_cut on it is cut flat at `cut` px (default: resting H)."""
        cut_h = self.H if cut is None else cut
        far = 3 * self.R
        disc = QPainterPath()
        disc.addEllipse(QPointF(0, 0), self.body_r, self.body_r)
        box = QPainterPath()   # extends off-screen on the corner side so those edges never show
        box.addRect(QRectF(-far, -far, far + self.W, far + cut_h - 0.5))
        path = disc.intersected(box)
        try:   # round the convex corner where the arc meets the flat bottom
            if not rounded or cut_h >= self.body_r:
                raise ValueError
            st = QPainterPathStroker()
            st.setWidth(10.0)
            st.setJoinStyle(Qt.RoundJoin)
            st.setCapStyle(Qt.RoundCap)
            eroded = path.subtracted(st.createStroke(path))
            path = eroded.united(st.createStroke(eroded))
        except Exception:
            pass
        sx = -1.0 if self.cfg["corner"].endswith("right") else 1.0
        sy = -1.0 if self.cfg["corner"].startswith("bottom") else 1.0
        t = QTransform()
        t.translate(self.cx, self.cy)
        t.scale(sx, sy)
        return t.map(path)

    def _current_shape(self):
        # while popped, let the flat cut slide away so the cylinder becomes a full circle
        if self.cfg["flat_cut"] and self.pop > 1.0 and self.scale > 1.001 and self.H < self.R:
            t = min(1.0, (self.scale - 1.0) / (self.pop - 1.0))
            return self._build_shape(self.H + (self.R - self.H) * t, rounded=False)
        return self.shape

    def _check_fullscreen(self):
        fs = fullscreen_active()
        if fs and self.isVisible():
            self.hide()
        elif not fs and not self.isVisible():
            self.show()

    # ----- state -----
    def _set_online(self, ok):
        self.is_online = ok
        self.update()

    def _prewarm_sounds(self):
        """Render the sound for every possible fresh spin in the background (winsound fallback only)."""
        try:
            for frm in range(self.n):
                start = self.fire - frm * self.step
                for to in range(self.n):
                    if to == frm:
                        continue
                    target, total, duration, s_val, _ = self._plan_spin(start, to, frm, True)
                    if total > 1.0:
                        ticks, land = spin_timeline(start, target, self.fire, self.step, duration, s_val)
                        self.sounds.prepare(ticks, land)
        except Exception as exc:
            _note(f"sound prewarm failed: {exc!r}")

    def _on_angle(self, v):
        prev = self.angle
        self.angle = float(v)
        if self.sounds.live is not None:
            self._click_check(prev, self.angle)
        self.update()

    def _click_check(self, a0, a1):
        """Tick whenever a chamber passes the firing position; clack when the target chamber locks in."""
        if self._landed:
            return
        k0 = math.floor((a0 - self.fire) / self.step + 1e-9)
        k1 = math.floor((a1 - self.fire) / self.step + 1e-9)
        if k0 == k1:
            return
        boundary = self.fire + (k1 if a1 > a0 else k0) * self.step
        if self._target is not None and abs(boundary - self._target) < 0.01:
            self._landed = True
            self.sounds.trigger("clack")
        else:
            self.sounds.trigger("tick")

    def _on_spin_finished(self):
        if self.sounds.live is not None and not self._landed:   # no overshoot: lock-in never crossed
            self._landed = True
            self.sounds.trigger("clack")
        self._shrink()

    def set_state(self, focused, loaded):
        loaded = set(loaded)
        changed = loaded != self.loaded
        self.loaded = loaded
        if focused is not None and focused != self.focused:
            self.focused = focused
            self.spin_to(focused)
        elif changed:
            self.update()

    def _plan_spin(self, start, index, prev_index, fresh):
        """Work out one spin: (target angle, travel, duration ms, easing s, extra rotation degrees)."""
        base = shortest_target(start, self.fire - index * self.step)
        n_extra = int(self.cfg["extra_spins"])
        extra = 0.0
        # Extra rotations only for a fresh spin that jumps far enough (1->3 yes, 1->2 no). If you
        # are switching fast and the wheel is already turning, just redirect it.
        if (fresh and n_extra > 0 and abs(base - start) > 0.5
                and abs(index - prev_index) >= int(self.cfg["long_spin_min_distance"])):
            extra = (1.0 if base > start else -1.0) * 360.0 * n_extra
        target = base + extra
        total = abs(target - start)
        s_val = 1.70158                                   # Qt's default OutBack overshoot
        if total > 1.0:
            s_val = back_s_for_overshoot(self.cfg["settle_overshoot_deg"] / total)
        duration = self.cfg["spin_ms"] + (n_extra * self.cfg["spin_ms_per_extra"] if extra else 0)
        return target, total, duration, s_val, extra

    def spin_to(self, index):
        fresh = self.anim.state() != QVariantAnimation.State.Running
        prev_index, self._last_index = self._last_index, index
        start_angle = self.angle
        target, total, duration, s_val, extra = self._plan_spin(start_angle, index, prev_index, fresh)

        curve = QEasingCurve(QEasingCurve.OutBack)
        if total > 1.0:
            curve.setOvershoot(s_val)

        self._target = target
        self._landed = total <= 1.0
        self.anim.stop()
        self.anim.setStartValue(start_angle)
        self.anim.setEndValue(target)
        self.anim.setDuration(duration)
        self.anim.setEasingCurve(curve)
        self.anim.start()
        if total > 1.0 and self.sounds.live is None:
            ticks, land = spin_timeline(start_angle, target, self.fire, self.step, duration, s_val)
            self.sounds.play_spin(ticks, land)
        self._grow()

    # ----- pop (scale) animation -----
    def _retarget_scale(self, end, ms, curve):
        self.scale_anim.stop()
        self.scale_anim.setStartValue(self.scale)
        self.scale_anim.setEndValue(end)
        self.scale_anim.setDuration(ms)
        self.scale_anim.setEasingCurve(curve)
        self.scale_anim.start()

    def _grow(self):
        if self.pop > 1.0:
            self._retarget_scale(self.pop, self.cfg["pop_grow_ms"], QEasingCurve.OutCubic)

    def _shrink(self):
        if self.pop > 1.0 and not self.hover_pop and self.anim.state() != QVariantAnimation.State.Running:
            self._retarget_scale(1.0, self.cfg["pop_shrink_ms"], QEasingCurve.InOutCubic)

    def _poll_hover(self):
        """Show the pop size while the mouse is over the cylinder. Polled (not enter/leave events)
        because the click mask changes size as the cylinder grows and shrinks."""
        if not self.cfg["hover_pop"] or self.pop <= 1.0 or not self.isVisible():
            return
        inside = self._inside_shape(self.mapFromGlobal(QCursor.pos()))
        self._inside_polls = self._inside_polls + 1 if inside else 0
        if not self.hover_pop and self._inside_polls >= 2:           # ~60 ms dwell, so a quick pass doesn't pop it
            self.hover_pop = True
            self._grow()
        elif self.hover_pop and not inside:
            self.hover_pop = False
            if self.anim.state() != QVariantAnimation.State.Running:
                self._shrink()

    def _on_scale(self, v):
        self.scale = float(v)
        grown = self.scale > 1.001
        if grown and not self._popped:
            self.clearMask()               # let the enlarged cylinder draw outside its resting area
            self._popped = True
        elif not grown and self._popped:
            self.setMask(self.rest_mask)   # back to a tight click area
            self._popped = False
        self.update()

    # ----- hit testing -----
    def chamber_at(self, pos):
        k = self.scale or 1.0
        pos = QPointF(self.cx + (pos.x() - self.cx) / k, self.cy + (pos.y() - self.cy) / k)
        for i in range(self.n):
            x, y = chamber_center(i, self.n, self.angle, self.cx, self.cy, self.ring_r)
            if math.hypot(pos.x() - x, pos.y() - y) <= self.hole_r + 3:
                return i
        return -1

    def request_switch(self, index):
        """Ask the slide script to switch (so the windows slide); fall back to a direct command."""
        if not self.ipc:
            return
        port = int(self.cfg.get("slide_port") or 0)
        name = self.names[index]
        if not port or name not in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            self.ipc.focus_workspace(index)
            return
        sock = QTcpSocket(self)
        state = {"connected": False}

        def on_connected():
            state["connected"] = True
            sock.write((name + "\n").encode("ascii"))
            sock.flush()
            sock.disconnectFromHost()

        def on_error(*_):
            if not state["connected"]:
                self.ipc.focus_workspace(index)     # slide script isn't running: plain switch
            sock.deleteLater()

        sock.connected.connect(on_connected)
        sock.errorOccurred.connect(on_error)
        sock.disconnected.connect(sock.deleteLater)
        sock.connectToHost("127.0.0.1", port)

    # ----- input -----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            i = self.chamber_at(e.position())
            if i >= 0:
                if self.ipc:
                    self.focused = i        # optimistic: start spinning immediately
                    self.spin_to(i)
                    self.request_switch(i)
            elif self._inside_shape(e.position()):
                self.open_settings()        # clicked the cylinder body, not a chamber
        elif e.button() == Qt.RightButton:
            menu = QMenu(self)
            menu.addAction("Settings…", self.open_settings)
            menu.addAction("Quit revolver", QApplication.quit)
            menu.exec(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        h = self.chamber_at(e.position())
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.PointingHandCursor if h >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def wheelEvent(self, e):
        if not self.ipc:
            return
        d = -1 if e.angleDelta().y() > 0 else 1
        i = (self.focused + d) % self.n
        self.focused = i
        self.spin_to(i)
        self.request_switch(i)

    # ----- painting -----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        if not self.is_online and self.ipc:
            p.setOpacity(0.45)
        c = QPointF(self.cx, self.cy)
        p.translate(c)
        p.scale(self.scale, self.scale)
        p.translate(-c)

        # cylinder body: same glassy dark + 1px steel border as the YASB groups
        p.setPen(QPen(self.pal["border"], 1.0))
        p.setBrush(self.pal["body"])
        shape = self._current_shape()
        self._cur_shape = shape
        p.drawPath(shape)

        # faint track the chambers ride on
        p.setPen(QPen(self.pal["track"], 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, self.ring_r, self.ring_r)

        # chambers
        font = QFont(self.cfg["font"])
        font.setBold(True)
        font.setPixelSize(max(8, int(self.hole_r * 0.95)))
        p.setFont(font)
        for i in range(self.n):
            self._draw_chamber(p, i)

        # axle cap in the screen corner
        p.setPen(QPen(self.pal["border"], 1.5))
        p.setBrush(self.pal["axle"])
        p.drawEllipse(c, self.R * 0.13, self.R * 0.13)

        # firing marker on the rim, pointing at the active chamber
        p.save()
        p.translate(c)
        p.rotate(self.fire)
        p.setPen(Qt.NoPen)
        p.setBrush(self.pal["accent"])
        w, h = self.R * 0.05, self.R * 0.07
        edge = self.body_r - 1.5
        p.drawPolygon(QPolygonF([
            QPointF(-w, -edge), QPointF(w, -edge), QPointF(0, -edge + h),
        ]))
        p.restore()
        p.end()

    def _draw_chamber(self, p, i):
        pal = self.pal
        x, y = chamber_center(i, self.n, self.angle, self.cx, self.cy, self.ring_r)
        c = QPointF(x, y)
        r = self.hole_r
        is_active = i == self.focused
        is_loaded = i in self.loaded
        is_hover = i == self.hover

        if is_active:
            # filled highlight + glow, like YASB's active workspace button
            glow = QRadialGradient(c, r + 10)
            glow.setColorAt(0.6, pal["glow0"])
            glow.setColorAt(1.0, pal["glow1"])
            p.save()
            p.setClipPath(getattr(self, '_cur_shape', self.shape))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(c, r + 10, r + 10)
            p.restore()
            p.setPen(QPen(pal["active_rim"], 1.5))
            p.setBrush(pal["accent"])
            text = pal["on_accent"]
        elif is_hover:
            p.setPen(QPen(pal["hover_rim"], 1.2))
            p.setBrush(pal["hover_fill"])
            text = pal["text"]
        elif is_loaded:
            p.setPen(QPen(pal["loaded_rim"], 1.2))
            p.setBrush(pal["loaded_fill"])
            text = pal["text"]
        else:
            p.setPen(QPen(pal["empty_rim"], 1.2))
            p.setBrush(pal["empty_fill"])
            text = pal["text_dim"]
        p.drawEllipse(c, r, r)

        # number (kept upright while the cylinder turns)
        p.setPen(text)
        p.drawText(QRectF(x - r, y - r, 2 * r, 2 * r), Qt.AlignCenter, self.names[i])


# --------------------------------------------------------------------------
def main():
    demo = "--demo" in sys.argv
    cfg = load_config()
    app = QApplication(sys.argv)

    if demo:
        w = Revolver(cfg, ipc=None)
        w.is_online = True
        w.set_state(0, {0, 2, 3})
        idx = {"i": 0}

        def tick():
            idx["i"] = (idx["i"] + 1) % w.n
            w.set_state(idx["i"], {0, 2, 3, idx["i"]})

        t = QTimer()
        t.timeout.connect(tick)
        t.start(1400)
        w._demo_timer = t
    else:
        ipc = GlazeIpc(cfg["port"], cfg["workspace_names"])
        w = Revolver(cfg, ipc)
        ipc.start()

    print(f"cylinder is {w.W}x{w.H}px -> set bars.<your-bar>.padding.{cfg['corner'].split('-')[1]} to {w.W + cfg['gap']} in YASB config.yaml")
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()