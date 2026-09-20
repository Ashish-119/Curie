"""Curie's face — a galaxy of stars on pure black.

The center is a living star cluster (dense core, tilted halo, slow rotation,
twinkle). Subsystem nodes sit around it like brighter named stars — each one
flares and fires an energy link into the galactic core while that part of
Curie is working. Only other element: the conversation panel.

States tint the galactic core:
    LISTENING → faint cyan breath
    THINKING  → amber shimmer, faster twinkle
    SPEAKING  → golden glow + waveform

Voice-only: no chat panel, no typed input, no visible conversation transcript
— the conversation lives in the terminal log only, by design.

Same interface as the old JarvisUI:
    .root.mainloop()  .set_state(s)  .write_log(t)  .wait_for_api_key()  .shutdown()
    .on_window_closed  (callback fired if the user closes the window directly)
"""
from __future__ import annotations

import datetime
import math
import random

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QMainWindow, QSizePolicy, QWidget

# ── palette: black space, white stars, colored accents ────────────────────────
BG         = QColor("#000000")
STAR_WHITE = QColor("#e8eef6")
CYAN       = QColor("#37c8f0")
GOLD       = QColor("#f2b23c")
AMBER      = QColor("#f2883c")
TEXT_DIM   = QColor("#6b7686")
TEXT_MAIN  = QColor("#e3e9f2")

_STATE_COLOR = {"LISTENING": CYAN, "THINKING": AMBER, "SPEAKING": GOLD}
_STATE_LABEL = {"LISTENING": "listening", "THINKING": "thinking", "SPEAKING": "speaking"}

# ── subsystem nodes: (key, label, rel_x, rel_y, color) ────────────────────────
NODE_DEFS = [
    ("hearing",  "Hearing",   0.13, 0.17, CYAN),
    ("memory",   "Memory",    0.08, 0.34, CYAN),
    ("ollama",   "Ollama",    0.11, 0.52, AMBER),
    ("system",   "System",    0.16, 0.70, GOLD),
    ("voice",    "Voice",     0.25, 0.85, GOLD),
    ("internet", "Internet",  0.87, 0.16, CYAN),
    ("sitdeck",  "Sitdeck",   0.92, 0.33, CYAN),
    ("cloud",    "Cloud AI",  0.89, 0.51, GOLD),
    ("gmail",    "Gmail",     0.84, 0.68, GOLD),
    ("browser",  "Browser",   0.75, 0.84, CYAN),
]

# log-line markers (lowercased substring) → node key
_NODE_MARKERS = [
    ("[heard]", "hearing"),
    ("[websearch]", "internet"), ("[browser-search]", "internet"),
    ("[live-data", "internet"),
    ("[privatesearch]", "sitdeck"),
    ("[dev]", "cloud"),
    ("[gmail", "gmail"),
    ("(remembered", "memory"), ("[memory]", "memory"),
    ("[browser]", "browser"), ("[new-tab]", "browser"), ("[page-nav]", "browser"),
    ("[website bypass]", "browser"), ("[read-screen]", "browser"),
    ("[open-app", "system"), ("[settings", "system"), ("[folder]", "system"),
    ("[file]", "system"), ("[music]", "system"), ("[tab-nav]", "system"),
    ("[vscode-nav]", "system"), ("[open-file]", "system"),
]


def _greeting_line() -> str:
    h = datetime.datetime.now().hour
    part = "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening"
    name = "Ashish"
    try:
        from memory.memory_manager import load_memory
        entry = load_memory().get("identity", {}).get("name", {})
        name = (entry.get("value") if isinstance(entry, dict) else entry) or name
    except Exception:
        pass
    return f"GOOD {part.upper()},\n{name.upper()}"


# ── the galaxy scene ───────────────────────────────────────────────────────────
class GalaxyScene(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "LISTENING"
        self._t = 0.0
        self._rot = 0.0
        self._rng = random.Random(11)

        # ambient stars scattered across the whole screen (rel coords)
        self._ambient = [(self._rng.random(), self._rng.random(),
                          self._rng.uniform(0.4, 1.3), self._rng.uniform(0.10, 0.45),
                          self._rng.uniform(0.4, 2.2))
                         for _ in range(160)]

        # the galaxy: gaussian cluster, elongated + tilted like a real one.
        # Stars live in "galaxy units" (scaled by min(w,h) at draw time) and are
        # grouped into twinkle buckets so painting stays cheap.
        tilt = math.radians(-28)
        cos_t, sin_t = math.cos(tilt), math.sin(tilt)
        n_buckets = 9
        self._buckets = []
        for b in range(n_buckets):
            stars = []
            for _ in range(125):
                # dense core + long sparse halo
                gx = self._rng.gauss(0, 0.30) * (1.55 if self._rng.random() < 0.5 else 0.9)
                gy = self._rng.gauss(0, 0.30) * 0.72
                x = gx * cos_t - gy * sin_t
                y = gx * sin_t + gy * cos_t
                size = (self._rng.uniform(0.5, 1.1) if self._rng.random() < 0.82
                        else self._rng.uniform(1.2, 2.4))
                stars.append((x, y, size))
            self._buckets.append((
                stars,
                self._rng.uniform(0, math.tau),        # twinkle phase
                self._rng.uniform(0.5, 1.6),           # twinkle speed
                self._rng.uniform(0.35, 0.95),         # base alpha
            ))

        # subsystem node activity + circuit-trace jogs
        self._node_act = {key: 0.0 for key, *_ in NODE_DEFS}
        self._node_jog = {key: (self._rng.uniform(0.04, 0.10),
                                self._rng.uniform(-0.06, 0.06),
                                self._rng.uniform(0.03, 0.07))
                          for key, *_ in NODE_DEFS}
        # each node taps its OWN region of the galaxy — an anchor star in
        # galaxy-local coords (rotates with the cluster), placed on the node's
        # side of the sky so links reach into different arms, not the core
        self._node_anchor = {}
        for (key, _lab, rx, ry, _c) in NODE_DEFS:
            ang = math.atan2(ry - 0.46, rx - 0.5) + self._rng.uniform(-0.5, 0.5)
            rad = self._rng.uniform(0.16, 0.42)
            self._node_anchor[key] = (math.cos(ang) * rad, math.sin(ang) * rad)

        self._wave = [0.0] * 33
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)   # ~30 fps
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def activate(self, key: str):
        if key in self._node_act:
            self._node_act[key] = 1.0

    def _tick(self):
        self._t += 0.033
        self._rot += 0.010 if self.state == "THINKING" else 0.004   # slow galactic spin
        for k in self._node_act:
            self._node_act[k] *= 0.986
        if self.state == "SPEAKING":
            for i in range(len(self._wave)):
                target = abs(math.sin(self._t * 7 + i * 0.9)) * self._rng.uniform(0.35, 1.0)
                self._wave[i] += (target - self._wave[i]) * 0.4
        else:
            for i in range(len(self._wave)):
                self._wave[i] *= 0.85
        self.update()

    # ── painting ──────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), BG)

        cx, cy = w * 0.5, h * 0.46
        col = _STATE_COLOR.get(self.state, CYAN)
        twk_mul = 2.2 if self.state == "THINKING" else 1.0

        # ambient stars everywhere
        p.setPen(Qt.PenStyle.NoPen)
        for (sx, sy, sz, sa, spd) in self._ambient:
            tw = 0.55 + 0.45 * math.sin(self._t * spd * twk_mul + sx * 25)
            c = QColor(STAR_WHITE)
            c.setAlphaF(sa * tw)
            p.setBrush(c)
            p.drawEllipse(QPointF(sx * w, sy * h), sz, sz)

        # ── state glow at the galactic core (very subtle) ─────
        breathe = 1.0 + 0.06 * math.sin(self._t * (2.6 if self.state != "LISTENING" else 1.3))
        glow_r = min(w, h) * 0.30 * breathe
        core_glow = QRadialGradient(cx, cy, glow_r)
        gc = QColor(col)
        gc.setAlpha(46 if self.state == "SPEAKING" else 30)
        core_glow.setColorAt(0.0, gc)
        gc2 = QColor(col); gc2.setAlpha(0)
        core_glow.setColorAt(1.0, gc2)
        p.setBrush(QBrush(core_glow))
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # white heart of the galaxy
        heart = QRadialGradient(cx, cy, min(w, h) * 0.055)
        hc = QColor("#ffffff"); hc.setAlpha(150)
        heart.setColorAt(0.0, hc)
        hc2 = QColor("#ffffff"); hc2.setAlpha(0)
        heart.setColorAt(1.0, hc2)
        p.setBrush(QBrush(heart))
        p.drawEllipse(QPointF(cx, cy), min(w, h) * 0.055, min(w, h) * 0.055)

        # ── the galaxy cluster (rotating slowly) ──────────────
        scale = min(w, h) * 0.52
        p.save()
        p.translate(cx, cy)
        p.rotate(math.degrees(self._rot) * 0.5)
        for (stars, ph, spd, base_a) in self._buckets:
            tw = 0.60 + 0.40 * math.sin(self._t * spd * twk_mul + ph)
            c = QColor(STAR_WHITE)
            c.setAlphaF(min(1.0, base_a * tw))
            p.setBrush(c)
            p.setPen(Qt.PenStyle.NoPen)
            for (gx, gy, sz) in stars:
                p.drawEllipse(QPointF(gx * scale, gy * scale), sz, sz)
        p.restore()

        # ── subsystem nodes: named stars of the galaxy ────────
        for (key, label, rx, ry, col_n) in NODE_DEFS:
            nx, ny = rx * w, ry * h
            act = self._node_act[key]
            left_side = rx < 0.5
            edge_dir = -1 if left_side else 1

            # faint circuit trace to the screen edge
            j1, j2, j3 = self._node_jog[key]
            p1 = QPointF(nx + edge_dir * j1 * w, ny)
            p2 = QPointF(p1.x(), ny + j2 * h)
            p3 = QPointF(p2.x() + edge_dir * j3 * w, p2.y())
            trace = QColor(STAR_WHITE)
            trace.setAlpha(int(22 + act * 110))
            p.setPen(QPen(trace, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(QPointF(nx, ny))
            path.lineTo(p1); path.lineTo(p2); path.lineTo(p3)
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(trace)
            p.drawEllipse(p3, 2.0, 2.0)

            # energy link into this node's own region of the galaxy
            if act > 0.05:
                gax, gay = self._node_anchor[key]
                rot = self._rot * 0.5   # same rotation the cluster is drawn with
                ax = cx + (gax * math.cos(rot) - gay * math.sin(rot)) * scale
                ay = cy + (gax * math.sin(rot) + gay * math.cos(rot)) * scale
                link = QPainterPath(QPointF(nx, ny))
                mx, my = (nx + ax) / 2, (ny + ay) / 2 - 40
                link.quadTo(QPointF(mx, my), QPointF(ax, ay))
                lc = QColor(col_n); lc.setAlphaF(0.5 * act)
                p.setPen(QPen(lc, 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(link)
                tp = (self._t * 0.9 + hash(key) % 10 / 10.0) % 1.0
                pt = link.pointAtPercent(tp)
                pc_ = QColor("#ffffff"); pc_.setAlphaF(0.95 * act)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(pc_)
                p.drawEllipse(pt, 2.2, 2.2)
                # the junction star lights up where the link lands
                jg = QRadialGradient(ax, ay, 10)
                jc = QColor(col_n); jc.setAlphaF(0.55 * act)
                jg.setColorAt(0, jc)
                jc2 = QColor(col_n); jc2.setAlpha(0)
                jg.setColorAt(1, jc2)
                p.setBrush(QBrush(jg))
                p.drawEllipse(QPointF(ax, ay), 10, 10)
                wc = QColor("#ffffff"); wc.setAlphaF(0.9 * act)
                p.setBrush(wc)
                p.drawEllipse(QPointF(ax, ay), 1.8, 1.8)

            # glow bloom when active
            if act > 0.05:
                g = QRadialGradient(nx, ny, 26 + act * 14)
                gc_ = QColor(col_n); gc_.setAlphaF(0.40 * act)
                g.setColorAt(0, gc_)
                gc2_ = QColor(col_n); gc2_.setAlpha(0)
                g.setColorAt(1, gc2_)
                p.setBrush(QBrush(g)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(nx, ny), 26 + act * 14, 26 + act * 14)

            # the star itself: bright dot + 4 diffraction spikes
            tw = 0.7 + 0.3 * math.sin(self._t * 1.9 + nx)
            star_c = QColor(STAR_WHITE) if act < 0.25 else QColor(col_n).lighter(140)
            star_c.setAlphaF(min(1.0, (0.55 + act * 0.45) * tw))
            spike = 5.5 + act * 9
            pen = QPen(star_c, 1.1)
            p.setPen(pen)
            p.drawLine(QPointF(nx - spike, ny), QPointF(nx + spike, ny))
            p.drawLine(QPointF(nx, ny - spike), QPointF(nx, ny + spike))
            d = spike * 0.45
            p.drawLine(QPointF(nx - d, ny - d), QPointF(nx + d, ny + d))
            p.drawLine(QPointF(nx - d, ny + d), QPointF(nx + d, ny - d))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(star_c)
            p.drawEllipse(QPointF(nx, ny), 2.2 + act * 1.8, 2.2 + act * 1.8)

            # label
            lab_c = QColor(TEXT_MAIN) if act > 0.25 else QColor(TEXT_DIM)
            lab_c.setAlpha(int(140 + act * 115))
            p.setPen(QPen(lab_c))
            lf = QFont("Helvetica Neue", 11)
            lf.setWeight(QFont.Weight.DemiBold if act > 0.25 else QFont.Weight.Normal)
            p.setFont(lf)
            if left_side:
                p.drawText(QRectF(nx + 14, ny - 10, 150, 20),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            else:
                p.drawText(QRectF(nx - 164, ny - 10, 150, 20),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        # ── waveform / idle dots ──────────────────────────────
        wy = h - 64
        n = len(self._wave)
        x0 = cx - (n * 9) / 2
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(self._wave):
            bar_h = 3 + v * 30
            c = QColor(GOLD if self.state == "SPEAKING" else TEXT_DIM)
            c.setAlpha(220 if self.state == "SPEAKING" else 80)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x0 + i * 9, wy - bar_h / 2, 4.5, bar_h), 2, 2)

        p.setPen(QPen(QColor(col)))
        f = QFont("Helvetica Neue", 10)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4.0)
        p.setFont(f)
        p.drawText(QRectF(0, wy + 14, w, 20), Qt.AlignmentFlag.AlignHCenter,
                   _STATE_LABEL.get(self.state, "").upper())

        # greeting, top-left
        p.setPen(QPen(TEXT_DIM))
        f2 = QFont("Helvetica Neue", 11)
        f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        p.setFont(f2)
        p.drawText(QRectF(28, 24, 360, 60), Qt.AlignmentFlag.AlignLeft, self._greet)

        # status pill, top-center
        pill_w, pill_h = 210, 26
        pr = QRectF(cx - pill_w / 2, 18, pill_w, pill_h)
        pc = QColor(col); pc.setAlpha(22)
        p.setBrush(pc)
        pen = QPen(); pc2 = QColor(col); pc2.setAlpha(110)
        pen.setColor(pc2); pen.setWidthF(1.0)
        p.setPen(pen)
        p.drawRoundedRect(pr, 13, 13)
        p.setPen(QPen(QColor(col)))
        p.setFont(QFont("Helvetica Neue", 9))
        dot = "●" if int(self._t * 2) % 2 == 0 or self.state != "LISTENING" else "○"
        p.drawText(pr, Qt.AlignmentFlag.AlignCenter,
                   f"{dot}  {_STATE_LABEL.get(self.state, '').upper()}")
        p.end()

    _greet = _greeting_line()


# ── main window + thread-safe bridge ──────────────────────────────────────────
class _Bridge(QObject):
    state_sig    = pyqtSignal(str)
    log_sig      = pyqtSignal(str)
    shutdown_sig = pyqtSignal()


class CurieWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Curie")
        self.resize(1180, 720)
        self.setMinimumSize(920, 600)
        self.on_window_closed = None   # set by JarvisUI; fired on the X button

        self.scene = GalaxyScene()
        self.setCentralWidget(self.scene)

    def closeEvent(self, event):
        # Closing the window should stop Curie's background listen loop too —
        # otherwise it keeps running as an orphaned process with no face.
        if self.on_window_closed:
            try: self.on_window_closed()
            except Exception: pass
        event.accept()

    # slots (main thread)
    def apply_state(self, s: str):
        state = (s or "LISTENING").upper()
        self.scene.state = state
        if state == "THINKING":
            self.scene.activate("ollama")
        elif state == "SPEAKING":
            self.scene.activate("voice")

    def apply_log(self, text: str):
        # Voice-only by design: no message bubbles, no transcript on screen —
        # the conversation lives in the terminal log. Only node activations
        # (which subsystem is working) are reflected visually.
        low = (text or "").strip().lower()
        for marker, key in _NODE_MARKERS:
            if marker in low:
                self.scene.activate(key)


class _RootShim:
    """Old interface compat: main.py calls ui.root.mainloop()."""
    def __init__(self, app):
        self._app = app

    def mainloop(self):
        self._app.exec()


class JarvisUI:
    """Drop-in replacement for the old HUD — same public surface."""

    def __init__(self):
        self._app = QApplication.instance() or QApplication([])
        self._win = CurieWindow()
        self._bridge = _Bridge()
        self._bridge.state_sig.connect(self._win.apply_state)
        self._bridge.log_sig.connect(self._win.apply_log)
        self._bridge.shutdown_sig.connect(self._app.quit)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def on_window_closed(self):
        return self._win.on_window_closed

    @on_window_closed.setter
    def on_window_closed(self, cb):
        self._win.on_window_closed = cb

    def set_state(self, state: str):
        self._bridge.state_sig.emit(state or "")

    def write_log(self, text: str):
        self._bridge.log_sig.emit(text or "")

    def shutdown(self):
        """Close the whole app — called when Curie shuts down by voice."""
        self._bridge.shutdown_sig.emit()

    def wait_for_api_key(self):
        return  # local build — nothing to wait for


if __name__ == "__main__":
    ui = JarvisUI()
    ui.write_log("Curie: Face preview — the galaxy build.")
    ui.write_log("You: Looks clean!")
    import itertools, random as _rnd, threading as _th, time as _tm
    _demo_logs = ["[heard] demo", "[WebSearch] demo", "[PrivateSearch] demo",
                  "[dev] demo", "[gmail bypass] demo", "(remembered demo)",
                  "[browser] demo", "[open-app bypass] demo"]
    def _demo():
        for s in itertools.cycle(["LISTENING", "THINKING", "SPEAKING"]):
            ui.set_state(s)
            ui.write_log(_rnd.choice(_demo_logs))
            _tm.sleep(3)
    _th.Thread(target=_demo, daemon=True).start()
    ui.root.mainloop()
