from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush, QPainter, QPen, QFont
from PyQt6.QtWidgets import (
    QLabel, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QScrollArea, QHeaderView
)

try:
    from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
    HAS_CHARTS = True
except ImportError:
    HAS_CHARTS = False

from cs.config import CFG
from cs.stylesheet import (
    DARK2, CARD, BORDER, ACCENT, GREEN, RED, YELLOW, WHITE, DIM,
    STRONG_BUY_BG, STRONG_SELL_BG, BUY_BG, SELL_BG,
    MONO_CSS, mono_font,
)


class TooltipHeaderView(QHeaderView):
    """
    QHeaderView subclass that shows per-column tooltips on hover.
    Tooltip appears after the standard Qt delay (~700ms) and disappears on mouse move.
    """
    def __init__(self, orientation, tooltips: dict, parent=None):
        """
        tooltips: dict mapping column index -> tooltip string
        """
        super().__init__(orientation, parent)
        self._tooltips = tooltips

    def event(self, e):
        from PyQt6.QtWidgets import QToolTip
        if e.type() == e.Type.ToolTip:
            pos   = e.pos()
            index = self.logicalIndexAt(pos)
            tip   = self._tooltips.get(index, "")
            if tip: QToolTip.showText(e.globalPos(), tip, self)
            else:   QToolTip.hideText()
            return True
        return super().event(e)


class SignalBadge(QLabel):
    COLORS = {
        "PRE-BREAKOUT":("#ff9900", "#2a1800",    "#ffaa33"),
        "STRONG BUY":  (GREEN,  STRONG_BUY_BG,  "#00ff88"),
        "BUY":         (GREEN,  BUY_BG,          "#00cc66"),
        "STRONG SELL": (RED,    STRONG_SELL_BG,  "#ff3366"),
        "SELL":        (RED,    SELL_BG,          "#cc2244"),
        "NEUTRAL":     (DIM,    CARD,             DIM),
    }

    def __init__(self, signal_text):
        super().__init__(signal_text)
        fg, bg, border = self.COLORS.get(signal_text, (WHITE, CARD, BORDER))
        bold = "800" if "STRONG" in signal_text else "600"
        self.setStyleSheet(f"""
            QLabel {{
                color: {fg};
                background: {bg};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 3px 8px;
                font-weight: {bold};
                font-size: 11px;
                font-family: {MONO_CSS};
                letter-spacing: 0.5px;
            }}
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

# ─────────────────────────────────────────────────────────────
#  SPARKLINE WIDGET
# ─────────────────────────────────────────────────────────────
class Sparkline(QWidget):
    def __init__(self, values, color=GREEN, parent=None):
        super().__init__(parent)
        self.values = values
        self.color  = QColor(color)
        self.setFixedSize(80, 28)

    def paintEvent(self, event):
        if not self.values or len(self.values) < 2:
            return
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        lo   = min(self.values)
        hi   = max(self.values)
        rng  = hi - lo or 1e-9
        pts  = [(i / (len(self.values) - 1) * w,
                 h - (v - lo) / rng * (h - 4) - 2)
                for i, v in enumerate(self.values)]
        pen  = QPen(self.color, 1.5)
        p.setPen(pen)
        for i in range(len(pts) - 1):
            p.drawLine(int(pts[i][0]), int(pts[i][1]),
                       int(pts[i+1][0]), int(pts[i+1][1]))
        # dot at end
        p.setBrush(QBrush(self.color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(pts[-1][0]) - 3, int(pts[-1][1]) - 3, 6, 6)

# ─────────────────────────────────────────────────────────────
#  MINI BAR (RSI / StochRSI)
# ─────────────────────────────────────────────────────────────
class MiniBar(QWidget):
    def __init__(self, value, lo_good=True, parent=None):
        super().__init__(parent)
        self.value   = max(0, min(100, value))
        self.lo_good = lo_good
        self.setFixedSize(70, 14)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # track
        p.setBrush(QBrush(QColor(BORDER)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 3, w, h - 6, 3, 3)

        v = self.value / 100
        if self.lo_good:
            c = QColor(GREEN) if self.value < 40 else QColor(RED) if self.value > 60 else QColor(YELLOW)
        else:
            c = QColor(RED) if self.value < 40 else QColor(GREEN) if self.value > 60 else QColor(YELLOW)
        p.setBrush(QBrush(c))
        p.drawRoundedRect(0, 3, int(w * v), h - 6, 3, 3)

# ─────────────────────────────────────────────────────────────
#  STAT CARD
# ─────────────────────────────────────────────────────────────
class StatCard(QFrame):
    def __init__(self, label, value, color=WHITE, parent=None):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        lay.setContentsMargins(14, 10, 14, 10)

        lbl = QLabel(label.upper())
        lbl.setStyleSheet(f"color:{DIM}; font-size:10px; font-weight:700; letter-spacing:1px;")

        self.val_lbl = QLabel(value)
        self.val_lbl.setStyleSheet(f"color:{color}; font-size:18px; font-weight:800; font-family:{MONO_CSS};")

        lay.addWidget(lbl)
        lay.addWidget(self.val_lbl)

    def set_value(self, value, color=None):
        self.val_lbl.setText(value)
        if color:
            self.val_lbl.setStyleSheet(
                f"color:{color}; font-size:18px; font-weight:800; font-family:{MONO_CSS};")

# ─────────────────────────────────────────────────────────────
#  PRICE CHART — pure QPainter candlestick chart, no QtCharts
# ─────────────────────────────────────────────────────────────
class PriceChart(QWidget):
    def __init__(self, candles, parent=None):
        super().__init__(parent)
        self.candles = candles
        self.setFixedHeight(180)
        self.setStyleSheet(f"background:{DARK2}; border-radius:6px;")
        self.setMinimumWidth(300)

    def paintEvent(self, event):
        if not self.candles:
            return
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h  = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 55, 12, 12, 24


        p.fillRect(0, 0, w, h, QColor(DARK2))

        candles = self.candles
        hi  = max(c["high"]  for c in candles)
        lo  = min(c["low"]   for c in candles)
        rng = hi - lo or 1e-9

        def y(price):
            return pad_t + (hi - price) / rng * (h - pad_t - pad_b)

        n    = len(candles)
        cw   = max(2, (w - pad_l - pad_r) / n - 1)
        gap  = (w - pad_l - pad_r) / n

        # Grid lines + Y labels
        p.setPen(QPen(QColor(BORDER), 1, Qt.PenStyle.DotLine))
        p.setFont(mono_font(8))
        p.setPen(QColor(DIM))
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            price = hi - frac * rng
            yy    = int(y(price))
            p.setPen(QPen(QColor(BORDER), 1, Qt.PenStyle.DotLine))
            p.drawLine(pad_l, yy, w - pad_r, yy)
            p.setPen(QColor(DIM))
            p.drawText(2, yy + 4, f"{price:.5f}")

        # Candles
        for i, c in enumerate(candles):
            x_center = pad_l + i * gap + gap / 2
            x_left   = int(x_center - cw / 2)
            is_green = c["close"] >= c["open"]
            col      = QColor(GREEN) if is_green else QColor(RED)

            # Wick
            p.setPen(QPen(col, 1))
            p.drawLine(int(x_center), int(y(c["high"])),
                       int(x_center), int(y(c["low"])))

            # Body
            body_top = int(y(max(c["open"], c["close"])))
            body_bot = int(y(min(c["open"], c["close"])))
            body_h   = max(1, body_bot - body_top)
            p.fillRect(x_left, body_top, max(1, int(cw)), body_h, col)

        # Current price line
        last_price = candles[-1]["close"]
        yy = int(y(last_price))
        p.setPen(QPen(QColor(ACCENT), 1, Qt.PenStyle.DashLine))
        p.drawLine(pad_l, yy, w - pad_r, yy)
        p.setPen(QColor(ACCENT))
        p.drawText(w - pad_r - 2, yy - 3, f"${last_price:.5f}")

        p.end()


# ─────────────────────────────────────────────────────────────
#  DETAIL PANEL
# ─────────────────────────────────────────────────────────────
class DetailPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet(f"background:{DARK2}; border:none;")
        self._build_ui()

    def _build_ui(self):
        w = QWidget()
        self.setWidget(w)
        self.lay = QVBoxLayout(w)
        self.lay.setSpacing(10)
        self.lay.setContentsMargins(14, 14, 14, 14)
        self.lay.addWidget(QLabel("← Select a coin from the scanner"))
        self.lay.addStretch()

    def load(self, r):
        # Clear
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not r:
            self.lay.addWidget(QLabel("No data"))
            return

        sym    = r["symbol"].replace("USDT", "/USDT")
        price  = r["price"]
        sig    = r["signal"]
        chg    = r["change_24h"]
        rsi    = r["rsi"]
        srsi   = r["stoch_rsi"]
        mh     = r["macd_hist"]
        pot    = r.get("potential", 0)
        exp    = r.get("expected_move", 0)
        chg_c  = GREEN if chg >= 0 else RED
        sig_c  = GREEN if "BUY" in sig else RED if "SELL" in sig else DIM

        hdr = QFrame(); hdr.setObjectName("accentCard")
        hlay = QHBoxLayout(hdr)

        sym_lbl = QLabel(sym)
        sym_lbl.setStyleSheet(f"color:{ACCENT}; font-size:22px; font-weight:800; font-family:{MONO_CSS};")

        price_lbl = QLabel(f"${price:.6f}")
        price_lbl.setStyleSheet(f"color:{WHITE}; font-size:18px; font-weight:700; font-family:{MONO_CSS};")

        chg_lbl = QLabel(f"{chg:+.2f}%")
        chg_lbl.setStyleSheet(f"color:{chg_c}; font-size:16px; font-weight:700;")

        badge = SignalBadge(sig)

        hlay.addWidget(sym_lbl)
        hlay.addWidget(price_lbl)
        hlay.addWidget(chg_lbl)
        hlay.addStretch()
        hlay.addWidget(badge)
        self.lay.addWidget(hdr)

        stats_w = QWidget()
        slay    = QHBoxLayout(stats_w)
        slay.setSpacing(8)
        slay.setContentsMargins(0, 0, 0, 0)
        pot_c  = GREEN if pot >= 70 else YELLOW if pot >= 40 else RED
        exp_c  = GREEN if exp >= 8  else GREEN  if exp >= 5  else YELLOW
        vr     = r.get("vol_ratio", 0)
        rising = r.get("macd_rising", False)
        # Volume gate thresholds matching scanner rules
        if "STRONG" in sig: vr_needed = 1.8
        elif sig in ("BUY","SELL"): vr_needed = 1.3
        else: vr_needed = 1.0
        vr_c   = GREEN if vr >= vr_needed * 1.3 else YELLOW if vr >= vr_needed else RED
        macd_c = GREEN if (mh > 0 and rising) or (mh < 0 and not rising) else YELLOW
        # 1H trend
        t1h = r.get("trend_1h", "flat")
        t1h_labels = {"up": "↑ Uptrend", "down": "↓ Downtrend", "flat": "→ Sideways"}
        t1h_str    = t1h_labels.get(t1h, "→ Sideways")
        if t1h == "up":
            t1h_col = GREEN if "BUY" in sig else (RED if "SELL" in sig else ACCENT)
        elif t1h == "down":
            t1h_col = RED if "BUY" in sig else (GREEN if "SELL" in sig else RED)
        else:
            t1h_col = DIM
        # Signal age
        age_dt  = r.get("signal_age")
        if age_dt and sig != "NEUTRAL":
            age_s   = int((datetime.now() - age_dt).total_seconds())
            age_str = f"{age_s // 60}m {age_s % 60}s" if age_s >= 60 else f"{age_s}s"
        else:
            age_str = "—"
        # Conf count
        sc      = r.get("signal_conf", 0) if sig != "NEUTRAL" else 0
        sc_col  = ACCENT if sc >= 5 else GREEN if sc >= 3 else YELLOW if sc >= 1 else DIM
        sc_str  = f"{sc} scan{'s' if sc != 1 else ''}"

        stats = [
            ("RSI",         f"{rsi:.1f}",          GREEN if rsi < 40 else RED if rsi > 60 else YELLOW),
            ("Stoch RSI",   f"{srsi:.1f}",          GREEN if srsi < 30 else RED if srsi > 70 else YELLOW),
            ("MACD",        f"{'Fresh ✓' if macd_c==GREEN else 'Stale ⚠'}  {mh:+.5f}", macd_c),
            ("Potential",   f"{pot}%",              pot_c),
            ("Exp Move",    f"{exp:.1f}%",          exp_c),
            ("Vol Ratio",   f"{vr:.2f}x  ({'✓' if vr >= vr_needed else '✗'})", vr_c),
            ("1H Trend",    t1h_str,                t1h_col),
            ("Sig Age",     age_str,                YELLOW if age_str != "—" else DIM),
            ("Confirmed",   sc_str,                 sc_col),
        ]
        for lbl, val, col in stats:
            slay.addWidget(StatCard(lbl, val, col))
        self.lay.addWidget(stats_w)

        ind_grp = QGroupBox("INDICATORS")
        ind_lay = QGridLayout(ind_grp)
        ind_lay.setSpacing(6)

        def ind_row(row, label, widget, val_text="", col=WHITE):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
            val = QLabel(val_text)
            val.setStyleSheet(f"color:{col}; font-family:{MONO_CSS}; font-size:12px; font-weight:700;")
            ind_lay.addWidget(lbl, row, 0)
            ind_lay.addWidget(widget, row, 1)
            ind_lay.addWidget(val, row, 2)

        ind_row(0, "RSI (14)",    MiniBar(rsi),  f"{rsi:.1f}", GREEN if rsi < 40 else RED if rsi > 60 else YELLOW)
        ind_row(1, "Stoch RSI",   MiniBar(srsi), f"{srsi:.1f}", GREEN if srsi < 30 else RED if srsi > 70 else YELLOW)

        # BB position
        bb_pos_val = 50.0
        if r.get("bb_upper") and r.get("bb_lower") and r["bb_upper"] != r["bb_lower"]:
            bb_pos_val = (price - r["bb_lower"]) / (r["bb_upper"] - r["bb_lower"]) * 100
        ind_row(2, "BB Position", MiniBar(bb_pos_val, lo_good=False),
                f"{bb_pos_val:.0f}%", GREEN if bb_pos_val < 30 else RED if bb_pos_val > 70 else YELLOW)

        macd_lbl = QLabel(f"MACD")
        macd_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        rising   = r.get("macd_rising", False)
        macd_dir = ("▲ Rising" if rising else "▼ Fading") if mh > 0 else \
                   ("▼ Falling" if not rising else "▲ Recovering") if mh < 0 else "— Flat"
        conf_txt = "  ✓ Fresh" if (mh > 0 and rising) or (mh < 0 and not rising) else "  ⚠ Stale"
        conf_col = GREEN if "Fresh" in conf_txt else YELLOW
        macd_val = QLabel(f"{mh:+.8f}  {macd_dir}{conf_txt}")
        macd_val.setStyleSheet(f"color:{GREEN if mh > 0 else RED}; font-family:{MONO_CSS}; font-size:12px; font-weight:700;")
        ind_lay.addWidget(macd_lbl, 3, 0)
        ind_lay.addWidget(macd_val, 3, 1, 1, 2)
        self.lay.addWidget(ind_grp)

        if r.get("bb_upper"):
            bb_grp = QGroupBox("BOLLINGER BANDS")
            bb_lay = QHBoxLayout(bb_grp)
            for lbl, val, col in [
                ("Lower", r["bb_lower"], GREEN),
                ("Mid",   r["bb_mid"],   YELLOW),
                ("Upper", r["bb_upper"], RED),
            ]:
                c = StatCard(lbl, f"${val:.6f}", col)
                bb_lay.addWidget(c)
            self.lay.addWidget(bb_grp)

        sr_grp = QGroupBox("SUPPORT / RESISTANCE")
        sr_lay = QHBoxLayout(sr_grp)
        sr_lay.addWidget(StatCard("Support",    f"${r['support']:.6f}", GREEN))
        sr_lay.addWidget(StatCard("Resistance", f"${r['resist']:.6f}",  RED))
        sr_lay.addWidget(StatCard("Vol 24h",    f"${r['volume_24h']/1e6:.1f}M", ACCENT))
        self.lay.addWidget(sr_grp)

        sl_pct  = CFG["sl_pct"]
        tp_pct  = CFG["tp_pct"]
        tp2_pct = CFG["tp2_pct"]
        rr      = round(tp_pct / sl_pct, 2)

        for setup_name, is_long in [("LONG SETUP", True), ("SHORT SETUP", False)]:
            active = ("BUY" in sig and is_long) or ("SELL" in sig and not is_long)
            border_col = GREEN if is_long else RED
            grp = QGroupBox(f"{'▲' if is_long else '▼'} {setup_name}  (SL {sl_pct}%  /  TP {tp_pct}%)")
            grp.setStyleSheet(f"""
                QGroupBox {{
                    color: {border_col if active else DIM};
                    border: 1px solid {border_col if active else BORDER};
                    border-left: 3px solid {border_col if active else BORDER};
                    border-radius: 6px; margin-top:16px; font-weight:700; font-size:11px;
                }}
                QGroupBox::title {{
                    subcontrol-origin: margin; subcontrol-position: top left;
                    padding: 0 8px; left: 10px;
                }}
            """)
            glay = QHBoxLayout(grp)

            if is_long:
                sl  = round(price * (1 - sl_pct  / 100), 6)
                tp1 = round(price * (1 + tp_pct  / 100), 6)
                tp2 = round(price * (1 + tp2_pct / 100), 6)
            else:
                sl  = round(price * (1 + sl_pct  / 100), 6)
                tp1 = round(price * (1 - tp_pct  / 100), 6)
                tp2 = round(price * (1 - tp2_pct / 100), 6)

            for lbl, val, col in [
                ("Entry",  f"${price:.6f}", WHITE),
                ("Stop Loss", f"${sl:.6f}",  RED),
                ("TP1",    f"${tp1:.6f}", GREEN),
                ("TP2",    f"${tp2:.6f}", GREEN),
                ("R/R",    f"{rr:.2f}x",  YELLOW),
            ]:
                glay.addWidget(StatCard(lbl, val, col))

            self.lay.addWidget(grp)

        candles = r.get("candles", [])
        if candles:
            chart_grp = QGroupBox("PRICE  (last 50 candles)")
            chart_lay = QVBoxLayout(chart_grp)
            chart_lay.addWidget(PriceChart(candles))
            self.lay.addWidget(chart_grp)

        pv_grp = QGroupBox("PATTERN / VOLUME")
        pv_lay = QHBoxLayout(pv_grp)
        pv_lay.addWidget(StatCard("Pattern", r["pattern"], ACCENT))
        pv_lay.addWidget(StatCard("Avg Vol", f"{r['avg_vol']:,.0f}", DIM))
        pv_lay.addWidget(StatCard("Last Vol", f"{r['last_vol']:,.0f}",
                                  GREEN if r.get("vol_ratio", 0) > 1.5 else YELLOW))
        self.lay.addWidget(pv_grp)
        self.lay.addStretch()

# ─────────────────────────────────────────────────────────
#  EQUITY CURVE WIDGET  — pure QPainter, no QtCharts
# ─────────────────────────────────────────────────────────
class _EquityCanvas(QWidget):
    """Draws a cumulative P&L line chart from a list of (label, cumulative_pnl) tuples."""
    def __init__(self):
        super().__init__()
        self._points = []   # list of float cumulative pnl values
        self._labels = []   # list of str trade labels
        self.setMinimumHeight(130)

    def set_data(self, points, labels):
        self._points = points
        self._labels = labels
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 48, 16, 8, 24

        # Background
        p.fillRect(0, 0, W, H, QColor(DARK2))

        pts = self._points
        if not pts:
            p.setPen(QColor(DIM))
            p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter,
                       "No closed trades yet")
            return

        # Single trade — render as a single labelled dot at centre
        if len(pts) == 1:
            col = QColor(GREEN) if pts[0] >= 0 else QColor(RED)
            cx, cy = W // 2, H // 2
            p.setBrush(QBrush(col)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(cx - 5, cy - 5, 10, 10)
            sign = "+" if pts[0] >= 0 else ""
            p.setPen(col)
            p.setFont(QFont("monospace", 9))
            lbl = f"{sign}{pts[0]:.4f} USDT  ({self._labels[0] if self._labels else ''})"
            p.drawText(0, cy + 12, W, 20, Qt.AlignmentFlag.AlignCenter, lbl)
            sub = "1 closed trade"
            p.setPen(QColor(DIM))
            p.setFont(QFont("monospace", 8))
            p.drawText(0, cy - 28, W, 20, Qt.AlignmentFlag.AlignCenter, sub)
            return

        mn, mx = min(pts), max(pts)
        span = mx - mn if mx != mn else max(abs(mx) * 0.1, 0.0001)
        gW = W - pad_l - pad_r
        gH = H - pad_t - pad_b

        def px(i):  return pad_l + int(i / (len(pts) - 1) * gW)
        def py(v):  return pad_t + int((1 - (v - mn) / span) * gH)

        # Zero line
        if mn < 0 < mx:
            zy = py(0)
            p.setPen(QPen(QColor(BORDER), 1, Qt.PenStyle.DashLine))
            p.drawLine(pad_l, zy, W - pad_r, zy)

        # Fill under curve
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        poly_pts = [QPoint(px(0), pad_t + gH)]
        for i, v in enumerate(pts):
            poly_pts.append(QPoint(px(i), py(v)))
        poly_pts.append(QPoint(px(len(pts)-1), pad_t + gH))
        final_positive = pts[-1] >= 0
        fill_col = QColor(0, 180, 80, 35) if final_positive else QColor(220, 50, 50, 35)
        from PyQt6.QtGui import QPolygon as _QP
        from PyQt6.QtCore import QPoint as _QPoint
        poly = _QP([_QPoint(pt.x(), pt.y()) for pt in poly_pts])
        p.setBrush(QBrush(fill_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(poly)

        # Line
        line_col = QColor(GREEN) if pts[-1] >= 0 else QColor(RED)
        p.setPen(QPen(line_col, 2))
        for i in range(1, len(pts)):
            p.drawLine(px(i-1), py(pts[i-1]), px(i), py(pts[i]))

        # Dots
        p.setBrush(QBrush(line_col))
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(pts):
            p.drawEllipse(px(i) - 3, py(v) - 3, 6, 6)

        # Y axis labels
        p.setPen(QColor(DIM))
        p.setFont(QFont("monospace", 8))
        for val in [mn, (mn+mx)/2, mx]:
            y = py(val)
            sign = "+" if val >= 0 else ""
            p.drawText(0, y - 8, pad_l - 4, 16,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{sign}{val:.2f}")

        # X axis trade labels (first, last, maybe middle)
        p.setPen(QColor(DIM))
        p.setFont(QFont("monospace", 7))
        idxs = [0, len(pts)-1]
        if len(pts) > 4:
            idxs.insert(1, len(pts)//2)
        for i in idxs:
            if i < len(self._labels):
                lbl = self._labels[i]
                x = px(i)
                p.drawText(x - 20, H - pad_b, 40, pad_b,
                           Qt.AlignmentFlag.AlignCenter, lbl)
