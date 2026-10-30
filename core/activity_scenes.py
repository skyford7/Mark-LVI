"""
Activity scenes — the HUD centrepiece changes with what JARVIS is doing.

The head is a good face for conversation and a dull one for everything else:
searching the web, checking the weather, setting a reminder and reading a file
all looked identical. This module gives each kind of work its own animated
scene and its own colour, and the HUD cross-fades to it while the work happens
and while JARVIS talks about the result, then fades back to the face.

Two things choose a scene:

  * A TOOL CALL (strong signal, priority 2) — `scene_for_tool(name, args)`.
    Whatever tool runs decides the scene, and nothing from speech overrides it.
  * WHAT HE IS SAYING (weak signal, priority 1) — `scene_for_speech(text)`.
    Keywords in his own transcript (weather, the time, news, an apology…) pick
    a scene when no tool did, so a plain chat about the weather still shows it.

Everything is drawn with QPainter in software, same budget as the head: no
images, no GPU, no new dependencies. Each painter takes the same arguments and
can be previewed on its own — see `render_preview()` at the bottom.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, QLineF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPolygonF, QPainterPath


# ── catalogue ────────────────────────────────────────────────────────────────
# kind -> (accent colour, status label shown under the scene)
SCENES: dict[str, tuple[str, str]] = {
    "web":      ("#3fa9ff", "SEARCHING THE WEB"),
    "weather":  ("#ffb347", "CHECKING THE WEATHER"),
    "apps":     ("#00e5ff", "OPERATING THE COMPUTER"),
    "settings": ("#7dffb0", "ADJUSTING SETTINGS"),
    "media":    ("#ff4fd8", "MEDIA"),
    "files":    ("#ffd84f", "WORKING WITH FILES"),
    "time":     ("#9fa8ff", "TIME & REMINDERS"),
    "message":  ("#4fffa0", "MESSAGING"),
    "vision":   ("#ff5f5f", "LOOKING"),
    "code":     ("#39ff14", "WRITING CODE"),
    "memory":   ("#b388ff", "MEMORY"),
    "system":   ("#00ffc8", "SYSTEM STATUS"),
    "travel":   ("#ffe082", "SEARCHING FLIGHTS"),
    "news":     ("#ff9e40", "NEWS"),
    "alert":    ("#ff6b35", "ATTENTION"),
    "greet":    ("#ffd166", "GOOD TO SEE YOU"),
}

# Tool name -> scene kind. Anything not listed falls through to keyword
# matching on the name, so plugins added later still get a sensible scene.
_TOOL_SCENES: dict[str, str] = {
    "web_search": "web", "browser_control": "web",
    "weather_report": "weather",
    "open_app": "apps", "desktop_control": "apps", "computer_control": "apps",
    "game_updater": "apps",
    "computer_settings": "settings", "undo": "settings",
    "youtube_video": "media", "video_player": "media",
    "file_controller": "files", "file_processor": "files",
    "reminder": "time",
    "send_message": "message",
    "screen_process": "vision", "close_camera": "vision",
    "code_helper": "code", "dev_agent": "code",
    "recall_memory": "memory",
    "system_status": "system", "manage_monitor": "system",
    "flight_finder": "travel",
    "shutdown_jarvis": "alert",
}

# Tools that happen silently in the background; showing a scene for them
# would make the HUD flicker for no visible reason.
_SILENT_TOOLS = {"save_memory"}

_NAME_HINTS = (
    (("music", "spotify", "song", "audio", "youtube", "video"), "media"),
    (("mail", "gmail", "whatsapp", "chat", "message", "sms"), "message"),
    (("calendar", "remind", "alarm", "timer", "clock"), "time"),
    (("light", "home", "tuya", "printer", "volume", "bright"), "settings"),
    (("file", "excel", "pdf", "doc", "pptx"), "files"),
    (("search", "web", "browser", "news"), "web"),
    (("code", "dev", "git"), "code"),
    (("screen", "camera", "vision", "pushup"), "vision"),
    (("weather",), "weather"),
)

_LABEL_OVERRIDES = {
    "open_app": "OPENING APP",
    "youtube_video": "PLAYING VIDEO",
    "video_player": "PLAYING VIDEO",
    "recall_memory": "SEARCHING MEMORY",
    "reminder": "SETTING A REMINDER",
    "undo": "UNDOING",
    "send_message": "SENDING A MESSAGE",
    "file_processor": "READING A FILE",
    "shutdown_jarvis": "POWER ACTION",
}


def scene_for_tool(name: str, args: dict | None = None) -> tuple[str, str] | None:
    """(kind, label) for a tool call, or None when it should not show a scene."""
    name = str(name or "").strip()
    if not name or name in _SILENT_TOOLS:
        return None
    args = args or {}
    kind = _TOOL_SCENES.get(name)
    if kind is None:
        low = name.lower()
        for words, k in _NAME_HINTS:
            if any(w in low for w in words):
                kind = k
                break
        else:
            kind = "apps"
    label = _LABEL_OVERRIDES.get(name, SCENES[kind][1])
    if name == "screen_process":
        label = ("LOOKING THROUGH THE CAMERA"
                 if str(args.get("angle", "")).lower() == "camera"
                 else "READING THE SCREEN")
    elif name == "open_app" and args.get("app_name"):
        label = f"OPENING {str(args['app_name']).upper()[:22]}"
    elif kind == "apps" and name not in _TOOL_SCENES:
        label = name.replace("_", " ").upper()[:28]
    return kind, label


# Speech keywords, checked in order — the first match wins, so an apology
# about the weather shows the apology.
_SPEECH_RULES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (k, re.compile(p, re.I)) for k, p in (
        ("alert",   r"\b(sorry|unfortunately|unable to|couldn'?t|can'?t|cannot|"
                    r"error|failed|warning|problem|not available)\b"),
        ("weather", r"\b(weather|temperature|degrees|forecast|raining|rainy|rain|"
                    r"sunny|cloudy|snow(ing)?|windy|humid(ity)?|celsius)\b"),
        ("time",    r"\b(o'?clock|the time is|it is \d{1,2}[: ]\d{2}|"
                    r"\d{1,2}[: ]\d{2}\s?(am|pm|a\.m\.|p\.m\.)|reminder|alarm)\b"),
        ("news",    r"\b(news|headlines?|breaking|reported|reports)\b"),
        ("media",   r"\b(music|song|playlist|album|now playing)\b"),
        ("greet",   r"\b(good (morning|afternoon|evening)|hello|welcome back|"
                    r"hi there|nice to see you)\b"),
        ("code",    r"\b(python|javascript|function|script|code)\b"),
        ("web",     r"\b(searched|searching|online|website|internet)\b"),
    ))


def scene_for_speech(text: str) -> tuple[str, str] | None:
    """(kind, label) from what JARVIS is currently saying, or None."""
    tail = str(text or "")[-240:]
    if len(tail) < 4:
        return None
    for kind, rx in _SPEECH_RULES:
        if rx.search(tail):
            return kind, SCENES[kind][1]
    return None


def scene_colour(kind: str) -> QColor:
    return QColor(SCENES.get(kind, ("#00e5ff", ""))[0])


# ── drawing helpers ──────────────────────────────────────────────────────────

class _Ctx:
    """Per-frame drawing context shared by every scene painter."""
    __slots__ = ("p", "cx", "cy", "r", "t", "amp", "acc", "main", "bg")

    def __init__(self, p, cx, cy, r, t, amp, acc, main, bg):
        self.p, self.cx, self.cy, self.r = p, cx, cy, r
        self.t, self.amp = t, amp
        self.acc, self.main, self.bg = acc, main, bg

    def mix(self, col: QColor, a: float) -> QColor:
        """Pre-blend onto the background — same trick the reactor core uses,
        because the raster engine's opaque path is much faster."""
        k = max(0.0, min(1.0, a))
        bg = self.bg
        return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                      int(bg.green() + (col.green() - bg.green()) * k),
                      int(bg.blue()  + (col.blue()  - bg.blue())  * k))

    def pen(self, col: QColor, a: float, w: float = 1.4):
        self.p.setPen(QPen(self.mix(col, a), w))

    def circle(self, x, y, rad):
        self.p.drawEllipse(QRectF(x - rad, y - rad, rad * 2, rad * 2))


def _frame(c: _Ctx) -> None:
    """The shared frame every scene sits in: a soft glow, two rings, rotating
    arcs and a voice ring that grows when JARVIS speaks."""
    p, cx, cy, r, t, amp = c.p, c.cx, c.cy, c.r, c.t, c.amp
    from PyQt6.QtGui import QRadialGradient
    p.setPen(Qt.PenStyle.NoPen)
    g = QRadialGradient(cx, cy, r * 0.95)
    lift = 1.0 + amp * 0.8
    g.setColorAt(0.0, c.mix(c.acc, min(0.9, 0.22 * lift)))
    g.setColorAt(0.6, c.mix(c.acc, min(0.9, 0.07 * lift)))
    g.setColorAt(1.0, c.mix(c.acc, 0.0))
    p.setBrush(QBrush(g))
    c.circle(cx, cy, r * 0.95)
    p.setBrush(Qt.BrushStyle.NoBrush)

    c.pen(c.acc, 0.40, 1.2); c.circle(cx, cy, r)
    c.pen(c.acc, 0.16, 1.0); c.circle(cx, cy, r * 0.93)

    for k, (rr, span, n, d, a, w) in enumerate((
            (0.965, 110, 2, +1, 0.85, 2.2),
            (0.875, 70, 3, -1, 0.45, 1.4),
            (0.80, 150, 1, +1, 0.35, 1.2))):
        rad = r * rr
        box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
        c.pen(c.acc, a, w)
        base = (t * (28 + k * 17) * d) % 360.0
        for s in range(n):
            p.drawArc(box, int((base + s * 360.0 / n) * 16), int(span * 16))

    # voice ring: ticks that grow with the audio level
    ring, lines = r * 0.74, []
    for i in range(72):
        a = math.radians(i * 5.0)
        wob = 0.5 + 0.5 * math.sin(t * 3.1 + i * 0.5)
        h = r * (0.012 + amp * 0.10 * wob)
        lines.append(QLineF(cx + math.cos(a) * ring, cy + math.sin(a) * ring,
                            cx + math.cos(a) * (ring + h), cy + math.sin(a) * (ring + h)))
    c.pen(c.acc, 0.30 + 0.6 * amp, 1.5)
    p.drawLines(lines)


# ── the scenes ───────────────────────────────────────────────────────────────
# Each draws inside a radius of ~0.62 r so it never touches the voice ring.

def _web(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.52
    c.pen(c.acc, 0.85, 1.8); c.circle(cx, cy, R)
    # meridians: ellipses whose width follows a rotating angle
    for k in range(6):
        ph = c.t * 0.9 + k * math.pi / 6
        w = abs(math.cos(ph)) * R
        front = math.sin(ph) > 0
        c.pen(c.acc, 0.70 if front else 0.25, 1.3)
        p.drawEllipse(QRectF(cx - w, cy - R, w * 2, R * 2))
    # parallels
    for k in (-2, -1, 0, 1, 2):
        y = cy + k * R * 0.36
        half = math.sqrt(max(0.0, R * R - (y - cy) ** 2))
        c.pen(c.acc, 0.45, 1.1)
        p.drawEllipse(QRectF(cx - half, y - half * 0.12, half * 2, half * 0.24))
    # a search ping travelling round the equator
    a = c.t * 2.2
    x, y = cx + math.cos(a) * R * 1.12, cy + math.sin(a) * R * 0.30
    p.setBrush(QBrush(c.mix(QColor("#ffffff"), 0.9)))
    p.setPen(Qt.PenStyle.NoPen)
    c.circle(x, y, max(2.5, R * 0.05))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _weather(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    sx, sy, sr = cx - R * 0.22, cy - R * 0.20, R * 0.40
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c.mix(c.acc, 0.85)))
    c.circle(sx, sy, sr)
    p.setBrush(Qt.BrushStyle.NoBrush)
    rays = []
    for i in range(12):
        a = math.radians(i * 30) + c.t * 0.6
        r0 = sr * 1.25
        r1 = sr * (1.55 + 0.12 * math.sin(c.t * 4 + i))
        rays.append(QLineF(sx + math.cos(a) * r0, sy + math.sin(a) * r0,
                           sx + math.cos(a) * r1, sy + math.sin(a) * r1))
    c.pen(c.acc, 0.9, 2.4)
    p.drawLines(rays)
    # cloud drifting across the sun
    dx = math.sin(c.t * 0.5) * R * 0.18
    ccx, ccy = cx + R * 0.18 + dx, cy + R * 0.22
    path = QPainterPath()
    for ox, oy, rr in ((-0.42, 0.05, 0.26), (-0.12, -0.12, 0.34),
                       (0.25, -0.02, 0.28), (0.48, 0.10, 0.20)):
        path.addEllipse(QPointF(ccx + ox * R, ccy + oy * R), rr * R, rr * R)
    path.addRoundedRect(QRectF(ccx - 0.62 * R, ccy, 1.24 * R, 0.30 * R), 0.14 * R, 0.14 * R)
    path.setFillRule(Qt.FillRule.WindingFill)   # merge the puffs into one outline
    path = path.simplified()
    p.setBrush(QBrush(c.mix(QColor("#dfefff"), 0.92)))
    c.pen(QColor("#ffffff"), 0.95, 1.6)
    p.drawPath(path)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # rain streaks under the cloud
    c.pen(QColor("#6fc3ff"), 0.8, 1.6)
    for i in range(6):
        x = ccx - 0.45 * R + i * 0.18 * R
        ph = (c.t * 1.6 + i * 0.37) % 1.0
        y0 = ccy + 0.36 * R + ph * 0.38 * R
        p.drawLine(QLineF(x, y0, x - 0.05 * R, y0 + 0.10 * R))


def _apps(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.50
    n, gap = 3, R * 0.10
    cell = (R * 1.6 - gap * (n - 1)) / n
    x0, y0 = cx - R * 0.8, cy - R * 0.8
    lit = int(c.t * 5) % (n * n)
    for i in range(n * n):
        x = x0 + (i % n) * (cell + gap)
        y = y0 + (i // n) * (cell + gap)
        on = i == lit
        p.setBrush(QBrush(c.mix(c.acc, 0.85 if on else 0.12)))
        c.pen(c.acc, 0.9 if on else 0.45, 1.6 if on else 1.1)
        p.drawRoundedRect(QRectF(x, y, cell, cell), cell * 0.22, cell * 0.22)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # cursor gliding between tiles
    i = lit
    x = x0 + (i % n) * (cell + gap) + cell * 0.7
    y = y0 + (i // n) * (cell + gap) + cell * 0.7
    tri = QPolygonF([QPointF(x, y), QPointF(x, y + cell * 0.45),
                     QPointF(x + cell * 0.13, y + cell * 0.33),
                     QPointF(x + cell * 0.32, y + cell * 0.33)])
    p.setBrush(QBrush(QColor("#ffffff")))
    p.setPen(QPen(c.bg, 1.2))
    p.drawPolygon(tri)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _gear(c: _Ctx, x, y, rad, teeth, rot, a):
    pts = []
    for i in range(teeth * 2):
        ang = rot + i * math.pi / teeth
        rr = rad if i % 2 == 0 else rad * 0.80
        for da in (-0.12, 0.12):
            pts.append(QPointF(x + math.cos(ang + da * (2 * math.pi / teeth)) * rr,
                               y + math.sin(ang + da * (2 * math.pi / teeth)) * rr))
    c.pen(c.acc, a, 1.8)
    c.p.drawPolygon(QPolygonF(pts))
    c.circle(x, y, rad * 0.32)


def _settings(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.52
    _gear(c, cx - R * 0.30, cy - R * 0.25, R * 0.42, 10, c.t * 0.8, 0.9)
    _gear(c, cx + R * 0.33, cy + R * 0.10, R * 0.28, 8, -c.t * 1.2 + 0.2, 0.6)
    # three sliders whose knobs move
    for i in range(3):
        y = cy + R * (0.55 + i * 0.17)
        c.pen(c.acc, 0.35, 2.0)
        p.drawLine(QLineF(cx - R * 0.7, y, cx + R * 0.7, y))
        k = 0.5 + 0.45 * math.sin(c.t * (0.9 + i * 0.4) + i)
        kx = cx - R * 0.7 + k * R * 1.4
        p.setBrush(QBrush(c.mix(c.acc, 0.95)))
        c.circle(kx, y, R * 0.055)
        p.setBrush(Qt.BrushStyle.NoBrush)


def _media(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.52
    tri = QPolygonF([QPointF(cx - R * 0.26, cy - R * 0.62),
                     QPointF(cx - R * 0.26, cy - R * 0.02),
                     QPointF(cx + R * 0.30, cy - R * 0.32)])
    p.setBrush(QBrush(c.mix(c.acc, 0.85)))
    c.pen(c.acc, 1.0, 1.6)
    p.drawPolygon(tri)
    p.setBrush(Qt.BrushStyle.NoBrush)
    n = 15
    bw = R * 1.5 / n
    for i in range(n):
        lvl = 0.25 + 0.75 * abs(math.sin(c.t * (2.0 + (i % 5) * 0.6) + i * 0.8))
        lvl = min(1.0, lvl * (0.6 + c.amp * 1.2))
        h = R * 0.62 * lvl
        x = cx - R * 0.75 + i * bw
        p.fillRect(QRectF(x, cy + R * 0.80 - h, bw * 0.7, h), c.mix(c.acc, 0.35 + 0.6 * lvl))


def _files(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.52
    w, h = R * 0.95, R * 1.25
    for k in (2, 1, 0):
        off = k * R * 0.10
        rect = QRectF(cx - w / 2 + off, cy - h / 2 - off, w, h)
        p.setBrush(QBrush(c.mix(c.acc, 0.10 + 0.05 * (2 - k))))
        c.pen(c.acc, 0.35 + 0.25 * (2 - k), 1.4)
        p.drawRoundedRect(rect, R * 0.05, R * 0.05)
    p.setBrush(Qt.BrushStyle.NoBrush)
    top = cy - h / 2
    c.pen(c.acc, 0.55, 1.6)
    for i in range(7):
        y = top + h * (0.18 + i * 0.11)
        ln = 0.55 + 0.30 * ((i * 37) % 10) / 10
        p.drawLine(QLineF(cx - w * 0.38, y, cx - w * 0.38 + w * 0.76 * ln, y))
    # scanning beam
    sy = top + h * (0.08 + 0.84 * (0.5 + 0.5 * math.sin(c.t * 1.8)))
    c.pen(QColor("#ffffff"), 0.9, 2.0)
    p.drawLine(QLineF(cx - w * 0.55, sy, cx + w * 0.55, sy))


def _time(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    c.pen(c.acc, 0.9, 2.0); c.circle(cx, cy, R)
    ticks = []
    for i in range(60):
        a = math.radians(i * 6)
        r0 = R * (0.80 if i % 5 == 0 else 0.90)
        ticks.append(QLineF(cx + math.cos(a) * r0, cy + math.sin(a) * r0,
                            cx + math.cos(a) * R * 0.96, cy + math.sin(a) * R * 0.96))
    c.pen(c.acc, 0.6, 1.4)
    p.drawLines(ticks)
    now = datetime.now()
    sec = now.second + now.microsecond / 1e6
    for frac, ln, w, col, a in (
            ((now.hour % 12 + now.minute / 60) / 12, 0.50, 4.0, c.acc, 0.95),
            ((now.minute + sec / 60) / 60, 0.74, 2.6, c.acc, 0.95),
            (sec / 60, 0.84, 1.4, QColor("#ffffff"), 0.9)):
        ang = frac * 2 * math.pi - math.pi / 2
        c.pen(col, a, w)
        p.drawLine(QLineF(cx, cy, cx + math.cos(ang) * R * ln, cy + math.sin(ang) * R * ln))
    p.setBrush(QBrush(c.mix(c.acc, 1.0)))
    c.circle(cx, cy, R * 0.05)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _message(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.52
    for k, (ox, oy, w, h, right) in enumerate(((-0.25, -0.30, 1.05, 0.55, False),
                                               (0.20, 0.32, 0.95, 0.50, True))):
        rect = QRectF(cx + (ox - w / 2) * R, cy + (oy - h / 2) * R, w * R, h * R)
        p.setBrush(QBrush(c.mix(c.acc, 0.22 if k == 0 else 0.55)))
        c.pen(c.acc, 0.9, 1.6)
        p.drawRoundedRect(rect, R * 0.16, R * 0.16)
        tx = rect.right() - R * 0.18 if right else rect.left() + R * 0.18
        tail = QPolygonF([QPointF(tx - R * 0.08, rect.bottom() - 1),
                          QPointF(tx + R * 0.08, rect.bottom() - 1),
                          QPointF(tx + (R * 0.14 if right else -R * 0.14), rect.bottom() + R * 0.14)])
        p.drawPolygon(tail)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # typing dots in the first bubble
    for i in range(3):
        up = max(0.0, math.sin(c.t * 5 - i * 0.7))
        p.setBrush(QBrush(c.mix(QColor("#ffffff"), 0.5 + 0.5 * up)))
        p.setPen(Qt.PenStyle.NoPen)
        c.circle(cx + (-0.48 + i * 0.22) * R, cy - 0.30 * R - up * R * 0.06, R * 0.055)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _vision(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    # eye outline
    path = QPainterPath()
    path.moveTo(cx - R, cy)
    path.quadTo(cx, cy - R * 0.85, cx + R, cy)
    path.quadTo(cx, cy + R * 0.85, cx - R, cy)
    c.pen(c.acc, 0.9, 2.0)
    p.drawPath(path)
    look = math.sin(c.t * 0.8) * R * 0.18
    c.pen(c.acc, 0.8, 1.6); c.circle(cx + look, cy, R * 0.34)
    p.setBrush(QBrush(c.mix(c.acc, 0.9)))
    c.circle(cx + look, cy, R * 0.14)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # corner brackets that breathe
    s = R * (1.05 + 0.05 * math.sin(c.t * 3))
    arm = R * 0.22
    c.pen(c.acc, 0.7, 1.8)
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x, y = cx + sx * s, cy + sy * s * 0.75
        p.drawLine(QLineF(x, y, x - sx * arm, y))
        p.drawLine(QLineF(x, y, x, y - sy * arm))
    y = cy + math.sin(c.t * 2.2) * R * 0.7
    c.pen(QColor("#ffffff"), 0.5, 1.2)
    p.drawLine(QLineF(cx - s, y, cx + s, y))


def _code(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    # falling columns of glyphs
    f = QFont("Courier New", max(7, int(R * 0.10)))
    p.setFont(f)
    glyphs = "01{}<>;=()[]#$"
    cols = 9
    for i in range(cols):
        x = cx - R * 0.85 + i * (R * 1.7 / (cols - 1))
        speed = 0.35 + (i * 7 % 5) * 0.08
        for j in range(5):
            ph = (c.t * speed + j * 0.2 + i * 0.13) % 1.0
            y = cy - R * 0.9 + ph * R * 1.8
            if abs(y - cy) < R * 0.25:
                continue
            ch = glyphs[(i * 3 + j * 5 + int(c.t * 4)) % len(glyphs)]
            c.pen(c.acc, 0.15 + 0.45 * (1 - ph), 1)
            p.drawText(QPointF(x, y), ch)
    big = QFont("Courier New", max(10, int(R * 0.38)), QFont.Weight.Bold)
    p.setFont(big)
    c.pen(c.acc, 0.95, 1)
    p.drawText(QRectF(cx - R, cy - R * 0.3, R * 2, R * 0.6),
               Qt.AlignmentFlag.AlignCenter, "</>")


def _memory(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    nodes = []
    for i in range(11):
        a = i * 2.39996 + c.t * 0.15          # golden-angle spiral, slowly turning
        rr = R * math.sqrt((i + 0.5) / 11)
        nodes.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
    for i, (x1, y1) in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            x2, y2 = nodes[j]
            d = math.hypot(x2 - x1, y2 - y1)
            if d < R * 0.75:
                pulse = 0.5 + 0.5 * math.sin(c.t * 3 + i + j)
                c.pen(c.acc, 0.15 + 0.45 * pulse * (1 - d / (R * 0.75)), 1.2)
                p.drawLine(QLineF(x1, y1, x2, y2))
    p.setPen(Qt.PenStyle.NoPen)
    for i, (x, y) in enumerate(nodes):
        pulse = 0.5 + 0.5 * math.sin(c.t * 2.5 + i)
        p.setBrush(QBrush(c.mix(c.acc, 0.6 + 0.4 * pulse)))
        c.circle(x, y, R * (0.035 + 0.025 * pulse))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _system(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.56
    for k in (1.0, 0.66, 0.33):
        c.pen(c.acc, 0.35, 1.1); c.circle(cx, cy, R * k)
    c.pen(c.acc, 0.25, 1.0)
    p.drawLine(QLineF(cx - R, cy, cx + R, cy))
    p.drawLine(QLineF(cx, cy - R, cx, cy + R))
    sweep = (c.t * 90.0) % 360.0
    for k in range(18):                      # fading wedge behind the beam
        a = math.radians(sweep - k * 2.2)
        c.pen(c.acc, 0.85 * (1 - k / 18), 2.0)
        p.drawLine(QLineF(cx, cy, cx + math.cos(a) * R, cy - math.sin(a) * R))
    p.setPen(Qt.PenStyle.NoPen)
    for i, (ang, dist) in enumerate(((40, 0.6), (130, 0.8), (210, 0.45),
                                     (300, 0.7), (75, 0.3))):
        since = (sweep - ang) % 360.0
        glow = max(0.0, 1.0 - since / 300.0)
        a = math.radians(ang)
        p.setBrush(QBrush(c.mix(QColor("#ffffff"), 0.2 + 0.8 * glow)))
        c.circle(cx + math.cos(a) * R * dist, cy - math.sin(a) * R * dist, R * 0.04)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _travel(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    a0, a1 = QPointF(cx - R * 0.85, cy + R * 0.35), QPointF(cx + R * 0.85, cy + R * 0.35)
    ctrl = QPointF(cx, cy - R * 0.85)
    path = QPainterPath(a0)
    path.quadTo(ctrl, a1)
    pen = QPen(c.mix(c.acc, 0.6), 1.8)
    pen.setStyle(Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.drawPath(path)
    for pt in (a0, a1):
        p.setBrush(QBrush(c.mix(c.acc, 0.95)))
        c.circle(pt.x(), pt.y(), R * 0.05)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = (c.t * 0.25) % 1.0
    x = (1 - s) ** 2 * a0.x() + 2 * (1 - s) * s * ctrl.x() + s * s * a1.x()
    y = (1 - s) ** 2 * a0.y() + 2 * (1 - s) * s * ctrl.y() + s * s * a1.y()
    dx = 2 * (1 - s) * (ctrl.x() - a0.x()) + 2 * s * (a1.x() - ctrl.x())
    dy = 2 * (1 - s) * (ctrl.y() - a0.y()) + 2 * s * (a1.y() - ctrl.y())
    ang = math.atan2(dy, dx)
    k = R * 0.12
    pts = [(1.2, 0), (-0.8, -0.7), (-0.4, 0), (-0.8, 0.7)]
    poly = QPolygonF([QPointF(x + (px * math.cos(ang) - py * math.sin(ang)) * k,
                              y + (px * math.sin(ang) + py * math.cos(ang)) * k)
                      for px, py in pts])
    p.setBrush(QBrush(QColor("#ffffff")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(poly)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _news(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.52
    rect = QRectF(cx - R * 0.8, cy - R * 0.75, R * 1.6, R * 1.5)
    p.setBrush(QBrush(c.mix(c.acc, 0.10)))
    c.pen(c.acc, 0.8, 1.6)
    p.drawRoundedRect(rect, R * 0.06, R * 0.06)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.fillRect(QRectF(rect.left() + R * 0.12, rect.top() + R * 0.14, R * 1.36, R * 0.22),
               c.mix(c.acc, 0.85))
    p.fillRect(QRectF(rect.left() + R * 0.12, rect.top() + R * 0.48, R * 0.55, R * 0.42),
               c.mix(c.acc, 0.35))
    c.pen(c.acc, 0.55, 1.4)
    for i in range(4):
        y = rect.top() + R * (0.52 + i * 0.11)
        p.drawLine(QLineF(rect.left() + R * 0.78, y, rect.right() - R * 0.12, y))
    # ticker along the bottom
    clip = QRectF(rect.left() + R * 0.12, rect.bottom() - R * 0.40, R * 1.36, R * 0.24)
    p.save()
    p.setClipRect(clip)
    off = (c.t * R * 0.5) % (R * 0.5)
    for i in range(6):
        x = clip.left() - off + i * R * 0.5
        p.fillRect(QRectF(x, clip.top() + R * 0.07, R * 0.36, R * 0.09), c.mix(c.acc, 0.7))
    p.restore()


def _alert(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    pulse = 0.5 + 0.5 * math.sin(c.t * 4.0)
    for k in range(3):
        rr = R * (0.75 + ((c.t * 0.6 + k / 3) % 1.0) * 0.45)
        a = 0.45 * (1 - ((c.t * 0.6 + k / 3) % 1.0))
        c.pen(c.acc, a, 1.4); c.circle(cx, cy, rr)
    tri = QPolygonF([QPointF(cx, cy - R * 0.62), QPointF(cx + R * 0.62, cy + R * 0.45),
                     QPointF(cx - R * 0.62, cy + R * 0.45)])
    p.setBrush(QBrush(c.mix(c.acc, 0.25 + 0.25 * pulse)))
    c.pen(c.acc, 0.95, 2.4)
    p.drawPolygon(tri)
    p.setBrush(Qt.BrushStyle.NoBrush)
    f = QFont("Courier New", max(10, int(R * 0.55)), QFont.Weight.Bold)
    p.setFont(f)
    c.pen(QColor("#ffffff"), 0.95, 1)
    p.drawText(QRectF(cx - R, cy - R * 0.30, R * 2, R * 0.75), Qt.AlignmentFlag.AlignCenter, "!")


def _greet(c: _Ctx):
    p, cx, cy, R = c.p, c.cx, c.cy, c.r * 0.55
    rise = 0.5 + 0.5 * math.sin(c.t * 0.7)
    hy = cy + R * 0.30
    sy = hy - R * (0.05 + 0.25 * rise)
    p.save()
    p.setClipRect(QRectF(cx - R * 1.2, cy - R * 1.2, R * 2.4, hy - (cy - R * 1.2)))
    rays = []
    for i in range(16):
        a = math.radians(i * 22.5) + c.t * 0.3
        rays.append(QLineF(cx + math.cos(a) * R * 0.55, sy + math.sin(a) * R * 0.55,
                           cx + math.cos(a) * R * 0.85, sy + math.sin(a) * R * 0.85))
    c.pen(c.acc, 0.8, 2.2)
    p.drawLines(rays)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c.mix(c.acc, 0.9)))
    c.circle(cx, sy, R * 0.42)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.restore()
    c.pen(c.acc, 0.9, 2.0)
    p.drawLine(QLineF(cx - R * 0.95, hy, cx + R * 0.95, hy))
    for k in range(1, 4):
        c.pen(c.acc, 0.5 / k, 1.4)
        w = R * (0.85 - k * 0.18)
        p.drawLine(QLineF(cx - w, hy + k * R * 0.12, cx + w, hy + k * R * 0.12))


_PAINTERS = {
    "web": _web, "weather": _weather, "apps": _apps, "settings": _settings,
    "media": _media, "files": _files, "time": _time, "message": _message,
    "vision": _vision, "code": _code, "memory": _memory, "system": _system,
    "travel": _travel, "news": _news, "alert": _alert, "greet": _greet,
}


def paint_scene(p: QPainter, kind: str, cx: float, cy: float, r: float,
                t: float, amp: float, bg: QColor) -> None:
    """Draw scene `kind` centred at (cx, cy) with outer radius r.

    `t` is a running time in seconds, `amp` the smoothed voice level 0..1.
    Never raises: a drawing bug must not take the HUD down with it.
    """
    try:
        acc = scene_colour(kind)
        c = _Ctx(p, cx, cy, r, t, max(0.0, min(1.0, amp)), acc, acc, bg)
        p.save()
        _frame(c)
        _PAINTERS.get(kind, _apps)(c)
        p.restore()
    except Exception:
        try:
            p.restore()
        except Exception:
            pass


def render_preview(path: str = "scenes_preview.png", size: int = 260) -> str:
    """Render every scene into one contact sheet — handy for checking the look
    without starting the whole assistant:  python -m core.activity_scenes"""
    from PyQt6.QtGui import QImage
    kinds = list(SCENES)
    cols = 4
    rows = (len(kinds) + cols - 1) // cols
    img = QImage(cols * size, rows * (size + 24), QImage.Format.Format_RGB32)
    bg = QColor("#000a12")
    img.fill(bg)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, k in enumerate(kinds):
        x = (i % cols) * size
        y = (i // cols) * (size + 24)
        paint_scene(p, k, x + size / 2, y + size / 2, size * 0.45, 1.7 + i, 0.4, bg)
        p.setPen(QPen(scene_colour(k), 1))
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.drawText(QRectF(x, y + size, size, 20), Qt.AlignmentFlag.AlignCenter, SCENES[k][1])
    p.end()
    img.save(path)
    return path


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    print(render_preview(sys.argv[1] if len(sys.argv) > 1 else "scenes_preview.png"))
