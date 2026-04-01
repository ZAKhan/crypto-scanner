import sys
import os
import json
import time
import subprocess
import threading
import requests
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton,
    QFrame, QHeaderView, QAbstractItemView, QProgressBar, QTabWidget,
    QScrollArea, QGridLayout, QSizePolicy, QSpacerItem, QGroupBox,
    QLineEdit, QTextEdit, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox,
    QStatusBar, QToolBar, QMessageBox, QDialog, QMenu, QFileDialog
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QPropertyAnimation,
    QEasingCurve, pyqtProperty, QObject, QSettings, QByteArray
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QBrush, QLinearGradient,
    QPainter, QPen, QIcon, QAction, QFontDatabase,
    QShortcut, QKeySequence
)

from cs.config import APP_VERSION, APP_DATA_DIR, APP_LOGS_DIR, CFG
from cs.api import TRADING_CFG
from cs.stylesheet import (
    DARK, DARK2, PANEL, CARD, BORDER, ACCENT, GREEN, RED, YELLOW, WHITE, DIM,
    STRONG_BUY_BG, STRONG_SELL_BG, BUY_BG, SELL_BG,
    MONO_CSS, MONO_FAMILIES, MONO, mono_font, SANS, FONT_SIZE, BROWSER_PATH,
    make_stylesheet
)
import cs.stylesheet as _stylesheet_mod
from cs.safety import (
    SAFETY_CFG, _daily_loss_tracker,
    check_trade_safety, record_trade_loss
)
from cs.logger import ALERT_CFG, _get_signal_log_path, SIGNAL_LOG_PATH, log_scan_results
from cs.alerts import AlertEngine, _outcome_tracker
from cs.surge import VolumeSurgeDetector, SURGE_CFG
from cs.scanner import Scanner, ScanWorker
from cs.trader import _trader
from cs.updater import UpdateChecker, GITHUB_RELEASES_PAGE
from cs.sounds import _SOUNDS
from cs.widgets import (
    TooltipHeaderView, SignalBadge, Sparkline, MiniBar,
    StatCard, PriceChart, DetailPanel, _EquityCanvas
)
from cs.websocket_feed import BinanceWebSocketPrices

try:
    from cs.websocket_feed import _WS_AVAILABLE
except ImportError:
    _WS_AVAILABLE = False


# ─────────────────────────────────────────────────────────
#  URL OPENER
# ─────────────────────────────────────────────────────────
def open_url(url: str) -> None:
    import shutil, os
    env = os.environ.copy()

    global BROWSER_PATH
    if BROWSER_PATH and BROWSER_PATH.strip():
        try:
            subprocess.Popen(
                [BROWSER_PATH.strip(), url], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

    xdg = shutil.which("xdg-open")
    if xdg:
        try:
            proc = subprocess.Popen(
                [xdg, url], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            proc.wait(timeout=3)
            if proc.returncode == 0:
                return
        except Exception:
            pass

    try:
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        if QDesktopServices.openUrl(QUrl(url)):
            return
    except Exception:
        pass

    for browser in ("firefox", "chromium", "chromium-browser",
                    "google-chrome", "brave-browser"):
        b = shutil.which(browser)
        if b:
            try:
                subprocess.Popen(
                    [b, url], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                continue

    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


class CryptoScannerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Crypto Scalper Scanner {APP_VERSION} — Binance")

        import os as _os, sys as _sys
        _icon_candidates = [
            _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app_icon.png"),
            _os.path.join(_os.getcwd(), "app_icon.png"),
            _os.path.join(_os.path.dirname(_os.path.abspath(_sys.argv[0])), "app_icon.png"),
        ]
        for _icon_path in _icon_candidates:
            if _os.path.exists(_icon_path):
                _icon = QIcon(_icon_path)
                QApplication.instance().setWindowIcon(_icon)
                self.setWindowIcon(_icon)
                break
        self.setMinimumSize(1280, 760)
        self._scanner  = Scanner()
        self._worker   = None
        self._results  = []
        self._live_prices = {}
        self._settings = QSettings("CryptoScalper", "CryptoScannerGUI")
        self._trades   = []
        self._programmatic_resize = False
        self._sort_col  = None
        self._sort_asc  = True
        self._alert_log = []
        self._flash_overlay = None
        self._flash_anim    = None
        self._title_flash_timer = QTimer(self)
        self._title_flash_timer.timeout.connect(self._flash_title_tick)
        self._title_flash_state = False
        self._title_flash_count = 0
        self._title_flash_msg   = ""
        self._status_alert_active = False
        self._alert_engine = AlertEngine()
        self._alert_engine.new_alert.connect(self._on_new_alert)
        self._alert_engine.scan_done.connect(self._on_alert_scan_done)
        self._alert_engine.scan_started.connect(self._on_alert_scan_started)

        self._surge_detector = VolumeSurgeDetector()
        self._surge_detector.surge_alert.connect(self._on_surge_alert)

        if _WS_AVAILABLE:
            self._ws_feed = BinanceWebSocketPrices()
            self._ws_feed.price_update.connect(self._on_ws_price, Qt.ConnectionType.QueuedConnection)
            self._ws_feed.connected.connect(self._on_ws_connected, Qt.ConnectionType.QueuedConnection)
            self._ws_feed.disconnected.connect(self._on_ws_disconnected, Qt.ConnectionType.QueuedConnection)
        else:
            self._ws_feed = None
        self._build_ui()
        self._setup_timer()
        self._restore_settings()
        self._trades_refresh_timer.start()
        self._alert_engine.start()
        self._surge_detector.start()
        _outcome_tracker.start()
        if self._ws_feed:
            open_syms = {t["symbol"] for t in self._trades if t["status"] == "OPEN"}
            if open_syms:
                self._ws_feed.subscribe(open_syms)
        QTimer.singleShot(1000, self._refresh_balance_display)
        QTimer.singleShot(5000, self._start_update_check)
        self.statusBar().showMessage("Starting scan…")
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("⏳")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        topbar = QFrame()
        topbar.setStyleSheet(f"background:{PANEL}; border-bottom:1px solid {BORDER};")
        topbar.setFixedHeight(64)
        tlay = QHBoxLayout(topbar)
        tlay.setContentsMargins(20, 0, 20, 0)
        tlay.setSpacing(12)
        tlay.setSizeConstraint(QHBoxLayout.SizeConstraint.SetNoConstraint)

        title = QLabel("◈ CRYPTO SCALPER")
        title.setObjectName("titleLabel")

        ver = QLabel(APP_VERSION)
        ver.setObjectName("versionLabel")
        ver.setToolTip("Application version")

        def _fmt_vol(v):
            if v >= 1_000_000: return f"${v/1_000_000:.0f}M"
            return f"${v:,.0f}"
        self._subtitle_lbl = QLabel(
            f"Binance Spot  ·  Price < ${CFG['max_price']:.0f}  ·  "
            f"Vol > {_fmt_vol(CFG['min_volume_usdt'])}  ·  {CFG['interval']}")
        self._subtitle_lbl.setObjectName("subtitleLabel")
        self._subtitle_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        tlay.addWidget(title, 0)
        tlay.addWidget(ver, 0)
        tlay.addSpacing(14)
        tlay.addWidget(self._subtitle_lbl, 0)
        tlay.addStretch(1)

        self._balance_lbl = QLabel("💰 —")
        self._balance_lbl.setFixedWidth(185)
        self._balance_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self._balance_lbl.setStyleSheet(
            f"color:{ACCENT}; font-family:{MONO_CSS}; font-size:11px; font-weight:700;")
        self._balance_lbl.setToolTip("USDT balance — click to refresh")
        self._balance_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._balance_lbl.mousePressEvent = lambda e: self._refresh_balance_display()
        tlay.addWidget(self._balance_lbl, 0)

        self.progress = QProgressBar()
        self.progress.setVisible(False)

        reset_col_btn = QPushButton("⇔ Cols")
        reset_col_btn.setFixedHeight(30)
        reset_col_btn.setMinimumWidth(75)
        reset_col_btn.setToolTip("Reset column widths to auto-proportional")
        reset_col_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-size:11px; padding:0 8px;")
        reset_col_btn.clicked.connect(self._reset_column_widths)
        tlay.addWidget(reset_col_btn, 0)

        self.scan_btn = QPushButton("⚡")
        self.scan_btn.setObjectName("scanBtn")
        self.scan_btn.setFixedHeight(30)
        self.scan_btn.setMinimumWidth(75)
        self.scan_btn.setToolTip("Scan now")
        self.scan_btn.clicked.connect(self._start_scan)
        tlay.addWidget(self.scan_btn, 0)

        self.status_lbl = QLabel()
        self.status_lbl.setVisible(False)

        self.lbl_filter = QLabel()
        self._update_filter_label()

        root.addWidget(topbar)

        self._live_banner = QLabel(
            "🔴  LIVE TRADING MODE — real money at risk  🔴")
        self._live_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._live_banner.setStyleSheet(
            f"background:#5a0000; color:#ff6666; font-weight:900; "
            f"font-size:12px; padding:4px; border-bottom:1px solid {RED};")
        self._live_banner.setVisible(not TRADING_CFG["testnet"])
        root.addWidget(self._live_banner)

        self._update_banner = QFrame()
        self._update_banner.setFixedHeight(34)
        self._update_banner.setStyleSheet(
            "background:#1a2e1a; border-bottom:1px solid #2d5a2d;")
        self._update_banner.setVisible(False)
        _ub_lay = QHBoxLayout(self._update_banner)
        _ub_lay.setContentsMargins(16, 0, 10, 0)
        _ub_lay.setSpacing(10)
        self._update_banner_lbl = QLabel()
        self._update_banner_lbl.setStyleSheet(
            "color:#7ddb7d; font-size:12px; background:transparent; border:none;")
        _ub_lay.addWidget(self._update_banner_lbl, 1)
        _ub_dl_btn = QPushButton("⬇ Download")
        _ub_dl_btn.setFixedHeight(22)
        _ub_dl_btn.setStyleSheet(
            "background:#2d5a2d; color:#a0e8a0; border:1px solid #3d7a3d; "
            "border-radius:3px; font-size:11px; font-weight:700; padding:0 10px;")
        _ub_dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _ub_dl_btn.clicked.connect(lambda: open_url(self._update_release_url))
        _ub_lay.addWidget(_ub_dl_btn)
        _ub_close_btn = QPushButton("✕")
        _ub_close_btn.setFixedSize(22, 22)
        _ub_close_btn.setToolTip("Dismiss")
        _ub_close_btn.setStyleSheet(
            "background:transparent; color:#3d7a3d; border:none; "
            "font-size:13px; font-weight:700;")
        _ub_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _ub_close_btn.clicked.connect(lambda: self._update_banner.setVisible(False))
        _ub_lay.addWidget(_ub_close_btn)
        self._update_release_url = GITHUB_RELEASES_PAGE
        root.addWidget(self._update_banner)

        tabs = QTabWidget()

        scanner_tab = QWidget()
        slay = QVBoxLayout(scanner_tab)
        slay.setContentsMargins(8, 8, 8, 8)
        slay.setSpacing(6)
        self.table = self._build_table()
        slay.addWidget(self.table)
        tabs.addTab(scanner_tab, "📊  Scanner")

        self.picks_tab = QWidget()
        picks_outer = QVBoxLayout(self.picks_tab)
        picks_outer.setContentsMargins(0, 0, 0, 0)
        picks_scroll = QScrollArea()
        picks_scroll.setWidgetResizable(True)
        picks_scroll.setFrameShape(QFrame.Shape.NoFrame)
        picks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        picks_inner = QWidget()
        self.picks_lay = QVBoxLayout(picks_inner)
        self.picks_lay.setContentsMargins(12, 12, 12, 12)
        self.picks_lay.setSpacing(8)
        self.picks_lay.addWidget(QLabel("Run a scan to see top picks."))
        picks_scroll.setWidget(picks_inner)
        picks_outer.addWidget(picks_scroll)
        tabs.addTab(self.picks_tab, "🎯  Top Picks")

        tabs.addTab(self._build_config_tab(), "⚙️  Config")

        self._alerts_tab_widget = self._build_alerts_tab()
        tabs.addTab(self._alerts_tab_widget, "🔔  Alerts")

        self._trades_tab_widget = self._build_trades_tab()
        tabs.addTab(self._trades_tab_widget, "💰  Trades")

        self._tabs_widget = tabs
        tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(tabs)

        self.statusBar().showMessage("Ready")
        self._scan_dot = QLabel("⬤")
        self._scan_dot.setStyleSheet("color: #00cc66; font-size:11px; padding:0 4px;")
        self._scan_dot.setToolTip("Scanner idle")
        self.statusBar().addPermanentWidget(self._scan_dot)

        if _WS_AVAILABLE:
            self._ws_status_lbl = QLabel("⚡ WS")
            self._ws_status_lbl.setStyleSheet(
                f"color:{DIM}; font-size:10px; font-weight:700; padding:0 6px;")
            self._ws_status_lbl.setToolTip("WebSocket — waiting for connection")
            self.statusBar().addPermanentWidget(self._ws_status_lbl)

        ver_lbl = QLabel(f"  {APP_VERSION}  ")
        ver_lbl.setStyleSheet(
            f"color:{ACCENT}; font-family:{MONO_CSS}; font-weight:700; "
            f"font-size:11px; padding:0 6px;"
        )
        self.statusBar().addPermanentWidget(ver_lbl)

    def _build_table(self):
        cols = ["#", "Symbol", "Price", "24h%", "RSI", "StRSI",
                "MACD", "BB%", "Vol 24h", "Signal", "Pot%", "Exp%", "L/S", "Pattern", "Chart",
                "AGE", "CONF", "1H", ""]

        COL_TIPS = {
            0:  "Rank — sorted by potential score after each scan",
            1:  "Trading pair (always USDT quoted)",
            2:  "Last traded price in USDT",
            3:  "24-hour price change %\n>+8% adds to short score, <-10% adds to long score",
            4:  "RSI (14-period Relative Strength Index)\n"
                "<30 = oversold → long bias  |  >70 = overbought → short bias\n"
                "Scores: <25=+5, <30=+4, <35=+3, <40=+2 (long)\n"
                "        >75=+5, >70=+4, >65=+3, >60=+2 (short)",
            5:  "Stochastic RSI (0–100)\n"
                "<20 = strongly oversold (+2 long)\n"
                ">80 = strongly overbought (+2 short)",
            6:  "MACD histogram value\n"
                "Positive + rising = bullish momentum (+3 long)\n"
                "Negative + falling = bearish momentum (+3 short)",
            7:  "Bollinger Band position (0–100%)\n"
                "0% = price at lower band (buy zone, +3 long)\n"
                "100% = price at upper band (sell zone, +3 short)\n"
                "Score halved if band width > 12% (wide/noisy bands)",
            8:  "24-hour trading volume in USDT",
            9:  "Signal verdict from the confluence scoring system\n"
                "STRONG BUY:  long ≥ 6 and margin ≥ 3\n"
                "BUY:         long ≥ 3 and margin ≥ 2\n"
                "NEUTRAL:     neither side wins clearly\n"
                "SELL:        short ≥ 3 and margin ≥ 2\n"
                "STRONG SELL: short ≥ 6 and margin ≥ 3",
            10: "Potential score (0–100) — composite urgency metric\n"
                "Signal strength: up to 30pts\n"
                "Volume ratio:    up to 25pts\n"
                "BB proximity:    up to 20pts\n"
                "RSI extremity:   up to 15pts\n"
                "StochRSI:        up to 10pts",
            11: "Expected move % — estimated near-term price range\n"
                "Based on ATR (14-period average true range)\n"
                "Multiplied by 1.4 for STRONG signals, 1.1 for BUY/SELL",
            12: "Long score / Short score\n"
                "Raw indicator confluence points for each direction\n"
                "Higher long score = stronger buy case\n"
                "Example: 7/2 = convincing long, 4/3 = weak edge",
            13: "Last detected candlestick pattern\n"
                "Hammer / Bull Engulf = bullish (+2 long, -1 short)\n"
                "Shooting Star / Bear Engulf = bearish (+2 short, -1 long)\n"
                "Squeeze = BB inside Keltner — breakout imminent (+1)\n"
                "Doji = indecision\n"
                "— = no pattern detected",
            14: "Sparkline — mini price chart of last 50 closes",
            15: "Signal age (mm:ss) — time since this signal first appeared\n"
                "Green < 30s (very fresh)\n"
                "Yellow < 5min\n"
                "Red > 5min (stale — treat with caution)",
            16: "Confirmation count — consecutive scans with the same signal\n"
                "Higher = more reliable. 5+ scans = fully confirmed",
            17: "1-hour trend direction\n"
                "↑ Up  = 1H close above 1H EMA (aligns with long)\n"
                "↓ Down = 1H close below 1H EMA (aligns with short)\n"
                "→ Flat = no clear trend",
        }

        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)

        tip_header = TooltipHeaderView(Qt.Orientation.Horizontal, COL_TIPS, t)
        tip_header.setStretchLastSection(False)
        tip_header.setSectionsClickable(True)
        tip_header.sectionClicked.connect(self._on_header_clicked)
        t.setHorizontalHeader(tip_header)

        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(False)
        t.setSortingEnabled(False)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(True)

        hdr = t.horizontalHeader()
        for i in range(len(cols) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(18, QHeaderView.ResizeMode.Stretch)
        t.setColumnHidden(18, True)

        t.itemDoubleClicked.connect(self._on_row_double_click)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(self._scanner_context_menu)
        return t

    _COL_FRACS = {
        0:  0.025, 1:  0.070, 2:  0.075, 3:  0.052, 4:  0.044,
        5:  0.048, 6:  0.048, 7:  0.044, 8:  0.068, 9:  0.088,
        10: 0.044, 11: 0.044, 12: 0.055, 13: 0.100, 14: 0.068,
        15: 0.048, 16: 0.048, 17: 0.040,
    }
    _COL_MINS = {
        0:  28,  1:  62,  2:  68,  3:  48,  4:  40,
        5:  44,  6:  44,  7:  40,  8:  64,  9:  84,
        10: 40,  11: 40,  12: 50,  13: 86,  14: 62,
        15: 42,  16: 42,  17: 32,
    }

    def _reflow_columns(self):
        total = self.table.viewport().width()
        if total < 100:
            return
        total_frac = sum(self._COL_FRACS.values()) or 1.0
        self._programmatic_resize = True
        try:
            for col, frac in self._COL_FRACS.items():
                min_w = self._COL_MINS.get(col, 44)
                self.table.setColumnWidth(col, max(min_w, int(total * frac / total_frac)))
        finally:
            self._programmatic_resize = False

    def _build_trades_tab(self):
        self._load_trades()
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel("Right-click any coin in the Scanner tab to open a BUY trade.")
        hint.setStyleSheet(f"color:{DIM}; font-size:11px; padding:4px 0;")
        root.addWidget(hint)

        self.tr_summary = QLabel("No trades yet")
        self.tr_summary.setStyleSheet(f"color:{DIM}; font-size:11px; font-weight:700; padding:2px 0;")
        root.addWidget(self.tr_summary)

        stats_equity_row = QHBoxLayout()
        stats_equity_row.setSpacing(12)

        stats_frame = QFrame()
        stats_frame.setStyleSheet(f"background:{CARD}; border:1px solid {BORDER}; border-radius:6px;")
        stats_frame.setFixedWidth(260)
        stats_lay = QVBoxLayout(stats_frame)
        stats_lay.setContentsMargins(12, 10, 12, 10)
        stats_lay.setSpacing(6)
        stats_title = QLabel("📈  Trade Statistics")
        stats_title.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700; border:none;")
        stats_lay.addWidget(stats_title)
        self._stats_labels = {}
        stat_keys = [
            ("total",    "Total trades"),
            ("open",     "Open trades"),
            ("wins",     "Wins"),
            ("losses",   "Losses"),
            ("winrate",  "Win rate"),
            ("avg_win",  "Avg win"),
            ("avg_loss", "Avg loss"),
            ("best",     "Best trade"),
            ("worst",    "Worst trade"),
            ("pf",       "Profit factor"),
            ("total_pnl","Total P&L"),
        ]
        for key, label in stat_keys:
            row_w = QWidget(); row_w.setStyleSheet("background:transparent; border:none;")
            row_h = QHBoxLayout(row_w); row_h.setContentsMargins(0,0,0,0); row_h.setSpacing(4)
            lbl = QLabel(label + ":"); lbl.setStyleSheet(f"color:{DIM}; font-size:11px; border:none;")
            val = QLabel("—");         val.setStyleSheet(f"color:{WHITE}; font-size:11px; font-weight:700; border:none;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_h.addWidget(lbl); row_h.addStretch(); row_h.addWidget(val)
            stats_lay.addWidget(row_w)
            self._stats_labels[key] = val
        stats_lay.addStretch()
        stats_equity_row.addWidget(stats_frame)

        equity_frame = QFrame()
        equity_frame.setStyleSheet(f"background:{CARD}; border:1px solid {BORDER}; border-radius:6px;")
        equity_frame.setMinimumHeight(180)
        equity_lay = QVBoxLayout(equity_frame)
        equity_lay.setContentsMargins(12, 10, 12, 10)
        equity_lay.setSpacing(4)
        eq_title = QLabel("📊  Equity Curve  (cumulative P&L)")
        eq_title.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700; border:none;")
        equity_lay.addWidget(eq_title)
        self._equity_canvas = _EquityCanvas()
        equity_lay.addWidget(self._equity_canvas)
        stats_equity_row.addWidget(equity_frame, 1)

        root.addLayout(stats_equity_row)

        self.tr_table = QTableWidget(0, 11)
        self.tr_table.setHorizontalHeaderLabels([
            "Opened", "Symbol", "Side", "Entry $", "Qty", "SL $", "TP $", "Live $", "Exit $", "P&L", "Status"
        ])
        self.tr_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tr_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tr_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tr_table.verticalHeader().setVisible(False)
        hdr = self.tr_table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(10, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.setColumnWidth(7, 130)
        self.tr_table.setColumnWidth(8, 130)
        self.tr_table.setAlternatingRowColors(False)
        self.tr_table.setSortingEnabled(False)
        self.tr_table.setShowGrid(True)
        self.tr_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tr_table.customContextMenuRequested.connect(self._trades_context_menu)
        root.addWidget(self.tr_table)

        btn_row = QHBoxLayout()
        close_btn = QPushButton("✓  Close Selected")
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet(
            f"background:{CARD}; color:{GREEN}; border:1px solid {GREEN}; "
            f"border-radius:4px; font-weight:700; padding:0 14px;")
        close_btn.clicked.connect(self._close_trade_dialog)

        edit_btn = QPushButton("✎  Edit Selected")
        edit_btn.setFixedHeight(30)
        edit_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; padding:0 14px;")
        edit_btn.clicked.connect(self._edit_trade_dialog)

        del_btn = QPushButton("✕  Delete Selected")
        del_btn.setFixedHeight(30)
        del_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:0 14px;")
        del_btn.clicked.connect(self._delete_trade)

        remove_won_btn = QPushButton("🗑  Remove Closed")
        remove_won_btn.setFixedHeight(30)
        remove_won_btn.setToolTip("Remove all closed trades (WIN and LOSS) from history")
        remove_won_btn.setStyleSheet(
            f"background:{CARD}; color:#f0c040; border:1px solid #f0c040; "
            f"border-radius:4px; padding:0 14px;")
        remove_won_btn.clicked.connect(self._remove_won_trades)

        csv_btn = QPushButton("⬇  Export CSV")
        csv_btn.setFixedHeight(30)
        csv_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:0 14px;")
        csv_btn.clicked.connect(self._export_trades_csv)

        btn_row.addWidget(close_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(remove_won_btn)
        btn_row.addWidget(csv_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._refresh_trades_table()
        return w

    def _scanner_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._results):
            return
        r = self._results[row]
        sym = r["symbol"].replace("_USDT", "").replace("USDT", "")
        price = r["price"]
        sig   = r["signal"]

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{CARD}; border:1px solid {BORDER}; color:{WHITE}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 20px; border-radius:3px; }}"
            f"QMenu::item:selected {{ background:{ACCENT}; color:{DARK}; }}"
            f"QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}"
        )

        title_act = menu.addAction(f"  {sym}  —  ${price:.6f}")
        title_act.setEnabled(False)

        api_key_set = bool(TRADING_CFG["api_key"] or
                           (hasattr(self, 'cfg_api_key') and self.cfg_api_key.text().strip()))
        if api_key_set:
            env = "🧪 TESTNET" if TRADING_CFG["testnet"] else "🔴 LIVE"
            mode_txt = f"  {env}  —  order will execute on Binance"
        else:
            mode_txt = "  📋 Journal only  —  no API key"
        mode_act = menu.addAction(mode_txt)
        mode_act.setEnabled(False)
        mode_font = mode_act.font()
        mode_font.setItalic(True)
        mode_act.setFont(mode_font)

        menu.addSeparator()

        long_act  = menu.addAction(f"📈  BUY  {sym}")
        short_act = menu.addAction(f"📉  SELL  {sym}  (margin — coming soon)")
        short_act.setEnabled(False)
        menu.addSeparator()

        detail_act  = menu.addAction("🔍  View Details")
        binance_act = menu.addAction(f"🌐  Open {sym} on Binance")
        tv_act      = menu.addAction(f"📈  Open {sym} on TradingView")

        if sig == "PRE-BREAKOUT":
            long_act.setText(f"📈  BUY  {sym}  ← ⚡ PRE-BREAKOUT")
        elif "BUY" in sig:
            long_act.setText(f"📈  BUY  {sym}  ← {sig}")
        elif "SELL" in sig:
            short_act.setText(f"📉  SELL  {sym}  ← {sig}  (coming soon)")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == long_act:
            self._record_trade(r, "LONG")
        elif action == short_act:
            self._record_trade(r, "SHORT")
        elif action == detail_act:
            self._show_detail_popup(r)
        elif action == binance_act:
            sym_url = sym.replace("_", "")
            open_url(f"https://www.binance.com/en/trade/{sym_url}USDT?type=spot&interval=5m")
        elif action == tv_act:
            sym_url = sym.replace("_", "")
            open_url(f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym_url}USDT&interval=5")

    def _trades_context_menu(self, pos):
        row = self.tr_table.rowAt(pos.y())
        if row < 0:
            return
        item = self.tr_table.item(row, 0)
        if item is None:
            return
        tid   = item.data(Qt.ItemDataRole.UserRole)
        trade = next((t for t in self._trades if t["id"] == tid), None)
        if trade is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{CARD}; border:1px solid {BORDER}; color:{WHITE}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 20px; border-radius:3px; }}"
            f"QMenu::item:selected {{ background:{ACCENT}; color:{DARK}; }}"
            f"QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}"
        )

        sym    = trade["symbol"]
        side   = trade["side"]
        status = trade["status"]

        title_act = menu.addAction(f"  {'BUY' if side == 'LONG' else 'SELL'} {sym}")
        title_act.setEnabled(False)
        menu.addSeparator()

        close_act = edit_act = None
        if status == "OPEN":
            cur = (self._live_prices.get(trade["symbol"]) or
                   next((r["price"] for r in self._results if r["symbol"] == trade["symbol"]), None))
            label = f"✓  Close at current price  (${cur:.6f})" if cur else "✓  Close at price..."
            close_act = menu.addAction(label)
            edit_act  = menu.addAction("✎  Edit entry / SL / TP")
            menu.addSeparator()

        del_act = menu.addAction("✕  Delete")
        binance_act2 = menu.addAction(f"🌐  Open {sym} on Binance")
        tv_act2      = menu.addAction(f"📈  Open {sym} on TradingView")

        action = menu.exec(self.tr_table.viewport().mapToGlobal(pos))
        if action == close_act:
            cur = (self._live_prices.get(trade["symbol"]) or
                   next((r["price"] for r in self._results if r["symbol"] == trade["symbol"]), None))
            self._close_trade_dialog(tid=tid, prefill_price=cur)
        elif action == edit_act:
            self._edit_trade_dialog(tid=tid)
        elif action == del_act:
            self._trades = [t for t in self._trades if t["id"] != tid]
            self._save_trades()
            self._refresh_trades_table()
        elif action == binance_act2:
            sym_url = trade["symbol"].replace("_USDT","").replace("USDT","").replace("_","")
            open_url(f"https://www.binance.com/en/trade/{sym_url}USDT?type=spot&interval=5m")
        elif action == tv_act2:
            sym_url = trade["symbol"].replace("_USDT","").replace("USDT","").replace("_","")
            open_url(f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym_url}USDT&interval=5")

    def _alerts_context_menu(self, pos):
        row = self.alert_log_table.rowAt(pos.y())
        if row < 0:
            return
        time_item = self.alert_log_table.item(row, 0)
        if time_item is None:
            return
        data = time_item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        sym    = data.get("symbol", "")
        sig    = data.get("signal", "")
        price  = data.get("price", 0)
        if not sym:
            return

        sym_full = sym.replace("_", "") + "USDT"
        sym_display = sym.replace("USDT", "")

        r = {"symbol": sym_full, "signal": sig, "price": price,
             "signal_conf": 2, "trend_1h": "flat"}

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{CARD}; border:1px solid {BORDER}; color:{WHITE}; padding:4px; }}"
            f"QMenu::item {{ padding:6px 20px; border-radius:3px; }}"
            f"QMenu::item:selected {{ background:{ACCENT}; color:{DARK}; }}"
            f"QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}"
        )

        title_act = menu.addAction(f"  {sig}  {sym_display}")
        title_act.setEnabled(False)
        menu.addSeparator()

        long_act  = menu.addAction(f"📈  BUY  {sym_display}")
        short_act = menu.addAction(f"📉  SELL  {sym_display}  (margin — coming soon)")
        short_act.setEnabled(False)
        if "BUY" in sig:
            long_act.setText(f"📈  BUY  {sym_display}  ← {sig}")
        elif "SELL" in sig:
            short_act.setText(f"📉  SELL  {sym_display}  ← {sig}  (coming soon)")
        menu.addSeparator()

        binance_act = menu.addAction(f"🌐  Open {sym_display} on Binance")
        tv_act      = menu.addAction(f"📈  Open {sym_display} on TradingView")
        menu.addSeparator()
        remove_act  = menu.addAction(f"🗑  Remove this alert")

        action = menu.exec(self.alert_log_table.viewport().mapToGlobal(pos))
        if action == long_act:
            self._record_trade(r, "LONG")
        elif action == binance_act:
            sym_url = sym.replace("_", "") + "USDT"
            open_url(f"https://www.binance.com/en/trade/{sym_url}?type=spot&interval=5m")
        elif action == tv_act:
            sym_url = sym.replace("_", "") + "USDT"
            open_url(f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym_url}&interval=5")
        elif action == remove_act:
            self._remove_alert_row(row)

    def _record_trade(self, r, side):
        sym   = r["symbol"].replace("_USDT", "").replace("USDT", "")
        price = r["price"]
        sl_pct = CFG["sl_pct"] / 100
        tp_pct = CFG["tp_pct"] / 100

        if side == "LONG":
            suggested_sl = round(price * (1 - sl_pct), 8)
            suggested_tp = round(price * (1 + tp_pct), 8)
        else:
            suggested_sl = round(price * (1 + sl_pct), 8)
            suggested_tp = round(price * (1 - tp_pct), 8)

        api_ready = bool(TRADING_CFG["api_key"] and TRADING_CFG["api_secret"])

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Open {'BUY' if side == 'LONG' else 'SELL'} — {sym}")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
        vlay = QVBoxLayout(dlg)
        vlay.setSpacing(10)

        accent = GREEN if side == "LONG" else RED
        icon   = "📈" if side == "LONG" else "📉"

        if api_ready:
            env  = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
            col  = GREEN if TRADING_CFG["testnet"] else RED
            mode_lbl = QLabel(f"{'🧪' if TRADING_CFG['testnet'] else '🔴'}  {env} — order will be placed on Binance")
            mode_lbl.setStyleSheet(
                f"background:{'#003a1a' if TRADING_CFG['testnet'] else '#3a0000'}; "
                f"color:{col}; font-size:11px; font-weight:700; padding:6px; border-radius:4px;")
        else:
            mode_lbl = QLabel("📋  Journal only — no API keys configured")
            mode_lbl.setStyleSheet(
                f"background:#1a1a2e; color:{DIM}; font-size:11px; padding:6px; border-radius:4px;")
        vlay.addWidget(mode_lbl)

        header = QLabel(f"{icon}  <b>{side}</b>  {sym}/USDT  —  ${price:.8f}")
        header.setStyleSheet(f"color:{accent}; font-size:13px; padding:4px 0;")
        vlay.addWidget(header)

        balance_row = QHBoxLayout()
        balance_lbl = QLabel("USDT Balance:")
        balance_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        balance_val = QLabel("Fetching…" if api_ready else "—")
        balance_val.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700;")
        balance_row.addWidget(balance_lbl)
        balance_row.addWidget(balance_val)
        balance_row.addStretch()
        vlay.addLayout(balance_row)

        pct_row = QHBoxLayout()
        pct_lbl = QLabel("Use:")
        pct_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        pct_row.addWidget(pct_lbl)
        _avail_usdt = [0.0]

        usdt_spin = QDoubleSpinBox()
        usdt_spin.setRange(0, 9999999)
        usdt_spin.setDecimals(2)
        usdt_spin.setValue(0)
        usdt_spin.setEnabled(api_ready)

        for pct in (25, 50, 75, 100):
            btn = QPushButton(f"{pct}%")
            btn.setFixedHeight(26)
            btn.setFixedWidth(48)
            btn.setStyleSheet(
                f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
                f"border-radius:4px; font-size:11px;")
            btn.setEnabled(api_ready)
            def _set_pct(p=pct):
                usdt_spin.setValue(round(_avail_usdt[0] * p / 100, 2))
            btn.clicked.connect(_set_pct)
            pct_row.addWidget(btn)
        pct_row.addStretch()
        vlay.addLayout(pct_row)

        grid = QGridLayout(); grid.setSpacing(8)
        lbl_s = f"color:{DIM}; font-size:11px;"

        entry_spin = QDoubleSpinBox()
        entry_spin.setRange(0.0000001,999999); entry_spin.setDecimals(8); entry_spin.setValue(price)

        qty_spin = QDoubleSpinBox(); qty_spin.setRange(0, 999999999)
        qty_spin.setDecimals(6); qty_spin.setValue(0)
        qty_spin.setEnabled(not api_ready)

        sl_spin = QDoubleSpinBox()
        sl_spin.setRange(0.0000001,999999); sl_spin.setDecimals(8); sl_spin.setValue(suggested_sl)

        sl_pct_spin = QDoubleSpinBox(); sl_pct_spin.setRange(0.01, 50)
        sl_pct_spin.setDecimals(2); sl_pct_spin.setSuffix("%")
        sl_pct_spin.setValue(CFG["sl_pct"])
        sl_pct_spin.setFixedWidth(80)
        sl_pct_spin.setToolTip("Stop Loss as % from entry")

        tp_spin = QDoubleSpinBox()
        tp_spin.setRange(0.0000001,999999); tp_spin.setDecimals(8); tp_spin.setValue(suggested_tp)

        tp_pct_spin = QDoubleSpinBox(); tp_pct_spin.setRange(0.01, 100)
        tp_pct_spin.setDecimals(2); tp_pct_spin.setSuffix("%")
        tp_pct_spin.setValue(CFG["tp_pct"])
        tp_pct_spin.setFixedWidth(80)
        tp_pct_spin.setToolTip("Take Profit as % from entry")

        _syncing = [False]

        def _sl_price_changed():
            if _syncing[0]: return
            e = entry_spin.value(); s = sl_spin.value()
            if e > 0 and s > 0:
                _syncing[0] = True
                sl_pct_spin.setValue(round(abs(e - s) / e * 100, 2))
                _syncing[0] = False

        def _sl_pct_changed():
            if _syncing[0]: return
            e = entry_spin.value(); p = sl_pct_spin.value()
            if e > 0:
                _syncing[0] = True
                sl_spin.setValue(round(e * (1 - p/100) if side == "LONG" else e * (1 + p/100), 8))
                _syncing[0] = False

        def _tp_price_changed():
            if _syncing[0]: return
            e = entry_spin.value(); t = tp_spin.value()
            if e > 0 and t > 0:
                _syncing[0] = True
                tp_pct_spin.setValue(round(abs(t - e) / e * 100, 2))
                _syncing[0] = False

        def _tp_pct_changed():
            if _syncing[0]: return
            e = entry_spin.value(); p = tp_pct_spin.value()
            if e > 0:
                _syncing[0] = True
                tp_spin.setValue(round(e * (1 + p/100) if side == "LONG" else e * (1 - p/100), 8))
                _syncing[0] = False

        sl_spin.valueChanged.connect(_sl_price_changed)
        sl_pct_spin.valueChanged.connect(_sl_pct_changed)
        tp_spin.valueChanged.connect(_tp_price_changed)
        tp_pct_spin.valueChanged.connect(_tp_pct_changed)

        def _entry_changed():
            _sl_pct_changed()
            _tp_pct_changed()
            _update_hint()
        entry_spin.valueChanged.connect(_entry_changed)

        note_edit = QLineEdit(); note_edit.setPlaceholderText("Optional note…")

        def _spin_select_all(spin):
            spin.lineEdit().focusInEvent = lambda e: (
                QLineEdit.focusInEvent(spin.lineEdit(), e),
                QTimer.singleShot(0, spin.selectAll))
            spin.lineEdit().mouseReleaseEvent = lambda e: spin.selectAll()
        for _sp in (usdt_spin, entry_spin, sl_spin, tp_spin, sl_pct_spin, tp_pct_spin):
            _spin_select_all(_sp)

        usdt_journal = QDoubleSpinBox()
        usdt_journal.setRange(0, 9999999); usdt_journal.setDecimals(2)
        usdt_journal.setPrefix("$"); usdt_journal.setValue(0)
        usdt_journal.setVisible(not api_ready)

        pnl_hint = QLabel()
        pnl_hint.setStyleSheet(f"color:{DIM}; font-size:11px;")

        def _update_hint():
            e = entry_spin.value()
            if api_ready:
                u = usdt_spin.value()
                q = u / e if e > 0 else 0
                qty_spin.setValue(round(q, 6))
            else:
                q = qty_spin.value()
                u = q * e
            if q > 0 and e > 0:
                sl_risk = abs(sl_spin.value() - e) * q
                tp_gain = abs(tp_spin.value() - e) * q
                rr = tp_gain / sl_risk if sl_risk > 0 else 0
                pnl_hint.setText(
                    f"Cost: ${u:.2f} USDT  |  SL risk: -${sl_risk:.4f}  |  "
                    f"TP gain: +${tp_gain:.4f}  |  R/R: {rr:.2f}x")
            else:
                pnl_hint.setText("Enter amount to see cost and risk")

        for ww in (qty_spin, sl_spin, tp_spin, usdt_spin, sl_pct_spin, tp_pct_spin):
            ww.valueChanged.connect(_update_hint)

        rows_cfg = [("Entry price", entry_spin)]
        if api_ready:
            rows_cfg.append(("USDT amount", usdt_spin))
        else:
            rows_cfg.append(("USDT amount", usdt_journal))
            rows_cfg.append(("Qty (coins)", qty_spin))

        for i, (lbl, widget) in enumerate(rows_cfg):
            l = QLabel(lbl); l.setStyleSheet(lbl_s)
            grid.addWidget(l, i, 0)
            grid.addWidget(widget, i, 1, 1, 2)

        row_sl = len(rows_cfg)
        sl_lbl = QLabel("Stop Loss"); sl_lbl.setStyleSheet(lbl_s)
        grid.addWidget(sl_lbl, row_sl, 0)
        grid.addWidget(sl_spin, row_sl, 1)
        grid.addWidget(sl_pct_spin, row_sl, 2)

        row_tp = row_sl + 1
        tp_lbl = QLabel("Take Profit"); tp_lbl.setStyleSheet(lbl_s)
        grid.addWidget(tp_lbl, row_tp, 0)
        grid.addWidget(tp_spin, row_tp, 1)
        grid.addWidget(tp_pct_spin, row_tp, 2)

        row_note = row_tp + 1
        note_lbl = QLabel("Note"); note_lbl.setStyleSheet(lbl_s)
        grid.addWidget(note_lbl, row_note, 0)
        grid.addWidget(note_edit, row_note, 1, 1, 2)

        grid.setColumnStretch(1, 1)
        vlay.addLayout(grid)
        vlay.addWidget(pnl_hint)

        if api_ready and TRADING_CFG["oco_enabled"]:
            oco_note = QLabel("🔒  OCO stop-loss will be placed on Binance after buy")
            oco_note.setStyleSheet(f"color:{YELLOW}; font-size:10px; padding:2px 0;")
            vlay.addWidget(oco_note)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton(f"{icon}  Confirm {'BUY' if side == 'LONG' else 'SELL'}")
        ok_btn.setStyleSheet(
            f"background:{'#002a1a' if side=='LONG' else '#2a0010'}; color:{accent}; "
            f"border:1px solid {accent}; border-radius:4px; font-weight:700; padding:4px 16px;")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px; padding:4px 12px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn); btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)

        order_status = QLabel("")
        order_status.setStyleSheet(f"color:{DIM}; font-size:11px; padding:2px 0;")
        order_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vlay.addWidget(order_status)

        if api_ready:
            class _BalFetch(QThread):
                done = pyqtSignal(bool, float)
                def run(self_):
                    ok, bal = _trader.get_usdt_balance()
                    self_.done.emit(ok, bal)
            def _on_bal(ok, bal):
                if ok:
                    _avail_usdt[0] = bal
                    balance_val.setText(f"${bal:,.2f} USDT")
                    usdt_spin.setMaximum(bal)
                    usdt_spin.setValue(round(bal, 2))
                    _update_hint()
                else:
                    balance_val.setText("fetch failed")
                    balance_val.setStyleSheet(f"color:{RED}; font-size:11px; font-weight:700;")
            self._bal_thread = _BalFetch()
            self._bal_thread.done.connect(_on_bal)
            self._bal_thread.start()

        _update_hint()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        entry_price = entry_spin.value()
        sl_price    = sl_spin.value()
        tp_price    = tp_spin.value()
        note        = note_edit.text().strip()

        if api_ready:
            usdt_amount = usdt_spin.value()
            if usdt_amount <= 0:
                QMessageBox.warning(self, "Invalid Amount", "USDT amount must be greater than 0.")
                return

            symbol = r["symbol"]

            safety_ok, safety_reason = check_trade_safety(r, self._trades)
            if not safety_ok:
                reply = QMessageBox.warning(
                    self, "Trade Safety Check Failed",
                    f"Safety filter blocked this trade:\n\n{safety_reason}\n\n"
                    f"Override and trade anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            sym_ok, sym_info = _trader.get_symbol_info(symbol)
            if not sym_ok:
                env = "testnet" if TRADING_CFG["testnet"] else "live Binance"
                reply = QMessageBox.warning(
                    self, "Symbol Not on Testnet",
                    f"{symbol} is not available on {env}.\n\n"
                    f"The scanner uses live Binance data but the testnet has fewer coins.\n\n"
                    f"Would you like to record this as a journal trade instead\n"
                    f"(no real order placed)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                api_ready = False

            if api_ready:
                prog = QDialog(self)
                prog.setWindowTitle("Placing Order…")
                prog.setModal(True)
                prog.setFixedSize(320, 100)
                prog.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
                prog_lay = QVBoxLayout(prog)
                prog_lbl = QLabel("Placing market BUY on Binance…")
                prog_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                prog_lbl.setStyleSheet(f"color:{ACCENT}; font-size:12px;")
                prog_lay.addWidget(prog_lbl)
                prog.show()
                QApplication.processEvents()

                ok, order = _trader.place_market_buy(symbol, usdt_amount)

                if not ok:
                    prog.close()
                    QMessageBox.critical(
                        self, "Order Failed",
                        f"Market BUY failed:\n\n{order.get('error', str(order))}\n\n"
                        f"Trade was NOT recorded."
                    )
                    return

                fills      = order.get("fills", [])
                filled_qty = float(order.get("executedQty", 0))
                if fills:
                    avg_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / filled_qty
                else:
                    avg_price = entry_price
                order_id   = order.get("orderId")

                if side == "LONG":
                    sl_price = round(avg_price * (1 - sl_pct), 8)
                    tp_price = round(avg_price * (1 + tp_pct), 8)
                else:
                    sl_price = round(avg_price * (1 + sl_pct), 8)
                    tp_price = round(avg_price * (1 - tp_pct), 8)

                oco_order_id = None

                if TRADING_CFG["oco_enabled"] and side == "LONG":
                    prog_lbl.setText("Placing OCO stop-loss…")
                    QApplication.processEvents()
                    sl_limit = round(sl_price * 0.999, 8)
                    oco_ok, oco_data = _trader.place_oco_sell(
                        symbol, filled_qty, tp_price, sl_price, sl_limit)
                    if oco_ok:
                        oco_order_id = oco_data.get("orderListId")
                    else:
                        QMessageBox.warning(
                            self, "OCO Warning",
                            f"BUY was filled but OCO stop-loss failed:\n"
                            f"{oco_data.get('error', str(oco_data))}\n\n"
                            f"Trade is recorded. Monitor manually or set OCO manually on Binance."
                        )

                prog.close()

                ok_info, sym_info = _trader.get_symbol_info(symbol)
                step = sym_info.get("stepSize", 0.00000001) if ok_info else 0.00000001
                stored_qty = _trader.round_step(filled_qty, step)

                trade = {
                    "id":           int(datetime.now().timestamp() * 1000),
                    "time":         datetime.now().strftime("%m-%d %H:%M"),
                    "symbol":       symbol,
                    "side":         side,
                    "entry":        round(avg_price, 8),
                    "qty":          stored_qty,
                    "sl":           sl_price,
                    "tp":           tp_price,
                    "note":         note,
                    "exit":         None, "pnl": None, "pnl_pct": None,
                    "status":       "OPEN",
                    "binance_order_id": order_id,
                    "binance_oco_id":   oco_order_id,
                    "live":         not TRADING_CFG["testnet"],
                }
                status_msg = (
                    f"✓ BUY filled: {sym} {filled_qty:.6f} @ ${avg_price:.6f}"
                    + (f"  |  OCO set" if oco_order_id else "")
                )
            else:
                # api_ready became False (symbol not on testnet)
                qty_val = usdt_amount / entry_price if entry_price > 0 else 0
                trade = {
                    "id":     int(datetime.now().timestamp() * 1000),
                    "time":   datetime.now().strftime("%m-%d %H:%M"),
                    "symbol": r["symbol"],
                    "side":   side,
                    "entry":  entry_price,
                    "qty":    round(qty_val, 8),
                    "sl":     sl_price,
                    "tp":     tp_price,
                    "note":   note,
                    "exit":   None, "pnl": None, "pnl_pct": None,
                    "status": "OPEN",
                    "binance_order_id": None,
                    "binance_oco_id":   None,
                    "live":   False,
                }
                status_msg = f"Opened {side} {sym} @ ${entry_price:.6f} (journal only)"
        else:
            qty_val = usdt_journal.value() / entry_price if usdt_journal.value() > 0 else qty_spin.value()
            trade = {
                "id":     int(datetime.now().timestamp() * 1000),
                "time":   datetime.now().strftime("%m-%d %H:%M"),
                "symbol": r["symbol"],
                "side":   side,
                "entry":  entry_price,
                "qty":    round(qty_val, 8),
                "sl":     sl_price,
                "tp":     tp_price,
                "note":   note,
                "exit":   None, "pnl": None, "pnl_pct": None,
                "status": "OPEN",
                "binance_order_id": None,
                "binance_oco_id":   None,
                "live":   False,
            }
            status_msg = f"Opened {side} {sym} @ ${entry_price:.6f} (journal only)"

        self._trades.insert(0, trade)
        self._log_trade_event("OPEN", trade)
        self._save_trades()
        self._refresh_trades_table()

        tabs = self.centralWidget().findChild(QTabWidget)
        if tabs:
            for i in range(tabs.count()):
                if "Trade" in tabs.tabText(i):
                    tabs.setCurrentIndex(i)
                    break
        self._show_status(status_msg)

    def _close_trade_dialog(self, checked=False, tid=None, prefill_price=None):
        if tid is None:
            row = self.tr_table.currentRow()
            if row < 0:
                self._show_status("Select a trade row first")
                return
            item = self.tr_table.item(row, 0)
            if item is None: return
            tid = item.data(Qt.ItemDataRole.UserRole)

        trade = next((t for t in self._trades if t["id"] == tid), None)
        if trade is None or trade["status"] != "OPEN":
            self._show_status("Trade is already closed")
            return

        sym   = trade["symbol"].replace("USDT", "")
        side  = trade["side"]
        entry = trade["entry"]
        qty   = trade["qty"]
        close_label = "Sell" if side == "LONG" else "Buy back"
        accent = RED if side == "LONG" else GREEN

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Close {side} — {sym}")
        dlg.setModal(True)
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
        vlay = QVBoxLayout(dlg)
        vlay.setSpacing(10)

        info = QLabel(
            f"<b>{side} {sym}</b> &nbsp; entry: <b>${entry:.8f}</b> &nbsp; qty: <b>{qty}</b>")
        info.setStyleSheet(f"color:{WHITE}; font-size:12px; padding:4px 0;")
        vlay.addWidget(info)

        grid = QGridLayout(); grid.setSpacing(8)
        lbl_s = f"color:{DIM}; font-size:11px;"

        exit_spin = QDoubleSpinBox()
        exit_spin.setRange(0.0000001,999999); exit_spin.setDecimals(8)
        exit_spin.setValue(prefill_price if prefill_price else entry)

        l = QLabel(f"{close_label} at price"); l.setStyleSheet(lbl_s)
        grid.addWidget(l, 0, 0)
        grid.addWidget(exit_spin, 0, 1)
        vlay.addLayout(grid)

        pnl_lbl = QLabel()
        pnl_lbl.setStyleSheet("font-size:14px; font-weight:700; padding:4px 0;")
        vlay.addWidget(pnl_lbl)

        def _calc():
            ep = exit_spin.value()
            if side == "LONG":
                pnl = (ep - entry) * qty
                pct = (ep - entry) / entry * 100
            else:
                pnl = (entry - ep) * qty
                pct = (entry - ep) / entry * 100
            sign = "+" if pnl >= 0 else ""
            col  = GREEN if pnl >= 0 else RED
            pnl_lbl.setText(f"P&L: {sign}{pnl:.6f} USDT  ({sign}{pct:.2f}%)")
            pnl_lbl.setStyleSheet(f"color:{col}; font-size:14px; font-weight:700; padding:4px 0;")
        exit_spin.valueChanged.connect(_calc)
        _calc()

        btn_row = QHBoxLayout()
        ok_btn = QPushButton(f"✓  Confirm {close_label}")
        ok_btn.setStyleSheet(
            f"background:{'#2a0010' if side=='LONG' else '#002a1a'}; color:{accent}; "
            f"border:1px solid {accent}; border-radius:4px; font-weight:700; padding:4px 16px;")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px; padding:4px 12px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn); btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        ep = exit_spin.value()
        if side == "LONG":
            pnl = (ep - entry) * qty
            pct = (ep - entry) / entry * 100
        else:
            pnl = (entry - ep) * qty
            pct = (entry - ep) / entry * 100

        api_ready   = bool(TRADING_CFG["api_key"] and TRADING_CFG["api_secret"])
        order_id    = trade.get("binance_order_id")
        oco_id      = trade.get("binance_oco_id")
        symbol_full = trade["symbol"]

        if api_ready and order_id is not None:
            if oco_id is not None:
                c_ok, c_data = _trader.cancel_oco(symbol_full, oco_id)
                if not c_ok:
                    self.statusBar().showMessage(
                        f"OCO cancel note: {c_data.get('error','')[:60]}")

            _, s_info = _trader.get_symbol_info(symbol_full)
            sell_qty = _trader.round_step(qty, s_info.get("stepSize", 0.00000001))

            s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
            if not s_ok and "-2010" in str(s_data):
                asset = symbol_full.replace("USDT", "")
                bal_ok, actual_bal = _trader.get_asset_balance(asset)
                if bal_ok and actual_bal > 0:
                    _, s_info = _trader.get_symbol_info(symbol_full)
                    sell_qty = _trader.round_step(actual_bal, s_info.get("stepSize", 1))
                    s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
                    if s_ok:
                        qty = sell_qty
            if not s_ok:
                QMessageBox.critical(
                    self, "Sell Failed",
                    f"Market SELL failed:\n\n{s_data.get('error', str(s_data))}\n\n"
                    f"Trade was NOT closed. Check Binance manually.")
                return

            fills    = s_data.get("fills", [])
            exec_qty = float(s_data.get("executedQty", qty))
            if fills and exec_qty > 0:
                ep = sum(float(f["price"]) * float(f["qty"]) for f in fills) / exec_qty
            if side == "LONG":
                pnl = (ep - entry) * exec_qty
                pct = (ep - entry) / entry * 100
            else:
                pnl = (entry - ep) * exec_qty
                pct = (entry - ep) / entry * 100
            record_trade_loss(pnl)

        trade["exit"]         = round(ep, 8)
        trade["pnl"]          = round(pnl, 8)
        trade["pnl_pct"]      = round(pct, 4)
        trade["status"]       = "WIN" if pnl >= 0 else "LOSS"
        trade["closed"]       = datetime.now().strftime("%m-%d %H:%M")
        trade["close_reason"] = "MANUAL"
        self._log_trade_event("CLOSE", trade)
        self._save_trades()
        self._refresh_trades_table()
        sign = "+" if pnl >= 0 else ""
        self.statusBar().showMessage(
            f"Closed {side} {sym}: {sign}{pnl:.6f} USDT ({sign}{pct:.2f}%)")

    def _edit_trade_dialog(self, checked=False, tid=None):
        if tid is None:
            row = self.tr_table.currentRow()
            if row < 0:
                self._show_status("Select a trade row first")
                return
            item = self.tr_table.item(row, 0)
            if item is None: return
            tid = item.data(Qt.ItemDataRole.UserRole)

        trade = next((t for t in self._trades if t["id"] == tid), None)
        if trade is None:
            return

        sym   = trade["symbol"].replace("USDT","")
        side  = trade["side"]
        accent = GREEN if side == "LONG" else RED

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit Trade — {side} {sym}")
        dlg.setModal(True)
        dlg.setMinimumWidth(340)
        dlg.setStyleSheet(f"background:{DARK2}; color:{WHITE};")
        vlay = QVBoxLayout(dlg)
        vlay.setSpacing(10)

        grid = QGridLayout(); grid.setSpacing(8)
        lbl_s = f"color:{DIM}; font-size:11px;"

        entry_spin = QDoubleSpinBox(); entry_spin.setRange(0.0000001,999999); entry_spin.setDecimals(8); entry_spin.setValue(trade["entry"])
        qty_spin   = QDoubleSpinBox(); qty_spin.setRange(0,999999999);        qty_spin.setDecimals(4);   qty_spin.setValue(trade["qty"])
        sl_spin    = QDoubleSpinBox(); sl_spin.setRange(0.0000001,999999);    sl_spin.setDecimals(8);    sl_spin.setValue(trade["sl"] or 0)
        tp_spin    = QDoubleSpinBox(); tp_spin.setRange(0.0000001,999999);    tp_spin.setDecimals(8);    tp_spin.setValue(trade["tp"] or 0)
        note_edit  = QLineEdit(); note_edit.setText(trade.get("note",""))

        for i, (lbl, widget) in enumerate([
            ("Entry price",       entry_spin),
            ("Quantity (coins)",  qty_spin),
            ("Stop Loss",         sl_spin),
            ("Take Profit",       tp_spin),
            ("Note",              note_edit),
        ]):
            l = QLabel(lbl); l.setStyleSheet(lbl_s)
            grid.addWidget(l, i, 0)
            grid.addWidget(widget, i, 1)
        vlay.addLayout(grid)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("✓  Save Changes")
        ok_btn.setStyleSheet(
            f"background:{CARD}; color:{accent}; border:1px solid {accent}; "
            f"border-radius:4px; font-weight:700; padding:4px 16px;")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px; padding:4px 12px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(ok_btn); btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        trade["entry"] = entry_spin.value()
        trade["qty"]   = qty_spin.value()
        trade["sl"]    = sl_spin.value()
        trade["tp"]    = tp_spin.value()
        trade["note"]  = note_edit.text().strip()
        self._save_trades()
        self._refresh_trades_table()
        if self._ws_feed:
            syms = {t["symbol"] for t in self._trades if t["status"] == "OPEN"}
            self._ws_feed.subscribe(syms)
        self._show_status(f"Trade updated: {side} {sym}")

    def _delete_trade(self):
        rows = self.tr_table.selectionModel().selectedRows()
        if not rows:
            self._show_status("Select one or more trade rows first")
            return
        tids = set()
        for idx in rows:
            item = self.tr_table.item(idx.row(), 0)
            if item:
                tids.add(item.data(Qt.ItemDataRole.UserRole))
        if not tids:
            return
        reply = QMessageBox.question(
            self, "Delete Trades",
            f"Delete {len(tids)} trade(s)? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._trades = [t for t in self._trades if t["id"] not in tids]
        self._save_trades()
        self._refresh_trades_table()

    def _remove_won_trades(self):
        closed = [t for t in self._trades if t["status"] in ("WIN", "LOSS")]
        if not closed:
            self._show_status("No closed trades to remove")
            return
        wins   = sum(1 for t in closed if t["status"] == "WIN")
        losses = sum(1 for t in closed if t["status"] == "LOSS")
        reply = QMessageBox.question(
            self, "Remove Closed Trades",
            f"Remove all {len(closed)} closed trade(s) from history?\n"
            f"({wins} wins, {losses} losses)\n\nOpen trades will not be affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        closed_ids = {t["id"] for t in closed}
        self._trades = [t for t in self._trades if t["id"] not in closed_ids]
        self._save_trades()
        self._refresh_trades_table()
        self._show_status(f"Removed {len(closed_ids)} closed trade(s)")

    def _refresh_trades_table(self):
        try:
            self._do_refresh_trades_table()
        except Exception as e:
            import traceback; traceback.print_exc()

    def _do_refresh_trades_table(self):
        if not hasattr(self, 'tr_table'):
            return
        self.tr_table.setRowCount(0)

        open_trades   = [t for t in self._trades if t["status"] == "OPEN"]
        closed_trades = [t for t in self._trades if t["status"] != "OPEN"]
        total_pnl  = sum(t.get("pnl") or 0 for t in closed_trades)
        wins       = sum(1 for t in closed_trades if (t.get("pnl") or 0) >= 0)
        losses     = len(closed_trades) - wins
        win_rate   = wins / len(closed_trades) * 100 if closed_trades else 0

        for trade in open_trades + closed_trades:
            r      = self.tr_table.rowCount()
            self.tr_table.insertRow(r)
            self.tr_table.setRowHeight(r, 34)

            status = trade["status"]
            if status == "OPEN":
                row_bg = QColor("#001525")
            elif (trade.get("pnl") or 0) >= 0:
                row_bg = QColor(STRONG_BUY_BG)
            else:
                row_bg = QColor(STRONG_SELL_BG)

            def cell(text, color=WHITE, bold=False,
                     align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                     tid=trade["id"]):
                it = QTableWidgetItem(str(text))
                it.setForeground(QBrush(QColor(color)))
                it.setBackground(QBrush(row_bg))
                it.setTextAlignment(align)
                if bold:
                    f = it.font(); f.setBold(True); it.setFont(f)
                it.setData(Qt.ItemDataRole.UserRole, tid)
                return it

            left   = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
            center = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter
            side   = trade["side"]
            side_col = GREEN if side == "LONG" else RED
            side_label = "BUY" if side == "LONG" else "SELL"

            if trade.get("pnl") is not None:
                pnl     = trade["pnl"]
                pct     = trade.get("pnl_pct", 0) or 0
                sign    = "+" if pct >= 0 else ""
                if pnl == 0 and pct != 0:
                    pnl_str = f"{sign}{pct:.2f}%  (no qty)"
                else:
                    pnl_str = f"{sign}{pnl:.4f} USDT  ({sign}{pct:.2f}%)"
                pnl_col = GREEN if pct >= 0 else RED
            else:
                sym_full2 = trade["symbol"]
                cur = (self._live_prices.get(sym_full2) or
                       next((res["price"] for res in self._results if res["symbol"] == sym_full2), None))
                if cur:
                    entry = trade["entry"]
                    qty   = trade.get("qty", 0)
                    if side == "LONG":
                        upct = (cur - entry) / entry * 100
                        upnl = (cur - entry) * qty
                    else:
                        upct = (entry - cur) / entry * 100
                        upnl = (entry - cur) * qty
                    sign = "+" if upct >= 0 else ""
                    if qty > 0:
                        pnl_str = f"{sign}{upnl:.4f} ({sign}{upct:.2f}%)"
                    else:
                        pnl_str = f"{sign}{upct:.2f}%"
                    pnl_col = GREEN if upct >= 0 else RED
                else:
                    pnl_str = "⏳ fetching…"
                    pnl_col = DIM

            status_col = ACCENT if status == "OPEN" else (GREEN if (trade.get("pnl") or 0) >= 0 else RED)
            if trade.get("exit") and status != "OPEN":
                exit_str = f"${trade['exit']:.8f}"
            else:
                exit_str = "—"

            sym_full = trade["symbol"]
            live_price = (self._live_prices.get(sym_full) or
                         next((res["price"] for res in self._results if res["symbol"] == sym_full), None))
            if status == "OPEN":
                live_str = f"${live_price:.8f}" if live_price else "⏳"
                live_col = ACCENT
            else:
                live_str = "—"
                live_col = DIM

            self.tr_table.setItem(r, 0,  cell(trade["time"],              DIM,       align=left))
            self.tr_table.setItem(r, 1,  cell(trade["symbol"].replace("USDT",""), ACCENT, bold=True, align=left))
            self.tr_table.setItem(r, 2,  cell(side_label,                 side_col,  bold=True, align=center))
            self.tr_table.setItem(r, 3,  cell(f"${trade['entry']:.8f}",   WHITE))
            self.tr_table.setItem(r, 4,  cell(f"{trade['qty']}",          WHITE))
            self.tr_table.setItem(r, 5,  cell(f"${trade['sl']:.8f}" if trade.get("sl") else "—", DIM))
            self.tr_table.setItem(r, 6,  cell(f"${trade['tp']:.8f}" if trade.get("tp") else "—", DIM))
            self.tr_table.setItem(r, 7,  cell(live_str,                   live_col, bold=True))
            self.tr_table.setItem(r, 8,  cell(exit_str,                   WHITE))
            self.tr_table.setItem(r, 9,  cell(pnl_str,                    pnl_col, bold=True))
            self.tr_table.setItem(r, 10, cell(status,                     status_col, bold=True, align=center))

        if hasattr(self, 'tr_summary'):
            if not self._trades:
                self.tr_summary.setText("No trades yet — right-click a coin in Scanner to begin")
                self.tr_summary.setStyleSheet(f"color:{DIM}; font-size:11px; font-weight:700; padding:2px 0;")
            else:
                sign = "+" if total_pnl >= 0 else ""
                col  = GREEN if total_pnl > 0 else RED if total_pnl < 0 else DIM
                self.tr_summary.setText(
                    f"Open: {len(open_trades)}  |  Closed: {len(closed_trades)}  |  "
                    f"Win rate: {win_rate:.0f}%  ({wins}W / {losses}L)  |  "
                    f"Total P&L: {sign}{total_pnl:.4f} USDT")
                self.tr_summary.setStyleSheet(f"color:{col}; font-size:11px; font-weight:700; padding:2px 0;")

        if hasattr(self, '_stats_labels'):
            closed = [t for t in self._trades if t["status"] != "OPEN"]
            win_pnls  = [t["pnl"] for t in closed if (t.get("pnl") or 0) >= 0]
            loss_pnls = [t["pnl"] for t in closed if (t.get("pnl") or 0) <  0]
            gross_win  = sum(win_pnls)
            gross_loss = abs(sum(loss_pnls))
            pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
            wr = wins / len(closed) * 100 if closed else 0

            def _sv(key, text, col=WHITE):
                lbl = self._stats_labels.get(key)
                if lbl:
                    lbl.setText(text)
                    lbl.setStyleSheet(f"color:{col}; font-size:11px; font-weight:700; border:none;")

            def _fmt_pnl(t):
                pnl = t.get("pnl") or 0
                pct = t.get("pnl_pct") or 0
                sign = "+" if pnl >= 0 else ""
                if pnl == 0 and pct != 0:
                    return f"{sign}{pct:.2f}%"
                return f"{sign}{pnl:.4f}"

            win_vals  = [t.get("pnl_pct") or 0 if (t.get("pnl") or 0) == 0
                         else (t.get("pnl") or 0) for t in closed if (t.get("pnl") or 0) >= 0]
            loss_vals = [t.get("pnl_pct") or 0 if (t.get("pnl") or 0) == 0
                         else (t.get("pnl") or 0) for t in closed if (t.get("pnl") or 0) < 0]
            avg_win_v  = sum(win_vals)  / len(win_vals)  if win_vals  else 0
            avg_loss_v = sum(loss_vals) / len(loss_vals) if loss_vals else 0
            best_t  = max(closed, key=lambda t: t.get("pnl") or 0) if closed else None
            worst_t = min(closed, key=lambda t: t.get("pnl") or 0) if closed else None

            _sv("total",    str(len(self._trades)))
            _sv("open",     str(len(open_trades)), ACCENT)
            _sv("wins",     str(wins),  GREEN if wins  else DIM)
            _sv("losses",   str(losses), RED if losses else DIM)
            _sv("winrate",  f"{wr:.1f}%", GREEN if wr >= 50 else RED)
            sign = "+" if avg_win_v >= 0 else ""
            _sv("avg_win",  f"{sign}{avg_win_v:.4f}",  GREEN if avg_win_v  > 0 else DIM)
            _sv("avg_loss", f"{avg_loss_v:.4f}",        RED   if avg_loss_v < 0 else DIM)
            _sv("best",     _fmt_pnl(best_t)  if best_t  else "—", GREEN)
            _sv("worst",    _fmt_pnl(worst_t) if worst_t else "—", RED)
            pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
            _sv("pf",       pf_str, GREEN if pf >= 1 else RED)
            tsign = "+" if total_pnl >= 0 else ""
            has_usdt = any((t.get("pnl") or 0) != 0 for t in closed)
            if has_usdt:
                _sv("total_pnl", f"{tsign}{total_pnl:.4f} USDT",
                    GREEN if total_pnl > 0 else RED if total_pnl < 0 else DIM)
            else:
                total_pct = sum(t.get("pnl_pct") or 0 for t in closed)
                _sv("total_pnl", f"{tsign}{total_pct:.2f}% (no qty set)",
                    GREEN if total_pct > 0 else RED if total_pct < 0 else DIM)

        if hasattr(self, '_equity_canvas'):
            closed_sorted = sorted(
                [t for t in self._trades if t["status"] != "OPEN" and t.get("pnl") is not None],
                key=lambda t: t.get("time",""))
            cum = 0.0
            cum_pts, labels = [], []
            for t in closed_sorted:
                cum += t["pnl"]
                cum_pts.append(round(cum, 6))
                labels.append(t["symbol"].replace("USDT",""))
            self._equity_canvas.set_data(cum_pts, labels)

    TRADES_FILE = os.path.join(APP_LOGS_DIR, "trades.json")
    TRADE_LOG   = os.path.join(APP_LOGS_DIR, "trade_log.txt")
    ALERTS_FILE = os.path.join(APP_LOGS_DIR, "alerts.json")

    def _save_trades(self):
        try:
            tmp = self.TRADES_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._trades, f, indent=2)
            os.replace(tmp, self.TRADES_FILE)
        except Exception as e:
            self._show_status(f"Trade save error: {e}")

    def _log_trade_event(self, event: str, trade: dict):
        try:
            ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            env = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
            sym = trade.get("symbol", "")
            sid = trade.get("side", "")
            line = (f"{ts}  [{env}]  {event:12s}  {sid:5s}  {sym:12s}  "
                    f"entry={trade.get('entry','')}  qty={trade.get('qty','')}  "
                    f"sl={trade.get('sl','')}  tp={trade.get('tp','')}  "
                    f"exit={trade.get('exit','')}  pnl={trade.get('pnl','')}  "
                    f"status={trade.get('status','')}  "
                    f"binance_order={trade.get('binance_order_id','')}  "
                    f"oco={trade.get('binance_oco_id','')}\n")
            with open(self.TRADE_LOG, "a") as f:
                f.write(line)
        except Exception:
            pass

    def _load_trades(self):
        try:
            if os.path.exists(self.TRADES_FILE):
                with open(self.TRADES_FILE, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._trades = [t for t in data if isinstance(t, dict)]
                else:
                    self._trades = []
            else:
                self._trades = []
        except Exception as e:
            print(f"[WARN] Could not load trades: {e} — starting fresh")
            self._trades = []

    def _save_alerts(self):
        try:
            os.makedirs(APP_LOGS_DIR, exist_ok=True)
            tmp = self.ALERTS_FILE + ".tmp"
            safe_alerts = []
            for a in self._alert_log[-50:]:
                safe = {}
                for k, v in a.items():
                    safe[k] = str(v) if not isinstance(v, (str, int, float, bool)) else v
                safe_alerts.append(safe)
            with open(tmp, "w") as f:
                json.dump(safe_alerts, f, indent=2)
            os.replace(tmp, self.ALERTS_FILE)
        except Exception as e:
            print(f"[WARN] Could not save alerts: {e}")

    def _load_alerts(self):
        try:
            if not os.path.exists(self.ALERTS_FILE):
                return
            with open(self.ALERTS_FILE, "r") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return
            for alert in data:
                if not isinstance(alert, dict):
                    continue
                for k in ("rsi", "exp", "pot", "vol", "price"):
                    if k in alert:
                        try:
                            alert[k] = float(alert[k])
                        except Exception:
                            pass
                self._alert_log.append(alert)
                self._add_alert_row(alert, flash=False)
                # Subscribe symbol to WS so live prices populate
                if self._ws_feed:
                    ws_sym = alert.get("symbol", "") + "USDT"
                    self._ws_feed.subscribe_alert(ws_sym)
            self._update_history_tab_badge()
        except Exception as e:
            print(f"[WARN] Could not load alerts: {e}")

    def _on_tab_changed(self, index):
        tab_text = self._tabs_widget.tabText(index)
        if tab_text.startswith("💰"):
            self._fetch_open_trade_prices()

    def _fetch_open_trade_prices(self):
        open_trades = [t for t in self._trades if t["status"] == "OPEN"]
        if not open_trades:
            return
        if getattr(self, '_trade_price_fetch_running', False):
            return
        if self._ws_feed:
            syms = {t["symbol"] for t in open_trades}
            self._ws_feed.subscribe(syms)

        open_syms = list({t["symbol"] for t in open_trades})
        self._trade_price_fetch_running = True

        def _worker():
            try:
                base = CFG["base_url"]
                for sym in open_syms:
                    try:
                        resp = requests.get(
                            f"{base}/api/v3/ticker/price",
                            params={"symbol": sym}, timeout=5)
                        d = resp.json()
                        if isinstance(d, dict) and "price" in d:
                            self._live_prices[sym] = float(d["price"])
                    except Exception:
                        pass
                for r in self._results:
                    if r["symbol"] in self._live_prices:
                        r["price"] = self._live_prices[r["symbol"]]
            except Exception:
                pass

        def _done():
            self._trade_price_fetch_running = False
            self._check_sltp_hits(self._results)
            self._refresh_trades_table()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        def _poll():
            if t.is_alive():
                QTimer.singleShot(100, _poll)
            else:
                _done()
        QTimer.singleShot(100, _poll)

    def _check_sltp_hits(self, results):
        try:
            self._check_sltp_hits_inner(results)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._show_status(f"⚠ SL/TP check error: {str(e)[:60]}")

    def _check_sltp_hits_inner(self, results):
        price_map = {r["symbol"]: r["price"] for r in results}
        price_map.update(getattr(self, "_live_prices", {}))
        hits = []

        for trade in self._trades:
            if trade["status"] != "OPEN":
                continue
            sym   = trade["symbol"]
            price = price_map.get(sym)
            if price is None:
                continue

            side     = trade["side"]
            sl       = trade.get("sl")
            tp       = trade.get("tp")
            hit_type = None

            if side == "LONG":
                if sl and price <= sl:   hit_type = "SL"
                elif tp and price >= tp: hit_type = "TP"
            else:
                if sl and price >= sl:   hit_type = "SL"
                elif tp and price <= tp: hit_type = "TP"

            if hit_type:
                hits.append((trade, hit_type, price))

        for trade, hit_type, price in hits:
            side        = trade["side"]
            entry       = trade["entry"]
            qty         = trade.get("qty", 0)
            symbol_full = trade["symbol"]
            sym_short   = symbol_full.replace("USDT", "")
            oco_id      = trade.get("binance_oco_id")
            order_id    = trade.get("binance_order_id")
            api_ready   = bool(TRADING_CFG["api_key"] and TRADING_CFG["api_secret"])
            exit_price  = price

            if hit_type == "TP" and api_ready and order_id is not None:
                if oco_id is not None:
                    _trader.cancel_oco(symbol_full, oco_id)
                _, s_info = _trader.get_symbol_info(symbol_full)
                sell_qty = _trader.round_step(qty, s_info.get("stepSize", 0.00000001))
                s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
                if not s_ok and "-2010" in str(s_data):
                    asset = symbol_full.replace("USDT", "")
                    _, actual_bal = _trader.get_asset_balance(asset)
                    if actual_bal > 0:
                        _, s_info = _trader.get_symbol_info(symbol_full)
                        sell_qty = _trader.round_step(actual_bal, s_info.get("stepSize", 1))
                        s_ok, s_data = _trader.place_market_sell(symbol_full, sell_qty)
                if s_ok:
                    fills    = s_data.get("fills", [])
                    exec_qty = float(s_data.get("executedQty", qty))
                    if fills and exec_qty > 0:
                        exit_price = sum(
                            float(f["price"]) * float(f["qty"]) for f in fills
                        ) / exec_qty
                    qty = exec_qty
                else:
                    err = s_data.get("error", str(s_data))[:80]
                    self.statusBar().showMessage(
                        f"⚠ TP SELL FAILED for {sym_short}: {err}")
                    continue

            if side == "LONG":
                pnl = (exit_price - entry) * qty
                pct = (exit_price - entry) / entry * 100
            else:
                pnl = (entry - exit_price) * qty
                pct = (entry - exit_price) / entry * 100

            trade["exit"]         = round(exit_price, 8)
            trade["pnl"]          = round(pnl, 8)
            trade["pnl_pct"]      = round(pct, 4)
            trade["status"]       = "WIN" if pnl >= 0 else "LOSS"
            trade["closed"]       = datetime.now().strftime("%m-%d %H:%M")
            trade["close_reason"] = hit_type
            self._log_trade_event(f"AUTO_{hit_type}", trade)

            sign = "+" if pnl >= 0 else ""
            msg  = (f"{'🎯' if hit_type=='TP' else '🛑'}  {hit_type} HIT  {side} {sym_short}  "
                    f"@ ${exit_price:.6f}  P&L: {sign}{pnl:.4f} USDT ({sign}{pct:.2f}%)")
            self.statusBar().showMessage(msg)

            try:
                urgency = "normal" if hit_type == "TP" else "critical"
                icon    = "dialog-information" if hit_type == "TP" else "dialog-warning"
                subprocess.Popen([
                    "notify-send", "-u", urgency, "-i", icon,
                    f"{hit_type} Hit — {side} {sym_short}",
                    f"Price: ${price:.6f}\nP&L: {sign}{pnl:.4f} USDT ({sign}{pct:.2f}%)"
                ])
            except Exception:
                pass

            try:
                wav = _SOUNDS.get("STRONG BUY" if hit_type == "TP" else "STRONG SELL")
                if wav and os.path.exists(wav):
                    players = ["ffplay -nodisp -autoexit", "aplay", "paplay", "pw-play"]
                    for pl in players:
                        parts = pl.split() + [wav]
                        try:
                            subprocess.Popen(parts, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                            break
                        except FileNotFoundError:
                            continue
            except Exception:
                pass

            try:
                if ALERT_CFG.get("telegram") and ALERT_CFG.get("tg_token") and ALERT_CFG.get("tg_chat_id"):
                    text = (f"{'🎯 TP HIT' if hit_type=='TP' else '🛑 SL HIT'}\n"
                            f"{side} {sym_short} closed @ ${price:.6f}\n"
                            f"P&L: {sign}{pnl:.4f} USDT ({sign}{pct:.2f}%)")
                    requests.post(
                        f"https://api.telegram.org/bot{ALERT_CFG['tg_token']}/sendMessage",
                        json={"chat_id": ALERT_CFG["tg_chat_id"], "text": text},
                        timeout=5)
            except Exception:
                pass

        if hits:
            self._save_trades()
            self._refresh_trades_table()

    def _export_trades_csv(self):
        import csv
        fname = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        fpath = os.path.join(APP_LOGS_DIR, fname)
        os.makedirs(APP_LOGS_DIR, exist_ok=True)
        try:
            with open(fpath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "id","time","symbol","side","entry","qty","sl","tp",
                    "exit","pnl","pnl_pct","status","closed","close_reason","note"])
                writer.writeheader()
                for t in self._trades:
                    writer.writerow({k: t.get(k,"") for k in writer.fieldnames})
            self._show_status(f"Trades exported → {fpath}")
        except Exception as e:
            self._show_status(f"CSV export error: {e}")

    def _build_alerts_tab(self):
        outer = QWidget()
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        self._alerts_sub_tabs = QTabWidget()
        self._alerts_sub_tabs.setDocumentMode(False)
        self._alerts_sub_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {BORDER};
                border-radius: 8px;
                background: {DARK};
                top: -1px;
            }}
            QTabBar {{
                alignment: left;
            }}
            QTabBar::tab {{
                font-family: {MONO_CSS}; font-size: 11px; font-weight: 700;
                padding: 7px 20px;
                color: {DIM};
                background: {CARD};
                border: 1px solid {BORDER};
                border-bottom: none;
                border-radius: 6px 6px 0 0;
                margin-right: 3px;
                min-width: 110px;
            }}
            QTabBar::tab:selected {{
                color: {ACCENT};
                background: {DARK};
                border-bottom: 1px solid {DARK};
            }}
            QTabBar::tab:hover:!selected {{
                color: {WHITE};
                background: {CARD};
            }}
        """)
        outer_lay.addWidget(self._alerts_sub_tabs)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        auto_grp = QGroupBox("AUTO-SCAN & TRIGGER")
        aglay = QGridLayout(auto_grp)
        aglay.setSpacing(6)
        aglay.setColumnMinimumWidth(0, 160)
        aglay.setColumnStretch(0, 0)
        aglay.setColumnStretch(1, 1)

        self.al_enabled = QCheckBox("Enable auto-scan alerts")
        self.al_enabled.setChecked(ALERT_CFG["enabled"])
        self.al_enabled.setStyleSheet(f"color:{WHITE};")

        self.al_interval = QSpinBox()
        self.al_interval.setFixedWidth(160)
        self.al_interval.setRange(30, 3600)
        self.al_interval.setValue(ALERT_CFG["interval_sec"])
        self.al_interval.setSuffix("s")

        self.al_min_signal = QComboBox()
        self.al_min_signal.setFixedWidth(160)
        for s in ["PRE-BREAKOUT", "BUY", "STRONG BUY"]:
            self.al_min_signal.addItem(s)
        self.al_min_signal.setCurrentText(ALERT_CFG["min_signal"])

        self.al_min_pot = QSpinBox()
        self.al_min_pot.setFixedWidth(160)
        self.al_min_pot.setRange(0, 100)
        self.al_min_pot.setValue(ALERT_CFG["min_potential"])
        self.al_min_pot.setSuffix("%")

        self.al_min_exp = QDoubleSpinBox()
        self.al_min_exp.setFixedWidth(160)
        self.al_min_exp.setRange(0, 50)
        self.al_min_exp.setValue(ALERT_CFG["min_exp_move"])
        self.al_min_exp.setSuffix("%")

        self.al_max_rsi = QSpinBox()
        self.al_max_rsi.setFixedWidth(160)
        self.al_max_rsi.setRange(1, 100)
        self.al_max_rsi.setValue(ALERT_CFG["max_rsi"])
        self.al_max_rsi.setToolTip("Only alert if RSI is below this value")

        self.al_max_bb = QSpinBox()
        self.al_max_bb.setFixedWidth(160)
        self.al_max_bb.setRange(0, 200)
        self.al_max_bb.setValue(ALERT_CFG["max_bb_pct"])
        self.al_max_bb.setSuffix("%")
        self.al_max_bb.setToolTip("Only alert if BB% is below this")

        self.al_vol_spike = QCheckBox("Require volume spike")
        self.al_vol_spike.setChecked(ALERT_CFG["require_vol_spike"])
        self.al_vol_spike.setStyleSheet(f"color:{WHITE};")
        self.al_vol_spike.setToolTip("Only alert if unusual volume detected")

        self.al_block_downtrend = QCheckBox("Block Downtrend pattern")
        self.al_block_downtrend.setChecked(ALERT_CFG.get("block_downtrend", True))
        self.al_block_downtrend.setStyleSheet(f"color:{WHITE};")
        self.al_block_downtrend.setToolTip("Skip alerts when candlestick pattern shows Downtrend ↓ or Rejection ↓")

        self.al_block_1h_downtrend = QCheckBox("Block BUY when 1H trend is bearish  ↓")
        self.al_block_1h_downtrend.setChecked(ALERT_CFG.get("block_1h_downtrend", True))
        self.al_block_1h_downtrend.setStyleSheet(f"color:{WHITE};")
        self.al_block_1h_downtrend.setToolTip(
            "Skip BUY and STRONG BUY alerts when the 1h EMA50 trend is bearish.\n"
            "Prevents scalping long into a confirmed higher-timeframe downtrend.\n"
            "The 1H column in the scanner shows ↓ when this would block an alert.")

        self.al_min_vol_ratio = QDoubleSpinBox()
        self.al_min_vol_ratio.setFixedWidth(160)
        self.al_min_vol_ratio.setRange(0, 5)
        self.al_min_vol_ratio.setDecimals(1)
        self.al_min_vol_ratio.setValue(ALERT_CFG.get("min_vol_ratio", 0.8))
        self.al_min_vol_ratio.setSuffix("")
        self.al_min_vol_ratio.setToolTip(
            "Minimum volume ratio vs average\n"
            "ROBO alerts had 0.3x-0.7x — dying volume after dump\n"
            "0.8x = only alert if volume is near normal or above")

        self.al_spike_cooldown = QCheckBox("Post-spike cooldown  >")
        self.al_spike_cooldown.setChecked(ALERT_CFG.get("spike_cooldown", True))
        self.al_spike_cooldown.setStyleSheet(f"color:{WHITE};")
        self.al_spike_cooldown_pct = QDoubleSpinBox()
        self.al_spike_cooldown_pct.setFixedWidth(100)
        self.al_spike_cooldown_pct.setRange(5, 50)
        self.al_spike_cooldown_pct.setDecimals(0)
        self.al_spike_cooldown_pct.setValue(ALERT_CFG.get("spike_pct", 15.0))
        self.al_spike_cooldown_pct.setSuffix("%")
        self.al_spike_cooldown_pct.setEnabled(ALERT_CFG.get("spike_cooldown", True))
        self.al_spike_cooldown.toggled.connect(self.al_spike_cooldown_pct.setEnabled)
        self.al_spike_cooldown.setToolTip(
            "If coin spiked more than this % in last 3h → block alerts for 2 hours\n"
            "Prevents chasing dump-after-pump signals like ROBO")

        self.al_crash_cooldown = QCheckBox("Crash candle cooldown  >")
        self.al_crash_cooldown.setChecked(ALERT_CFG.get("crash_cooldown", True))
        self.al_crash_cooldown.setStyleSheet(f"color:{WHITE};")
        self.al_crash_cooldown_pct = QDoubleSpinBox()
        self.al_crash_cooldown_pct.setFixedWidth(100)
        self.al_crash_cooldown_pct.setRange(3, 30)
        self.al_crash_cooldown_pct.setDecimals(0)
        self.al_crash_cooldown_pct.setValue(ALERT_CFG.get("crash_pct", 8.0))
        self.al_crash_cooldown_pct.setSuffix("%")
        self.al_crash_cooldown_pct.setEnabled(ALERT_CFG.get("crash_cooldown", True))
        self.al_crash_cooldown.toggled.connect(self.al_crash_cooldown_pct.setEnabled)
        self.al_crash_cooldown_mins = QSpinBox()
        self.al_crash_cooldown_mins.setFixedWidth(80)
        self.al_crash_cooldown_mins.setRange(10, 240)
        self.al_crash_cooldown_mins.setValue(ALERT_CFG.get("crash_cooldown_mins", 60))
        self.al_crash_cooldown_mins.setSuffix(" min")
        self.al_crash_cooldown_mins.setEnabled(ALERT_CFG.get("crash_cooldown", True))
        self.al_crash_cooldown.toggled.connect(self.al_crash_cooldown_mins.setEnabled)
        self.al_crash_cooldown.setToolTip(
            "If any of the last 3 candles dropped more than this %,\n"
            "block BUY alerts on that coin for the cooldown period.\n"
            "Catches post-dump bounce alerts like PHA 11:08.")

        self.al_block_doji = QCheckBox("Block Doji pattern  (indecision candle)")
        self.al_block_doji.setChecked(ALERT_CFG.get("block_doji", True))
        self.al_block_doji.setStyleSheet(f"color:{WHITE};")
        self.al_block_doji.setToolTip(
            "Skip alerts when the last candle is a Doji.\n"
            "Doji = open ≈ close = no directional conviction.\n"
            "49% of noise alerts in log analysis had Doji pattern.")

        self.al_block_neutral_pat = QCheckBox("Block Neutral pattern  (no conviction)")
        self.al_block_neutral_pat.setChecked(ALERT_CFG.get("block_neutral_pattern", True))
        self.al_block_neutral_pat.setStyleSheet(f"color:{WHITE};")
        self.al_block_neutral_pat.setToolTip(
            "Skip alerts when pattern is Neutral.\n"
            "31% of noise alerts had Neutral pattern.\n"
            "Real setups show Hammer, Engulf, Vol Spike, Squeeze, Uptrend.")

        self.al_squeeze_exempt_width = QDoubleSpinBox()
        self.al_squeeze_exempt_width.setFixedWidth(160)
        self.al_squeeze_exempt_width.setRange(0.5, 5.0)
        self.al_squeeze_exempt_width.setDecimals(1)
        self.al_squeeze_exempt_width.setSingleStep(0.5)
        self.al_squeeze_exempt_width.setValue(ALERT_CFG.get("squeeze_exempt_bb_width", 2.0))
        self.al_squeeze_exempt_width.setSuffix("")
        self.al_squeeze_exempt_width.setToolTip(
            "Only exempt exp_move filter when BB width is tighter than this.\n"
            "Lower = stricter (fewer exemptions). Default 2.0%.\n"
            "3.0% was too wide — exemption became the main alert path.")

        self.al_require_macd = QCheckBox("Require MACD rising")
        self.al_coin_cooldown = QCheckBox("Per-coin cooldown")
        self.al_coin_cooldown.setChecked(ALERT_CFG.get("coin_cooldown", True))
        self.al_coin_cooldown.setStyleSheet(f"color:{WHITE};")
        self.al_coin_cooldown_mins = QSpinBox()
        self.al_coin_cooldown_mins.setFixedWidth(100)
        self.al_coin_cooldown_mins.setRange(5, 240)
        self.al_coin_cooldown_mins.setValue(ALERT_CFG.get("coin_cooldown_mins", 30))
        self.al_coin_cooldown_mins.setSuffix(" min")
        self.al_coin_cooldown_mins.setEnabled(ALERT_CFG.get("coin_cooldown", True))
        self.al_coin_cooldown.toggled.connect(self.al_coin_cooldown_mins.setEnabled)
        self.al_coin_cooldown.setToolTip(
            "Once a coin alerts, block it for this many minutes\n"
            "Prevents ROBO-style spam (82 alerts from 1 coin today)\n"
            "30 min = each coin can alert at most twice per hour")
        self.al_require_macd.setChecked(ALERT_CFG.get("require_macd_rising", False))
        self.al_require_macd.setStyleSheet(f"color:{WHITE};")
        self.al_require_macd.setToolTip(
            "Only alert if MACD histogram is rising (bullish momentum building)\n"
            "Stricter — eliminates signals with fading momentum")

        self.al_min_adr = QDoubleSpinBox()
        self.al_min_adr.setFixedWidth(160)
        self.al_min_adr.setRange(0, 20)
        self.al_min_adr.setDecimals(1)
        self.al_min_adr.setValue(ALERT_CFG.get("min_adr_pct", 0.5))
        self.al_min_adr.setSuffix("%")
        self.al_min_adr.setToolTip(
            "Skip coins with avg candle range below this\n"
            "NIGHT ~1% → set 2%+ to exclude flat coins\n"
            "Higher = only coins with real price movement")

        rows = [
            ("Scan interval",   self.al_interval),
            ("Minimum signal",  self.al_min_signal),
            ("Min Potential %", self.al_min_pot),
            ("Min Exp Move %",  self.al_min_exp),
            ("Max RSI",         self.al_max_rsi),
            ("Max BB%",         self.al_max_bb),
            ("Min ADR %",       self.al_min_adr),
            ("Min Vol Ratio",   self.al_min_vol_ratio),
        ]
        aglay.addWidget(self.al_enabled, 0, 0, 1, 2)
        for i, (lbl_text, widget) in enumerate(rows, 1):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(f"color:{DIM};")
            aglay.addWidget(lbl, i, 0)
            aglay.addWidget(widget, i, 1, Qt.AlignmentFlag.AlignLeft)
        row_offset = len(rows) + 1
        aglay.addWidget(self.al_vol_spike,          row_offset,     0, 1, 2)
        aglay.addWidget(self.al_block_downtrend,    row_offset + 1, 0, 1, 2)
        aglay.addWidget(self.al_block_1h_downtrend, row_offset + 2, 0, 1, 2)
        aglay.addWidget(self.al_require_macd,       row_offset + 3, 0, 1, 2)
        aglay.addWidget(self.al_block_doji,          row_offset + 4, 0, 1, 2)
        aglay.addWidget(self.al_block_neutral_pat,   row_offset + 5, 0, 1, 2)

        squeeze_row_lbl = QLabel("BB squeeze exempt width")
        squeeze_row_lbl.setStyleSheet(f"color:{DIM};")
        aglay.addWidget(squeeze_row_lbl,             row_offset + 6, 0)
        aglay.addWidget(self.al_squeeze_exempt_width, row_offset + 6, 1, Qt.AlignmentFlag.AlignLeft)

        spike_row = QHBoxLayout()
        spike_row.addWidget(self.al_spike_cooldown)
        spike_row.addWidget(self.al_spike_cooldown_pct)
        spike_row.addStretch()
        spike_w = QWidget(); spike_w.setLayout(spike_row)
        aglay.addWidget(spike_w,                    row_offset + 7, 0, 1, 2)
        cooldown_row = QHBoxLayout()
        cooldown_row.addWidget(self.al_coin_cooldown)
        cooldown_row.addWidget(self.al_coin_cooldown_mins)
        cooldown_row.addStretch()
        cooldown_w = QWidget(); cooldown_w.setLayout(cooldown_row)
        aglay.addWidget(cooldown_w,                 row_offset + 8, 0, 1, 2)
        crash_row = QHBoxLayout()
        crash_row.addWidget(self.al_crash_cooldown)
        crash_row.addWidget(self.al_crash_cooldown_pct)
        crash_row.addWidget(self.al_crash_cooldown_mins)
        crash_row.addStretch()
        crash_w = QWidget(); crash_w.setLayout(crash_row)
        aglay.addWidget(crash_w,                    row_offset + 9, 0, 1, 2)

        # ── Volume Surge Detector settings ────────────────────────────────
        surge_grp = QGroupBox("VOLUME SURGE DETECTOR")
        sglay = QGridLayout(surge_grp)
        sglay.setSpacing(8)

        self.sg_enabled = QCheckBox("Enable surge detection")
        self.sg_enabled.setChecked(SURGE_CFG.get("enabled", True))
        self.sg_enabled.setToolTip("Detects sudden 5m volume spikes on any sub-$1 coin")

        self.sg_vol_5m = QDoubleSpinBox()
        self.sg_vol_5m.setRange(1.5, 20.0); self.sg_vol_5m.setDecimals(1)
        self.sg_vol_5m.setSingleStep(0.5); self.sg_vol_5m.setFixedWidth(120)
        self.sg_vol_5m.setValue(SURGE_CFG.get("vol_5m_mult", 3.0))
        self.sg_vol_5m.setToolTip("Last 5m candle volume must be Nx average — primary trigger")

        self.sg_max_chg = QDoubleSpinBox()
        self.sg_max_chg.setRange(5.0, 100.0); self.sg_max_chg.setDecimals(0)
        self.sg_max_chg.setSingleStep(5); self.sg_max_chg.setFixedWidth(120)
        self.sg_max_chg.setValue(SURGE_CFG.get("max_price_pct", 30.0))
        self.sg_max_chg.setSuffix("%")
        self.sg_max_chg.setToolTip("Skip coins already up more than this % (catch early, not tops)")

        self.sg_min_chg = QDoubleSpinBox()
        self.sg_min_chg.setRange(0.1, 5.0); self.sg_min_chg.setDecimals(1)
        self.sg_min_chg.setSingleStep(0.1); self.sg_min_chg.setFixedWidth(120)
        self.sg_min_chg.setValue(SURGE_CFG.get("min_price_pct", 0.5))
        self.sg_min_chg.setSuffix("%")
        self.sg_min_chg.setToolTip("Minimum price move to count as a real surge")

        self.sg_min_vol = QDoubleSpinBox()
        self.sg_min_vol.setRange(100_000, 10_000_000); self.sg_min_vol.setDecimals(0)
        self.sg_min_vol.setSingleStep(100_000); self.sg_min_vol.setFixedWidth(120)
        self.sg_min_vol.setPrefix("$"); self.sg_min_vol.setValue(SURGE_CFG.get("min_vol_usdt", 500_000))
        self.sg_min_vol.setToolTip("Minimum 24h USDT volume for a coin to be considered")

        self.sg_interval = QSpinBox()
        self.sg_interval.setRange(10, 120); self.sg_interval.setSingleStep(5)
        self.sg_interval.setFixedWidth(120); self.sg_interval.setSuffix(" s")
        self.sg_interval.setValue(SURGE_CFG.get("interval_sec", 30))
        self.sg_interval.setToolTip("How often the surge detector checks all coins (seconds)")

        self.sg_max_cand = QSpinBox()
        self.sg_max_cand.setRange(1, 30); self.sg_max_cand.setFixedWidth(120)
        self.sg_max_cand.setValue(SURGE_CFG.get("max_candidates", 10))
        self.sg_max_cand.setToolTip("Max coins to fetch klines for per tick (higher = more thorough, more API calls)")

        self.sg_cooldown = QSpinBox()
        self.sg_cooldown.setRange(1, 240); self.sg_cooldown.setFixedWidth(120)
        self.sg_cooldown.setSuffix(" min")
        self.sg_cooldown.setValue(SURGE_CFG.get("cooldown_mins", 60))
        self.sg_cooldown.setToolTip("Per-coin cooldown between surge alerts — prevents duplicate firing")

        sglay.addWidget(self.sg_enabled, 0, 0, 1, 2)
        for i, (lbl_text, widget) in enumerate([
            ("5m vol mult",    self.sg_vol_5m),
            ("Max price chg",  self.sg_max_chg),
            ("Min price chg",  self.sg_min_chg),
            ("Min 24h vol",    self.sg_min_vol),
            ("Check interval", self.sg_interval),
            ("Max candidates", self.sg_max_cand),
            ("Coin cooldown",  self.sg_cooldown),
        ], 1):
            lbl = QLabel(lbl_text); lbl.setStyleSheet(f"color:{DIM};")
            sglay.addWidget(lbl, i, 0)
            sglay.addWidget(widget, i, 1, Qt.AlignmentFlag.AlignLeft)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(auto_grp, 1)

        ch_grp = QGroupBox("NOTIFICATION CHANNELS")
        chlay = QVBoxLayout(ch_grp)
        chlay.setSpacing(4)
        chlay.setContentsMargins(12, 8, 12, 8)

        self.al_sound   = QCheckBox("🔊  Sound alert  (via ffplay — ascending beep = long, descending = short)")
        self.al_desktop = QCheckBox("🖥  Desktop notification  (via notify-send)")
        self.al_tg      = QCheckBox("✈️  Telegram bot message")
        for cb in (self.al_sound, self.al_desktop, self.al_tg):
            cb.setStyleSheet(f"color:{WHITE};")
        self.al_sound.setChecked(ALERT_CFG["sound"])
        self.al_desktop.setChecked(ALERT_CFG["desktop"])
        self.al_tg.setChecked(ALERT_CFG["telegram"])

        tg_frame = QFrame()
        tg_frame.setStyleSheet(f"background:{CARD}; border-radius:4px; padding:4px;")
        tglay = QGridLayout(tg_frame)
        tglay.setSpacing(8)
        tg_note = QLabel(
            "Get your token from @BotFather on Telegram.\n"
            "Get your chat ID by messaging @userinfobot.\n"
            "Format: 123456789  (just the number)")
        tg_note.setStyleSheet(f"color:{DIM}; font-size:11px;")
        tg_note.setWordWrap(True)

        self.al_tg_token   = QLineEdit(); self.al_tg_token.setPlaceholderText("Bot token  e.g. 123456789:ABCdef...")
        self.al_tg_chat    = QLineEdit(); self.al_tg_chat.setPlaceholderText("Chat ID  e.g. 123456789")
        self.al_tg_token.setText(ALERT_CFG["tg_token"])
        self.al_tg_chat.setText(ALERT_CFG["tg_chat_id"])
        for f in (self.al_tg_token, self.al_tg_chat):
            f.setStyleSheet(f"background:{DARK2}; color:{WHITE}; border:1px solid {BORDER}; padding:4px; border-radius:3px;")

        tglay.addWidget(tg_note,             0, 0, 1, 2)
        tglay.addWidget(QLabel("Token:"),    1, 0)
        tglay.addWidget(self.al_tg_token,    1, 1)
        tglay.addWidget(QLabel("Chat ID:"),  2, 0)
        tglay.addWidget(self.al_tg_chat,     2, 1)
        for i in (1, 2):
            tglay.itemAtPosition(i, 0).widget().setStyleSheet(f"color:{DIM};")
        tg_frame.setVisible(ALERT_CFG["telegram"])
        self.al_tg.toggled.connect(tg_frame.setVisible)

        chlay.addWidget(self.al_sound)
        chlay.addWidget(self.al_desktop)
        chlay.addWidget(self.al_tg)
        chlay.addWidget(tg_frame)

        self.al_wa = QCheckBox("📱  WhatsApp  (via PicoClaw — scan QR once, alerts forever)")
        self.al_wa.setStyleSheet(f"color:{WHITE};")
        self.al_wa.setChecked(ALERT_CFG["whatsapp"])

        wa_frame = QFrame()
        wa_frame.setStyleSheet(f"background:{CARD}; border-radius:4px; padding:4px;")
        walay = QGridLayout(wa_frame)
        walay.setSpacing(8)

        wa_note = QLabel(
            "PicoClaw is a lightweight AI agent that links to your WhatsApp\n"
            "as a Linked Device — just like WhatsApp Web.\n\n"
            "Setup (one-time, 3 minutes):\n"
            "  1. yay -S picoclaw-bin   (or download from github.com/sipeed/picoclaw)\n"
            "  2. picoclaw onboard\n"
            "  3. Add WhatsApp to ~/.picoclaw/config.json  (see button below)\n"
            "  4. picoclaw gateway   → scan QR code in WhatsApp → Linked Devices\n"
            "  5. Leave gateway running. Done — alerts arrive instantly.\n\n"
            "Your phone number below (recipient for alerts):\n"
            "Format: country code + number, no + or spaces\n"
            "Example Pakistan: 923001234567  |  Example US: 12125551234")
        wa_note.setStyleSheet(f"color:{DIM}; font-size:11px;")
        wa_note.setWordWrap(True)

        self.al_wa_number = QLineEdit()
        self.al_wa_number.setPlaceholderText("e.g.  923001234567")
        self.al_wa_number.setText(ALERT_CFG["wa_number"])
        self.al_wa_number.setStyleSheet(
            f"background:{DARK2}; color:{WHITE}; border:1px solid {BORDER}; "
            f"padding:4px; border-radius:3px;")

        self.al_wa_queue = QLineEdit()
        self.al_wa_queue.setText(ALERT_CFG["picoclaw_queue"])
        self.al_wa_queue.setStyleSheet(
            f"background:{DARK2}; color:{DIM}; border:1px solid {BORDER}; "
            f"padding:4px; border-radius:3px; font-size:10px;")

        gen_cfg_btn = QPushButton("📋  Copy PicoClaw config snippet")
        gen_cfg_btn.clicked.connect(self._copy_picoclaw_config)
        gen_hb_btn  = QPushButton("📋  Copy HEARTBEAT.md task")
        gen_hb_btn.clicked.connect(self._copy_picoclaw_heartbeat)

        walay.addWidget(wa_note,                          0, 0, 1, 2)
        walay.addWidget(QLabel("Your number:"),           1, 0)
        walay.addWidget(self.al_wa_number,                1, 1)
        walay.addWidget(QLabel("Queue file:"),            2, 0)
        walay.addWidget(self.al_wa_queue,                 2, 1)
        walay.addWidget(gen_cfg_btn,                      3, 0)
        walay.addWidget(gen_hb_btn,                       3, 1)
        for i in (1, 2):
            walay.itemAtPosition(i, 0).widget().setStyleSheet(f"color:{DIM};")

        wa_frame.setVisible(ALERT_CFG["whatsapp"])
        self.al_wa.toggled.connect(wa_frame.setVisible)

        chlay.addWidget(self.al_wa)
        chlay.addWidget(wa_frame)

        top_row.addWidget(ch_grp, 1)
        lay.addLayout(top_row)
        lay.addWidget(surge_grp)

        btn_row2 = QHBoxLayout()
        apply_btn = QPushButton("✓  Apply Alert Settings")
        apply_btn.clicked.connect(self._apply_alert_config)
        test_btn = QPushButton("🔔  Test Alerts Now")
        test_btn.clicked.connect(self._test_alert)
        btn_row2.addWidget(apply_btn)
        btn_row2.addWidget(test_btn)
        lay.addLayout(btn_row2)

        lay.addStretch()
        settings_scroll.setWidget(w)

        history_w = QWidget()
        history_lay = QVBoxLayout(history_w)
        history_lay.setContentsMargins(16, 12, 16, 12)
        history_lay.setSpacing(8)

        hist_header = QHBoxLayout()
        hist_title = QLabel("ALERT HISTORY")
        hist_title.setStyleSheet(f"color:{ACCENT}; font-weight:800; font-size:13px; font-family:{MONO_CSS};")
        hist_header.addWidget(hist_title)
        hist_header.addStretch()
        hist_clear2 = QPushButton("🗑  Clear Log")
        hist_clear2.setFixedHeight(28)
        hist_clear2.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-size:11px; padding:0 10px;")
        hist_clear2.clicked.connect(self._clear_alert_log)
        hist_header.addWidget(hist_clear2)
        history_lay.addLayout(hist_header)

        self.al_history_stats = QLabel("No alerts yet — waiting for signals")
        self.al_history_stats.setStyleSheet(f"color:{DIM}; font-size:11px;")
        history_lay.addWidget(self.al_history_stats)

        # ── P&L summary bar ──────────────────────────────────────────
        pnl_bar = QHBoxLayout()
        pnl_bar.setSpacing(18)
        _pl = QLabel("Total Profit:")
        _pl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self._al_profit_lbl = QLabel("—")
        self._al_profit_lbl.setStyleSheet(f"color:{GREEN}; font-size:12px; font-weight:700; font-family:{MONO_CSS};")
        _ll = QLabel("Total Loss:")
        _ll.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self._al_loss_lbl = QLabel("—")
        self._al_loss_lbl.setStyleSheet(f"color:{RED}; font-size:12px; font-weight:700; font-family:{MONO_CSS};")
        _nl = QLabel("Net:")
        _nl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self._al_net_lbl = QLabel("—")
        self._al_net_lbl.setStyleSheet(f"font-size:12px; font-weight:700; font-family:{MONO_CSS};")
        for w in (_pl, self._al_profit_lbl, _ll, self._al_loss_lbl, _nl, self._al_net_lbl):
            pnl_bar.addWidget(w)
        pnl_bar.addStretch()
        history_lay.addLayout(pnl_bar)

        self.alert_log_table = QTableWidget(0, 6)
        self.alert_log_table.setHorizontalHeaderLabels(
            ["TIME", "SYMBOL", "SIGNAL", "DETAILS", "ENTRY", "LIVE P&L"])
        self.alert_log_table.verticalHeader().setVisible(False)
        self.alert_log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alert_log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alert_log_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.alert_log_table.setAlternatingRowColors(False)
        self.alert_log_table.setSortingEnabled(False)
        self.alert_log_table.setShowGrid(True)
        self.alert_log_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.alert_log_table.customContextMenuRequested.connect(self._alerts_context_menu)
        al_hdr = self.alert_log_table.horizontalHeader()
        al_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        al_hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        al_hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        history_lay.addWidget(self.alert_log_table)

        self.alert_log_widget2 = QWidget()
        self.alert_log_layout2 = QVBoxLayout(self.alert_log_widget2)
        self.alert_log_widget = self.alert_log_widget2
        self.alert_log_layout = self.alert_log_layout2

        self._alerts_sub_tabs.addTab(history_w, "📋  Alerts  (0)")
        self._alerts_sub_tabs.addTab(settings_scroll, "⚙  Settings")

        return outer

    def _apply_alert_config(self):
        ALERT_CFG["enabled"]          = self.al_enabled.isChecked()
        self._refresh_alert_toggle()
        ALERT_CFG["interval_sec"]     = self.al_interval.value()
        ALERT_CFG["min_signal"]       = self.al_min_signal.currentText()
        ALERT_CFG["min_potential"]    = self.al_min_pot.value()
        ALERT_CFG["min_exp_move"]     = self.al_min_exp.value()
        ALERT_CFG["max_rsi"]          = self.al_max_rsi.value()
        ALERT_CFG["max_bb_pct"]       = self.al_max_bb.value()
        ALERT_CFG["require_vol_spike"]    = self.al_vol_spike.isChecked()
        ALERT_CFG["min_adr_pct"]          = self.al_min_adr.value()
        ALERT_CFG["block_downtrend"]      = self.al_block_downtrend.isChecked()
        ALERT_CFG["block_1h_downtrend"]   = self.al_block_1h_downtrend.isChecked()
        ALERT_CFG["min_vol_ratio"]        = self.al_min_vol_ratio.value()
        ALERT_CFG["spike_cooldown"]       = self.al_spike_cooldown.isChecked()
        ALERT_CFG["spike_pct"]            = self.al_spike_cooldown_pct.value()
        ALERT_CFG["crash_cooldown"]       = self.al_crash_cooldown.isChecked()
        ALERT_CFG["crash_pct"]            = self.al_crash_cooldown_pct.value()
        ALERT_CFG["crash_cooldown_mins"]  = self.al_crash_cooldown_mins.value()
        ALERT_CFG["require_macd_rising"]  = self.al_require_macd.isChecked()
        ALERT_CFG["block_doji"]           = self.al_block_doji.isChecked()
        ALERT_CFG["block_neutral_pattern"]= self.al_block_neutral_pat.isChecked()
        ALERT_CFG["squeeze_exempt_bb_width"] = self.al_squeeze_exempt_width.value()
        ALERT_CFG["coin_cooldown"]        = self.al_coin_cooldown.isChecked()
        ALERT_CFG["coin_cooldown_mins"]   = self.al_coin_cooldown_mins.value()
        ALERT_CFG["sound"]            = self.al_sound.isChecked()
        ALERT_CFG["desktop"]          = self.al_desktop.isChecked()
        ALERT_CFG["telegram"]         = self.al_tg.isChecked()
        ALERT_CFG["tg_token"]         = self.al_tg_token.text().strip()
        ALERT_CFG["tg_chat_id"]       = self.al_tg_chat.text().strip()
        ALERT_CFG["whatsapp"]         = self.al_wa.isChecked()
        ALERT_CFG["wa_number"]        = self.al_wa_number.text().strip()
        ALERT_CFG["picoclaw_queue"]   = self.al_wa_queue.text().strip()
        # Surge detector config
        SURGE_CFG["enabled"]       = self.sg_enabled.isChecked()
        SURGE_CFG["vol_5m_mult"]   = self.sg_vol_5m.value()
        SURGE_CFG["max_price_pct"] = self.sg_max_chg.value()
        SURGE_CFG["min_price_pct"] = self.sg_min_chg.value()
        SURGE_CFG["min_vol_usdt"]  = self.sg_min_vol.value()
        SURGE_CFG["interval_sec"]  = self.sg_interval.value()
        SURGE_CFG["max_candidates"]= self.sg_max_cand.value()
        SURGE_CFG["cooldown_mins"] = self.sg_cooldown.value()
        self.statusBar().showMessage(
            f"Alert settings applied — auto-scan every {ALERT_CFG['interval_sec']}s")
        s = self._settings
        for k, v in ALERT_CFG.items():
            s.setValue(f"alert_{k}", v)
        for k, v in SURGE_CFG.items():
            s.setValue(f"surge_{k}", v)

    def _test_alert(self):
        fake = {
            "time":        datetime.now().strftime("%H:%M:%S"),
            "symbol":      "TEST",
            "signal":      "STRONG BUY",
            "price":       0.04567,
            "rsi":         27.3,
            "exp":         8.4,
            "pot":         81,
            "pattern":     "Hammer ↑",
            "vol":         2.4,
            "macd_rising": True,
        }
        self._alert_engine._fire(fake)
        self._on_new_alert(fake)
        self._show_status("Test alert fired — check sound / desktop / Telegram / WhatsApp queue")

    def _copy_picoclaw_config(self):
        queue = ALERT_CFG["picoclaw_queue"].replace("\\", "/")
        snippet = f'''\
Add this to ~/.picoclaw/config.json inside the "channels" object:

  "whatsapp": {{
    "enabled": true,
    "use_native": true,
    "allow_from": ["{ALERT_CFG["wa_number"] or "YOUR_NUMBER_HERE"}"]
  }}

Full minimal config.json example:
{{
  "agents": {{
    "defaults": {{
      "model": "anthropic/claude-sonnet-4-6",
      "workspace": "~/.picoclaw/workspace"
    }}
  }},
  "providers": {{
    "anthropic": {{
      "api_key": "YOUR_ANTHROPIC_API_KEY"
    }}
  }},
  "channels": {{
    "whatsapp": {{
      "enabled": true,
      "use_native": true,
      "allow_from": ["{ALERT_CFG["wa_number"] or "923001234567"}"]
    }}
  }},
  "heartbeat": {{
    "enabled": true,
    "interval": 1
  }}
}}

Then run:  picoclaw gateway
Scan QR in WhatsApp → Settings → Linked Devices → Link a Device
'''
        QApplication.clipboard().setText(snippet)
        self.statusBar().showMessage("PicoClaw config snippet copied to clipboard!")

    def _copy_picoclaw_heartbeat(self):
        queue = ALERT_CFG["picoclaw_queue"].replace("\\", "/")
        number = ALERT_CFG["wa_number"] or "923001234567"
        heartbeat = f'''\
# Crypto Scanner Alert Delivery

## Send pending crypto alerts via WhatsApp

Read the file `{queue}` as JSON.
Find all entries where "sent" is false.
For each unsent entry:
  - Send a WhatsApp message to "{number}@s.whatsapp.net" with the text from the "text" field
  - Mark that entry's "sent" field as true
Write the updated JSON back to `{queue}`.
If the file does not exist or is empty, do nothing and respond HEARTBEAT_OK.
'''
        QApplication.clipboard().setText(heartbeat)
        self.statusBar().showMessage("HEARTBEAT.md task copied to clipboard — paste into ~/.picoclaw/workspace/HEARTBEAT.md")

    def _update_history_tab_badge(self):
        count = self.alert_log_table.rowCount() if hasattr(self, 'alert_log_table') else 0
        if hasattr(self, '_alerts_sub_tabs'):
            self._alerts_sub_tabs.setTabText(0, f"📋  Alerts  ({count})")
        if hasattr(self, 'al_history_stats'):
            if count > 0:
                self.al_history_stats.setText(f"{count} alert{'s' if count != 1 else ''} — latest at top")
            else:
                self.al_history_stats.setText("No alerts yet — waiting for signals")

    def _add_alert_row(self, alert, flash=True):
        if not hasattr(self, 'alert_log_table'):
            return
        sig    = alert.get("signal", "")
        is_surge = alert.get("surge", False)
        col    = GREEN if "BUY" in sig else (RED if "SELL" in sig else "#ff9900")
        if is_surge:
            col = "#ff9500"

        try:
            if is_surge:
                detail_text = (
                    f"24h vol {alert.get('vol_24h_x',0)}x  ·  "
                    f"5m vol {float(alert.get('vol_5m_x',0)):.1f}x  ·  "
                    f"Price +{float(alert.get('chg_pct',0)):.1f}%  ·  "
                    f"RSI {float(alert.get('rsi',0)):.0f}")
            else:
                detail_text = (
                    f"RSI {float(alert.get('rsi',0)):.0f}  ·  "
                    f"Exp {float(alert.get('exp',0)):.1f}%  ·  "
                    f"Pot {alert.get('pot',0)}%  ·  "
                    f"Vol {float(alert.get('vol',0)):.1f}x  ·  "
                    f"{alert.get('pattern','')}")
        except Exception:
            detail_text = str(alert.get("pattern", ""))
        try:
            price_text = f"${float(alert.get('price', 0)):.5f}"
        except Exception:
            price_text = str(alert.get("price", ""))

        tbl = self.alert_log_table
        tbl.insertRow(0)

        def cell(text, color=None, bold=False, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
            item = QTableWidgetItem(str(text))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if color:
                item.setForeground(QColor(color))
            if bold:
                f = item.font(); f.setBold(True); item.setFont(f)
            item.setTextAlignment(align)
            return item

        tbl.setItem(0, 0, cell(alert.get("time", ""), DIM))
        time_item = tbl.item(0, 0)
        entry_price = alert.get("price", 0)
        time_item.setData(Qt.ItemDataRole.UserRole, {
            "symbol":      alert.get("symbol", ""),
            "signal":      sig,
            "price":       entry_price,
            "entry_price": entry_price,
        })
        tbl.setItem(0, 1, cell(alert.get("symbol", "").replace("USDT",""), ACCENT, bold=True))
        tbl.setItem(0, 2, cell(sig, col, bold=True))
        tbl.setItem(0, 3, cell(detail_text, DIM))
        tbl.setItem(0, 4, cell(price_text, WHITE,
                               align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
        # LIVE P&L — populate immediately if we already have a price
        sym_ws = alert.get("symbol", "").replace("USDT", "") + "USDT"
        cur_price = self._live_prices.get(sym_ws)
        pnl_item = self._make_pnl_item(entry_price, cur_price)
        tbl.setItem(0, 5, pnl_item)

        if is_surge:
            surge_bg = QColor("#2a1f00")
            for _c in range(tbl.columnCount()):
                _item = tbl.item(0, _c)
                if _item:
                    _item.setBackground(surge_bg)

        while tbl.rowCount() > 20:
            tbl.removeRow(tbl.rowCount() - 1)

        if flash and hasattr(self, '_alerts_sub_tabs'):
            pass   # don't switch tabs — user may be on Settings

        self._refresh_alert_pnl_summary()

    def _make_pnl_item(self, entry_price, cur_price):
        """Create a QTableWidgetItem showing live P&L vs entry price."""
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        try:
            ep = float(entry_price)
            cp = float(cur_price) if cur_price is not None else None
            if cp is None or ep == 0:
                item.setText("—")
                item.setForeground(QColor(DIM))
            else:
                pnl_pct = (cp - ep) / ep * 100
                price_str = f"${cp:.5f}" if cp < 1 else f"${cp:.4f}"
                sign = "+" if pnl_pct >= 0 else ""
                item.setText(f"{price_str}  {sign}{pnl_pct:.2f}%")
                if pnl_pct > 0:
                    item.setForeground(QColor(GREEN))
                    f = item.font(); f.setBold(True); item.setFont(f)
                elif pnl_pct < 0:
                    item.setForeground(QColor(RED))
                    f = item.font(); f.setBold(True); item.setFont(f)
                else:
                    item.setForeground(QColor(WHITE))
        except Exception:
            item.setText("—")
            item.setForeground(QColor(DIM))
        return item

    def _update_alert_pnl(self):
        """Refresh the LIVE P&L column for all alert rows."""
        if not hasattr(self, 'alert_log_table'):
            return
        tbl = self.alert_log_table
        for row in range(tbl.rowCount()):
            time_item = tbl.item(row, 0)
            if time_item is None:
                continue
            data = time_item.data(Qt.ItemDataRole.UserRole)
            if not data:
                continue
            sym = data.get("symbol", "")
            entry_price = data.get("entry_price", 0)
            if not sym or not entry_price:
                continue
            ws_sym = sym.replace("USDT", "") + "USDT"
            cur_price = self._live_prices.get(ws_sym)
            pnl_item = self._make_pnl_item(entry_price, cur_price)
            tbl.setItem(row, 5, pnl_item)
        self._refresh_alert_pnl_summary()

    def _refresh_alert_pnl_summary(self):
        """Recompute and display Total Profit / Total Loss / Net from all visible alert rows."""
        if not hasattr(self, 'alert_log_table') or not hasattr(self, '_al_profit_lbl'):
            return
        tbl = self.alert_log_table
        total_profit = 0.0
        total_loss   = 0.0
        for row in range(tbl.rowCount()):
            pnl_item = tbl.item(row, 5)
            if pnl_item is None:
                continue
            txt = pnl_item.text()   # e.g. "$0.19970  +0.55%"
            # Extract the trailing % value
            try:
                pct_part = txt.split()[-1]          # "+0.55%" or "-3.80%"
                pct = float(pct_part.replace("%", ""))
                if pct > 0:
                    total_profit += pct
                elif pct < 0:
                    total_loss   += pct             # negative number
            except Exception:
                continue
        net = total_profit + total_loss
        sign = "+" if net >= 0 else ""
        net_col = GREEN if net > 0 else (RED if net < 0 else WHITE)
        self._al_profit_lbl.setText(f"+{total_profit:.2f}%")
        self._al_loss_lbl.setText(f"{total_loss:.2f}%")
        self._al_net_lbl.setText(f"{sign}{net:.2f}%")
        self._al_net_lbl.setStyleSheet(
            f"color:{net_col}; font-size:12px; font-weight:700; font-family:{MONO_CSS};")

    def _on_new_alert(self, alert):
        self._alert_log.append(alert)
        if len(self._alert_log) > 50:
            self._alert_log = self._alert_log[-50:]

        sig = alert["signal"]
        sym = alert["symbol"]

        # Subscribe symbol to WS so live P&L updates flow in
        if self._ws_feed:
            ws_sym = sym.replace("USDT", "") + "USDT"
            self._ws_feed.subscribe_alert(ws_sym)

        self._flash_window(sig)
        self._start_title_flash(sig, sym)
        self._update_status_alert(sig, sym)
        if "STRONG" in sig:
            self._show_strong_popup(alert)

        self._add_alert_row(alert, flash=True)
        self._update_history_tab_badge()
        self._save_alerts()

    def _on_surge_alert(self, alert):
        self._alert_log.append(alert)
        if len(self._alert_log) > 50:
            self._alert_log = self._alert_log[-50:]
        if self._ws_feed:
            ws_sym = alert.get("symbol", "").replace("USDT", "") + "USDT"
            self._ws_feed.subscribe_alert(ws_sym)
        self._add_alert_row(alert, flash=True)
        self._update_history_tab_badge()
        self._save_alerts()
        if ALERT_CFG["sound"]:
            self._alert_engine._play_sound("STRONG BUY")
        if ALERT_CFG["desktop"]:
            sym = alert["symbol"]
            msg = (f"VOLUME SURGE: {sym}  ${alert['price']:.5f}\n"
                   f"24h vol {alert['vol_24h_x']}x  |  price +{alert['chg_pct']:.1f}%  |  RSI {alert['rsi']:.0f}")
            self._alert_engine._desktop_notify("VOLUME SURGE", sym, msg)

    def _on_ws_connected(self):
        if hasattr(self, '_ws_status_lbl'):
            self._ws_status_lbl.setText("⚡ WS")
            self._ws_status_lbl.setStyleSheet(
                f"color:#00cc66; font-size:10px; font-weight:700; padding:0 6px;")
            self._ws_status_lbl.setToolTip("WebSocket connected — live price feed active")

    def _on_ws_disconnected(self):
        if hasattr(self, '_ws_status_lbl'):
            self._ws_status_lbl.setText("⚡ WS")
            self._ws_status_lbl.setStyleSheet(
                f"color:{YELLOW}; font-size:10px; font-weight:700; padding:0 6px;")
            self._ws_status_lbl.setToolTip("WebSocket disconnected — reconnecting…")

    def _on_ws_price(self, symbol: str, price: float):
        self._live_prices[symbol] = price
        for r in self._results:
            if r["symbol"] == symbol:
                r["price"] = price
        self._update_alert_pnl_for_symbol(symbol, price)
        if not getattr(self, "_ws_refresh_pending", False):
            self._ws_refresh_pending = True
            QTimer.singleShot(100, self._ws_flush)

    def _update_alert_pnl_for_symbol(self, ws_symbol: str, price: float):
        """Instantly update P&L for any alert rows matching this WS symbol."""
        if not hasattr(self, 'alert_log_table'):
            return
        tbl = self.alert_log_table
        for row in range(tbl.rowCount()):
            time_item = tbl.item(row, 0)
            if not time_item:
                continue
            data = time_item.data(Qt.ItemDataRole.UserRole)
            if not data:
                continue
            sym = data.get("symbol", "")
            if sym.replace("USDT", "") + "USDT" != ws_symbol:
                continue
            entry_price = data.get("entry_price", 0)
            if not entry_price:
                continue
            tbl.setItem(row, 5, self._make_pnl_item(entry_price, price))
        self._refresh_alert_pnl_summary()

    def _ws_flush(self):
        self._ws_refresh_pending = False
        self._check_sltp_hits(self._results)
        self._refresh_trades_table()

    def _on_alert_scan_started(self):
        if self._worker is None or not self._worker.isRunning():
            self.scan_btn.setEnabled(False)
            self.scan_btn.setText("⏳")
            self._set_dot_scanning()

    def _on_alert_scan_done(self, results):
        if self._worker is None or not self._worker.isRunning():
            self._results = results
            self._refresh_display()
            self._populate_picks(results)
            self._check_sltp_hits(results)
            self._refresh_trades_table()
            self._refresh_balance_display()
            n = len(results)
            self.statusBar().showMessage(
                f"Auto-scan: {n} coins  [{datetime.now().strftime('%H:%M:%S')}]")
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText("⚡")
            self._set_dot_idle(n)
            threading.Thread(
                target=log_scan_results,
                args=(results,),
                kwargs={"trades": self._trades},
                daemon=True
            ).start()
            QTimer.singleShot(2000, self._update_signal_log_size)
            if self._ws_feed:
                syms = {r["symbol"] for r in results}
                syms |= {t["symbol"] for t in self._trades if t["status"] == "OPEN"}
                self._ws_feed.subscribe(syms)
                if not self._ws_feed._running:
                    self._ws_feed.start()
            # Update _live_prices from scan results (covers alert symbols in the scan set)
            for r in results:
                if r.get("price"):
                    self._live_prices[r["symbol"]] = r["price"]

    def _remove_alert_row(self, row: int):
        """Remove a single alert row from the table and from _alert_log."""
        tbl = self.alert_log_table
        if row < 0 or row >= tbl.rowCount():
            return
        # Remove matching entry from _alert_log by time+symbol
        time_item = tbl.item(row, 0)
        if time_item:
            data = time_item.data(Qt.ItemDataRole.UserRole) or {}
            sym  = data.get("symbol", "")
            t    = time_item.text()
            self._alert_log = [
                a for a in self._alert_log
                if not (a.get("symbol", "").replace("USDT", "") == sym and
                        a.get("time", "") == t)
            ]
        tbl.removeRow(row)
        self._update_history_tab_badge()
        self._refresh_alert_pnl_summary()
        self._save_alerts()

    def _clear_alert_log(self):
        self._alert_log.clear()
        if hasattr(self, 'alert_log_table'):
            self.alert_log_table.setRowCount(0)
        self._update_history_tab_badge()
        if hasattr(self, 'al_history_stats'):
            self.al_history_stats.setText("No alerts yet — waiting for signals")
        self._refresh_alert_pnl_summary()
        self._save_alerts()

    def _flash_window(self, signal):
        is_buy    = "BUY" in signal
        is_strong = "STRONG" in signal
        color     = "#00ee77" if is_buy else "#ee2222"
        flashes   = 3 if is_strong else 2

        if self._flash_overlay is not None:
            try:
                self._flash_overlay.hide()
                self._flash_overlay.setParent(None)
            except RuntimeError:
                pass
            self._flash_overlay = None
        if self._flash_anim is not None:
            try:
                self._flash_anim.stop()
            except RuntimeError:
                pass
            self._flash_anim = None

        overlay = QWidget(self.centralWidget())
        overlay.setGeometry(self.centralWidget().rect())
        overlay.setStyleSheet(f"background-color: {color};")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.raise_()
        overlay.show()
        self._flash_overlay = overlay

        state    = {"count": flashes * 2}
        interval = 110

        timer = QTimer(self)
        self._flash_anim = timer

        def tick():
            state["count"] -= 1
            try:
                if state["count"] <= 0:
                    timer.stop()
                    overlay.hide()
                    overlay.setParent(None)
                    self._flash_overlay = None
                    self._flash_anim    = None
                else:
                    overlay.setVisible(state["count"] % 2 == 0)
            except RuntimeError:
                timer.stop()

        timer.timeout.connect(tick)
        timer.start(interval)

    def _start_title_flash(self, signal, symbol):
        is_buy = "BUY" in signal
        arrow  = "🚀" if is_buy else "🔴"
        self._title_flash_msg   = f"{arrow} {signal}: {symbol}"
        self._title_flash_count = 20
        self._title_flash_state = False
        self._title_flash_timer.start(400)

    def _flash_title_tick(self):
        if self._title_flash_count <= 0:
            self._title_flash_timer.stop()
            self.setWindowTitle("Crypto Scalp Scanner")
            return
        self._title_flash_state = not self._title_flash_state
        if self._title_flash_state:
            self.setWindowTitle(f"⚡ {self._title_flash_msg} ⚡")
        else:
            self.setWindowTitle("Crypto Scalp Scanner")
        self._title_flash_count -= 1

    def _show_strong_popup(self, alert):
        sig    = alert["signal"]
        sym    = alert["symbol"]
        is_buy = "BUY" in sig
        color  = "#00ff88" if is_buy else "#ff4444"
        arrow  = "🚀" if is_buy else "🔻"
        direction = "LONG" if is_buy else "SHORT"

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{arrow} {sig}")
        dlg.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint)
        dlg.setStyleSheet(
            f"background: #0d0d0d; border: 3px solid {color}; border-radius: 10px;")
        dlg.setFixedWidth(420)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        hdr = QLabel(f"{arrow}  {sig}")
        hdr.setStyleSheet(
            f"color: {color}; font-size: 22px; font-weight: 900; "
            f"font-family: monospace; border: none;")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sym_lbl = QLabel(sym)
        sym_lbl.setStyleSheet(
            "color: #ffffff; font-size: 32px; font-weight: 900; "
            "font-family: monospace; border: none;")
        sym_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        dir_lbl = QLabel(f"[ {direction} ]")
        dir_lbl.setStyleSheet(
            f"color: {color}; font-size: 16px; font-weight: 700; "
            f"font-family: monospace; border: none;")
        dir_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {color}; border: 1px solid {color};")

        def stat_row(label, value, val_color="#e0e0e0"):
            ww = QWidget()
            ww.setStyleSheet("border: none; background: transparent;")
            hl = QHBoxLayout(ww)
            hl.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #888; font-size: 13px; font-family: monospace; border: none;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {val_color}; font-size: 13px; font-weight: 700; font-family: monospace; border: none;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hl.addWidget(lbl)
            hl.addStretch()
            hl.addWidget(val)
            return ww

        price_row   = stat_row("Price",       f"${alert['price']:.5f}", "#ffffff")
        rsi_row     = stat_row("RSI",          f"{alert['rsi']:.1f}", "#f0c040")
        exp_row     = stat_row("Exp Move",     f"{alert['exp']:.1f}%", color)
        pot_row     = stat_row("Potential",    f"{alert['pot']}%", color)
        vol_row     = stat_row("Volume",       f"{alert['vol']:.1f}x avg", "#aaaaaa")
        pat_row     = stat_row("Pattern",      alert["pattern"], "#cccccc")

        countdown_lbl = QLabel("Auto-dismiss in 15s")
        countdown_lbl.setStyleSheet(
            "color: #555; font-size: 11px; border: none; font-family: monospace;")
        countdown_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        dismiss_btn = QPushButton("✕  Dismiss")
        dismiss_btn.setStyleSheet(
            f"background: {color}22; color: {color}; border: 1px solid {color}; "
            f"border-radius: 5px; padding: 6px 20px; font-weight: 700; font-size: 13px;")
        dismiss_btn.clicked.connect(dlg.accept)

        lay.addWidget(hdr)
        lay.addWidget(sym_lbl)
        lay.addWidget(dir_lbl)
        lay.addWidget(sep)
        lay.addWidget(price_row)
        lay.addWidget(rsi_row)
        lay.addWidget(exp_row)
        lay.addWidget(pot_row)
        lay.addWidget(vol_row)
        lay.addWidget(pat_row)
        lay.addSpacing(6)
        lay.addWidget(countdown_lbl)
        lay.addWidget(dismiss_btn)

        geo  = self.geometry()
        dlg.adjustSize()
        dlg.move(geo.right() - dlg.width() - 20, geo.top() + 60)

        remaining = [15]
        def tick():
            remaining[0] -= 1
            countdown_lbl.setText(f"Auto-dismiss in {remaining[0]}s")
            if remaining[0] <= 0:
                timer.stop()
                dlg.accept()
        timer = QTimer(dlg)
        timer.timeout.connect(tick)
        timer.start(1000)

        dlg.show()

    def _update_status_alert(self, signal, symbol):
        is_buy = "BUY" in signal
        color  = "#00cc66" if is_buy else "#cc2222"
        self.statusBar().setStyleSheet(f"background: {color}; color: #ffffff; font-weight: 700;")
        self.statusBar().showMessage(
            f"  ⚡ {signal}: {symbol}  —  click Scan to refresh")
        self._status_alert_active = True
        QTimer.singleShot(60000, self._clear_status_alert)

    def _clear_status_alert(self):
        if self._status_alert_active:
            self.statusBar().setStyleSheet("")
            self.statusBar().showMessage("Ready")
            self._status_alert_active = False

    def _build_config_tab(self):
        outer = QWidget()
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        def _cfg_grid(parent):
            g = QGridLayout(parent)
            g.setSpacing(12)
            g.setColumnMinimumWidth(0, 160)
            g.setColumnStretch(0, 0)
            g.setColumnStretch(1, 1)
            return g

        filter_grp = QGroupBox("SCAN FILTERS")
        flay = _cfg_grid(filter_grp)

        self.cfg_max_price = QDoubleSpinBox()
        self.cfg_max_price.setRange(0.01, 100); self.cfg_max_price.setDecimals(2)
        self.cfg_max_price.setPrefix("$"); self.cfg_max_price.setValue(CFG["max_price"]); self.cfg_max_price.setFixedWidth(160)

        self.cfg_min_vol = QDoubleSpinBox()
        self.cfg_min_vol.setRange(100000, 1e9); self.cfg_min_vol.setDecimals(0)
        self.cfg_min_vol.setPrefix("$"); self.cfg_min_vol.setSingleStep(100000)
        self.cfg_min_vol.setValue(CFG["min_volume_usdt"]); self.cfg_min_vol.setFixedWidth(160)

        self.cfg_interval = QComboBox()
        self.cfg_interval.setFixedWidth(160)
        for iv in ["1m", "3m", "5m", "15m", "30m", "1h"]:
            self.cfg_interval.addItem(iv)
        self.cfg_interval.setCurrentText(CFG["interval"])

        self.cfg_top_n = QSpinBox()
        self.cfg_top_n.setRange(5, 100); self.cfg_top_n.setValue(CFG["top_n"]); self.cfg_top_n.setFixedWidth(160)

        self.cfg_picks_n = QSpinBox()
        self.cfg_picks_n.setRange(1, 20); self.cfg_picks_n.setValue(CFG["picks_n"]); self.cfg_picks_n.setFixedWidth(160)
        self.cfg_picks_n.setToolTip("Max cards shown per section in Top Picks tab")

        self.cfg_candles = QSpinBox()
        self.cfg_candles.setRange(20, 200); self.cfg_candles.setValue(CFG["candle_limit"]); self.cfg_candles.setFixedWidth(160)

        self.cfg_new_listing = QCheckBox("Enable")
        self.cfg_new_listing.setChecked(CFG.get("new_listing_filter", False))
        self.cfg_new_listing.setStyleSheet(f"color:{WHITE};")
        self.cfg_new_listing.setToolTip(
            "Only scan coins listed on Binance within the day range below.\n"
            "Adds one lightweight API call per coin to check listing date.")

        self.cfg_new_listing_min = QSpinBox()
        self.cfg_new_listing_min.setRange(1, 30)
        self.cfg_new_listing_min.setValue(CFG.get("new_listing_min_days", 2))
        self.cfg_new_listing_min.setFixedWidth(160)
        self.cfg_new_listing_min.setToolTip("Minimum days since listing (skip coins listed today / yesterday)")

        self.cfg_new_listing_max = QSpinBox()
        self.cfg_new_listing_max.setRange(2, 90)
        self.cfg_new_listing_max.setValue(CFG.get("new_listing_max_days", 10))
        self.cfg_new_listing_max.setFixedWidth(160)
        self.cfg_new_listing_max.setToolTip("Maximum days since listing (skip older coins)")

        rows = [
            ("Max Price ($)",     self.cfg_max_price),
            ("Min Volume (USDT)", self.cfg_min_vol),
            ("Interval",          self.cfg_interval),
            ("Top N coins",       self.cfg_top_n),
            ("Top Picks to show", self.cfg_picks_n),
            ("Candles to fetch",  self.cfg_candles),
            ("New Listing Filter", self.cfg_new_listing),
            ("Listed min days",   self.cfg_new_listing_min),
            ("Listed max days",   self.cfg_new_listing_max),
        ]
        for i, (lbl, widget) in enumerate(rows):
            l = QLabel(lbl); l.setStyleSheet(f"color:{DIM};")
            flay.addWidget(l, i, 0)
            flay.addWidget(widget, i, 1, Qt.AlignmentFlag.AlignLeft)

        # Symbol blocklist — one symbol per line, no USDT suffix needed
        blocklist_lbl = QLabel("Symbol blocklist")
        blocklist_lbl.setStyleSheet(f"color:{DIM};")
        blocklist_lbl.setToolTip("One symbol per line. USDT suffix optional.\nCoins in this list are excluded from all scans and alerts.")
        from PyQt6.QtWidgets import QPlainTextEdit
        self.cfg_blocklist = QPlainTextEdit()
        self.cfg_blocklist.setFixedHeight(90)
        self.cfg_blocklist.setStyleSheet(
            f"background:{CARD}; color:{WHITE}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-family:{MONO_CSS}; font-size:11px; padding:4px;")
        self.cfg_blocklist.setToolTip(
            "Symbols to exclude from scanning entirely.\n"
            "Enter one per line — e.g. USDC, BUSD, WLFI, MBL\n"
            "USDT suffix is added automatically if missing.\n"
            "Changes apply on next scan after clicking Apply.")
        # Populate from CFG
        current_bl = CFG.get("symbol_blocklist", set())
        self.cfg_blocklist.setPlainText(
            "\n".join(sorted(s.replace("USDT", "") for s in current_bl)))
        flay.addWidget(blocklist_lbl,      len(rows),     0, Qt.AlignmentFlag.AlignTop)
        flay.addWidget(self.cfg_blocklist, len(rows),     1)

        grid2 = QGridLayout()
        grid2.setSpacing(12)
        grid2.setColumnStretch(0, 1)
        grid2.setColumnStretch(1, 1)
        grid2.addWidget(filter_grp, 0, 0)

        risk_grp = QGroupBox("RISK MANAGEMENT")
        rlay = _cfg_grid(risk_grp)

        self.cfg_sl  = QDoubleSpinBox(); self.cfg_sl.setRange(0.5, 20); self.cfg_sl.setValue(CFG["sl_pct"]); self.cfg_sl.setSuffix("%"); self.cfg_sl.setFixedWidth(160)
        self.cfg_tp  = QDoubleSpinBox(); self.cfg_tp.setRange(0.5, 50); self.cfg_tp.setValue(CFG["tp_pct"]); self.cfg_tp.setSuffix("%"); self.cfg_tp.setFixedWidth(160)
        self.cfg_tp2 = QDoubleSpinBox(); self.cfg_tp2.setRange(1.0, 100); self.cfg_tp2.setValue(CFG["tp2_pct"]); self.cfg_tp2.setSuffix("%"); self.cfg_tp2.setFixedWidth(160)

        self.rr_lbl = QLabel()
        self._update_rr_label()
        self.cfg_sl.valueChanged.connect(self._update_rr_label)
        self.cfg_tp.valueChanged.connect(self._update_rr_label)

        risk_rows = [
            ("Stop Loss %",      self.cfg_sl),
            ("Take Profit %",    self.cfg_tp),
            ("TP2 % (extended)", self.cfg_tp2),
        ]
        for i, (lbl, widget) in enumerate(risk_rows):
            l = QLabel(lbl); l.setStyleSheet(f"color:{DIM};")
            rlay.addWidget(l, i, 0)
            rlay.addWidget(widget, i, 1, Qt.AlignmentFlag.AlignLeft)

        rr_lbl_title = QLabel("R/R Ratio"); rr_lbl_title.setStyleSheet(f"color:{DIM};")
        rlay.addWidget(rr_lbl_title, len(risk_rows), 0)
        rlay.addWidget(self.rr_lbl, len(risk_rows), 1)
        grid2.addWidget(risk_grp, 0, 1)

        safety_grp = QGroupBox("TRADE SAFETY")
        slay = _cfg_grid(safety_grp)

        self.sf_persistence = QCheckBox("Signal must hold 2+ consecutive scans")
        self.sf_persistence.setChecked(SAFETY_CFG["signal_persistence"])
        self.sf_persistence.setStyleSheet(f"color:{WHITE};")
        self.sf_persistence.setToolTip("Eliminates false signals that appear for only one scan")

        self.sf_btc_check = QCheckBox("Skip if BTC dropping >")
        self.sf_btc_check.setChecked(SAFETY_CFG["btc_trend_check"])
        self.sf_btc_check.setStyleSheet(f"color:{WHITE};")
        self.sf_btc_drop = QDoubleSpinBox()
        self.sf_btc_drop.setRange(0.5, 10); self.sf_btc_drop.setValue(SAFETY_CFG["btc_drop_pct"])
        self.sf_btc_drop.setSuffix("%"); self.sf_btc_drop.setFixedWidth(100)
        self.sf_btc_drop.setEnabled(SAFETY_CFG["btc_trend_check"])
        self.sf_btc_check.toggled.connect(self.sf_btc_drop.setEnabled)

        self.sf_coin_check = QCheckBox("Skip if coin down >")
        self.sf_coin_check.setChecked(SAFETY_CFG["coin_trend_check"])
        self.sf_coin_check.setStyleSheet(f"color:{WHITE};")
        self.sf_coin_drop = QDoubleSpinBox()
        self.sf_coin_drop.setRange(1, 20); self.sf_coin_drop.setValue(SAFETY_CFG["coin_drop_pct"])
        self.sf_coin_drop.setSuffix("% in 24h"); self.sf_coin_drop.setFixedWidth(120)
        self.sf_coin_drop.setEnabled(SAFETY_CFG["coin_trend_check"])
        self.sf_coin_check.toggled.connect(self.sf_coin_drop.setEnabled)

        self.sf_max_trades = QCheckBox("Max open trades")
        self.sf_max_trades.setChecked(SAFETY_CFG["max_open_trades"])
        self.sf_max_trades.setStyleSheet(f"color:{WHITE};")
        self.sf_max_trades_n = QSpinBox()
        self.sf_max_trades_n.setRange(1, 20); self.sf_max_trades_n.setValue(SAFETY_CFG["max_open_trades_count"])
        self.sf_max_trades_n.setFixedWidth(100)
        self.sf_max_trades_n.setEnabled(SAFETY_CFG["max_open_trades"])
        self.sf_max_trades.toggled.connect(self.sf_max_trades_n.setEnabled)

        self.sf_daily_loss = QCheckBox("Daily loss limit  $")
        self.sf_daily_loss.setChecked(SAFETY_CFG["daily_loss_limit"])
        self.sf_daily_loss.setStyleSheet(f"color:{WHITE};")
        self.sf_daily_loss_n = QDoubleSpinBox()
        self.sf_daily_loss_n.setRange(10, 10000); self.sf_daily_loss_n.setValue(SAFETY_CFG["daily_loss_amount"])
        self.sf_daily_loss_n.setPrefix("$"); self.sf_daily_loss_n.setFixedWidth(100)
        self.sf_daily_loss_n.setEnabled(SAFETY_CFG["daily_loss_limit"])
        self.sf_daily_loss.toggled.connect(self.sf_daily_loss_n.setEnabled)

        self.sf_reset_btn = QPushButton("Reset Daily Loss")
        self.sf_reset_btn.setFixedWidth(140)
        self.sf_reset_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:2px 8px; font-size:11px;")
        def _reset_daily():
            _daily_loss_tracker["loss"] = 0.0
            _daily_loss_tracker["date"] = ""
            self.statusBar().showMessage("Daily loss counter reset")
        self.sf_reset_btn.clicked.connect(_reset_daily)

        slay.addWidget(self.sf_persistence, 0, 0, 1, 2)
        slay.addWidget(self.sf_btc_check,   1, 0)
        slay.addWidget(self.sf_btc_drop,    1, 1, Qt.AlignmentFlag.AlignLeft)

        self.sf_btc_cooldown_check = QCheckBox("  BTC drop cooldown")
        self.sf_btc_cooldown_check.setChecked(True)
        self.sf_btc_cooldown_check.setStyleSheet(f"color:{WHITE};")
        self.sf_btc_cooldown_check.setToolTip(
            "After BTC drop triggers, block new LONGs for this many minutes.\n"
            "Lifts early if BTC recovers the % set to the right.")
        self.sf_btc_cooldown_mins = QSpinBox()
        self.sf_btc_cooldown_mins.setRange(5, 240)
        self.sf_btc_cooldown_mins.setValue(SAFETY_CFG.get("btc_drop_cooldown_mins", 60))
        self.sf_btc_cooldown_mins.setSuffix(" min block")
        self.sf_btc_cooldown_mins.setFixedWidth(120)
        self.sf_btc_recovery = QDoubleSpinBox()
        self.sf_btc_recovery.setRange(0.5, 10.0)
        self.sf_btc_recovery.setValue(SAFETY_CFG.get("btc_recovery_pct", 1.5))
        self.sf_btc_recovery.setSuffix("% BTC recovery to lift early")
        self.sf_btc_recovery.setFixedWidth(210)
        slay.addWidget(self.sf_btc_cooldown_check, 2, 0)
        slay.addWidget(self.sf_btc_cooldown_mins,  2, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_btc_recovery,       2, 2, Qt.AlignmentFlag.AlignLeft)

        self.sf_trend_freshness = QCheckBox("Override stale trend_1h='up' if price fell >")
        self.sf_trend_freshness.setChecked(SAFETY_CFG.get("trend_1h_freshness", True))
        self.sf_trend_freshness.setStyleSheet(f"color:{WHITE};")
        self.sf_trend_freshness.setToolTip(
            "If the coin has already dropped this much from its 1h candle open,\n"
            "treat trend_1h='up' as stale and block the LONG.")
        self.sf_trend_stale = QDoubleSpinBox()
        self.sf_trend_stale.setRange(0.5, 10.0)
        self.sf_trend_stale.setValue(SAFETY_CFG.get("trend_1h_stale_pct", 1.5))
        self.sf_trend_stale.setSuffix("% below 1h open")
        self.sf_trend_stale.setFixedWidth(160)
        self.sf_trend_stale.setEnabled(SAFETY_CFG.get("trend_1h_freshness", True))
        self.sf_trend_freshness.toggled.connect(self.sf_trend_stale.setEnabled)
        slay.addWidget(self.sf_trend_freshness, 3, 0)
        slay.addWidget(self.sf_trend_stale,     3, 1, Qt.AlignmentFlag.AlignLeft)

        self.sf_sym_recovery = QCheckBox("Per-symbol recovery gate after safety block")
        self.sf_sym_recovery.setChecked(SAFETY_CFG.get("symbol_recovery_gate", True))
        self.sf_sym_recovery.setStyleSheet(f"color:{WHITE};")
        self.sf_sym_recovery.setToolTip(
            "After a safety block fires for a coin, require its price to bounce\n"
            "by this % before a new LONG is allowed.")
        self.sf_sym_recovery_pct = QDoubleSpinBox()
        self.sf_sym_recovery_pct.setRange(0.2, 10.0)
        self.sf_sym_recovery_pct.setValue(SAFETY_CFG.get("symbol_recovery_pct", 1.0))
        self.sf_sym_recovery_pct.setSuffix("% bounce required")
        self.sf_sym_recovery_pct.setFixedWidth(160)
        self.sf_sym_recovery_pct.setEnabled(SAFETY_CFG.get("symbol_recovery_gate", True))
        self.sf_sym_expiry = QSpinBox()
        self.sf_sym_expiry.setRange(5, 120)
        self.sf_sym_expiry.setValue(SAFETY_CFG.get("symbol_recovery_expiry_mins", 30))
        self.sf_sym_expiry.setSuffix(" min max lock")
        self.sf_sym_expiry.setFixedWidth(130)
        self.sf_sym_expiry.setEnabled(SAFETY_CFG.get("symbol_recovery_gate", True))
        self.sf_sym_recovery.toggled.connect(self.sf_sym_recovery_pct.setEnabled)
        self.sf_sym_recovery.toggled.connect(self.sf_sym_expiry.setEnabled)
        slay.addWidget(self.sf_sym_recovery,     4, 0)
        slay.addWidget(self.sf_sym_recovery_pct, 4, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_sym_expiry,       4, 2, Qt.AlignmentFlag.AlignLeft)

        slay.addWidget(self.sf_coin_check,   5, 0)
        slay.addWidget(self.sf_coin_drop,    5, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_max_trades,   6, 0)
        slay.addWidget(self.sf_max_trades_n, 6, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_daily_loss,   7, 0)
        slay.addWidget(self.sf_daily_loss_n, 7, 1, Qt.AlignmentFlag.AlignLeft)
        slay.addWidget(self.sf_reset_btn,    8, 0, 1, 2)

        grid2.addWidget(safety_grp, 1, 0)

        ui_grp = QGroupBox("UI APPEARANCE")
        ulay   = _cfg_grid(ui_grp)

        self.cfg_font_size = QSpinBox()
        self.cfg_font_size.setRange(8, 20)
        self.cfg_font_size.setValue(FONT_SIZE)
        self.cfg_font_size.setSuffix(" px")
        self.cfg_font_size.setFixedWidth(160)
        self.cfg_font_size.setToolTip("Base font size — all text scales proportionally")

        fs_lbl  = QLabel("Font Size"); fs_lbl.setStyleSheet(f"color:{DIM};")
        fs_hint = QLabel("Resize the window to test layout at any font size")
        fs_hint.setStyleSheet(f"color:{DIM}; font-size:10px;")

        ulay.addWidget(fs_lbl,             0, 0)
        ulay.addWidget(self.cfg_font_size, 0, 1, Qt.AlignmentFlag.AlignLeft)
        ulay.addWidget(fs_hint,            1, 0, 1, 2)
        grid2.addWidget(ui_grp, 1, 1)

        alert_grp = QGroupBox("ALERTS")
        alay = QHBoxLayout(alert_grp)
        alay.setSpacing(12)

        self.cfg_alert_enabled = QPushButton()
        self.cfg_alert_enabled.setCheckable(True)
        self.cfg_alert_enabled.setChecked(ALERT_CFG["enabled"])
        self.cfg_alert_enabled.setFixedHeight(34)
        self._refresh_alert_toggle()
        self.cfg_alert_enabled.clicked.connect(self._on_alert_toggle)

        alay.addWidget(self.cfg_alert_enabled)
        alay.addStretch()
        grid2.addWidget(alert_grp, 2, 0)

        export_grp = QGroupBox("EXPORT SCAN RESULTS")
        elay = QVBoxLayout(export_grp)
        elay.setSpacing(8)

        export_btn = QPushButton("↓  Export Last Scan to JSON")
        export_btn.setFixedHeight(34)
        export_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; font-weight:700; padding:0 14px;")
        export_btn.clicked.connect(self._export)
        self.cfg_export_lbl = QLabel("No scan yet")
        self.cfg_export_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        exp_row = QHBoxLayout()
        exp_row.addWidget(export_btn)
        exp_row.addSpacing(12)
        exp_row.addWidget(self.cfg_export_lbl)
        exp_row.addStretch()
        elay.addLayout(exp_row)

        signal_log_btn = QPushButton("📋  Open Signal Log")
        signal_log_btn.setFixedHeight(30)
        signal_log_btn.setStyleSheet(
            f"background:{CARD}; color:{YELLOW}; border:1px solid {YELLOW}; "
            f"border-radius:4px; font-size:11px; padding:0 12px;")
        signal_log_btn.setToolTip(f"CSV audit log of all scan results: {SIGNAL_LOG_PATH}")
        signal_log_btn.clicked.connect(self._open_signal_log)

        outcome_btn = QPushButton("📊  Outcome Analysis")
        outcome_btn.setFixedHeight(30)
        outcome_btn.setStyleSheet(
            f"background:{CARD}; color:#00cc99; border:1px solid #00cc99; "
            f"border-radius:4px; font-size:11px; padding:0 12px;")
        outcome_btn.setToolTip("Analyse alert outcomes — WIN/LOSS/FLAT rates from signal log")
        outcome_btn.clicked.connect(self._show_outcome_analysis)

        self._signal_log_size_lbl = QLabel()
        self._signal_log_size_lbl.setStyleSheet(f"color:{DIM}; font-size:10px;")
        self._update_signal_log_size()

        clear_log_btn = QPushButton("🗑  Clear")
        clear_log_btn.setFixedHeight(30)
        clear_log_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; font-size:11px; padding:0 10px;")
        clear_log_btn.clicked.connect(self._clear_signal_log)

        log_row = QHBoxLayout()
        log_row.addWidget(signal_log_btn)
        log_row.addWidget(outcome_btn)
        log_row.addWidget(clear_log_btn)
        log_row.addWidget(self._signal_log_size_lbl)
        log_row.addStretch()
        elay.addLayout(log_row)

        grid2.addWidget(export_grp, 2, 1)
        lay.addLayout(grid2)

        api_grp = QGroupBox("BINANCE API  —  TRADING")
        api_grp.setStyleSheet(
            f"QGroupBox {{ border:1px solid {YELLOW}; border-radius:6px; "
            f"margin-top:8px; color:{YELLOW}; font-weight:700; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; }}")
        aplay = QVBoxLayout(api_grp)
        aplay.setSpacing(8)

        mode_row = QHBoxLayout()
        self.cfg_testnet = QPushButton("🧪  TESTNET MODE  (safe)")
        self.cfg_testnet.setCheckable(True)
        self.cfg_testnet.setChecked(TRADING_CFG["testnet"])
        self.cfg_testnet.setFixedHeight(32)
        self._refresh_trading_mode_btn()
        self.cfg_testnet.clicked.connect(self._on_trading_mode_toggle)
        mode_row.addWidget(self.cfg_testnet)
        mode_row.addStretch()
        aplay.addLayout(mode_row)

        self.cfg_live_warning = QLabel(
            "⚠  LIVE MODE — real money at risk. "
            "API key must have TRADE permission only. NO withdrawal permission.")
        self.cfg_live_warning.setStyleSheet(
            f"color:{RED}; font-size:11px; font-weight:700; padding:4px 0;")
        self.cfg_live_warning.setWordWrap(True)
        self.cfg_live_warning.setVisible(not TRADING_CFG["testnet"])
        aplay.addWidget(self.cfg_live_warning)

        key_grid = QGridLayout()
        key_grid.setSpacing(8)
        key_grid.setColumnMinimumWidth(0, 100)
        key_grid.setColumnStretch(1, 1)

        key_lbl = QLabel("API Key:"); key_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self.cfg_api_key = QLineEdit()
        self.cfg_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.cfg_api_key.setPlaceholderText("Paste your Binance API key here")
        self.cfg_api_key.setText(TRADING_CFG["api_key"])

        sec_lbl = QLabel("Secret:"); sec_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self.cfg_api_secret = QLineEdit()
        self.cfg_api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.cfg_api_secret.setPlaceholderText("Paste your Binance API secret here")
        self.cfg_api_secret.setText(TRADING_CFG["api_secret"])

        show_btn = QPushButton("👁")
        show_btn.setFixedSize(28, 28)
        show_btn.setCheckable(True)
        show_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px;")
        show_btn.setToolTip("Show / hide keys")
        def _toggle_echo(checked):
            mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            self.cfg_api_key.setEchoMode(mode)
            self.cfg_api_secret.setEchoMode(mode)
        show_btn.toggled.connect(_toggle_echo)

        key_grid.addWidget(key_lbl,             0, 0)
        key_grid.addWidget(self.cfg_api_key,    0, 1)
        key_grid.addWidget(show_btn,            0, 2, 2, 1)
        key_grid.addWidget(sec_lbl,             1, 0)
        key_grid.addWidget(self.cfg_api_secret, 1, 1)
        aplay.addLayout(key_grid)

        oco_row = QHBoxLayout()
        self.cfg_oco = QCheckBox("Place OCO stop-loss on Binance after each buy")
        self.cfg_oco.setChecked(TRADING_CFG["oco_enabled"])
        self.cfg_oco.setStyleSheet(f"color:{DIM}; font-size:11px;")
        self.cfg_oco.setToolTip(
            "OCO = One-Cancels-the-Other order\n"
            "Places a stop-loss directly on Binance — protects you even if the app is closed.\n"
            "Disable to use in-app monitoring only.")
        oco_row.addWidget(self.cfg_oco)
        oco_row.addStretch()
        aplay.addLayout(oco_row)

        conn_row = QHBoxLayout()
        self.cfg_conn_btn = QPushButton("🔌  Test Connection")
        self.cfg_conn_btn.setFixedHeight(30)
        self.cfg_conn_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; font-weight:700; padding:0 14px;")
        self.cfg_conn_btn.clicked.connect(self._test_api_connection)
        self.cfg_conn_lbl = QLabel("Not tested")
        self.cfg_conn_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")
        conn_row.addWidget(self.cfg_conn_btn)
        conn_row.addSpacing(10)
        conn_row.addWidget(self.cfg_conn_lbl)
        conn_row.addStretch()
        aplay.addLayout(conn_row)

        lay.addWidget(api_grp)

        browser_grp = QGroupBox("BROWSER")
        blay = QHBoxLayout(browser_grp)
        blay.setSpacing(8)

        browser_lbl = QLabel("Browser path:")
        browser_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")

        self.cfg_browser = QLineEdit()
        self.cfg_browser.setPlaceholderText(
            "Leave empty for system default  (e.g. /usr/bin/firefox  or  /usr/bin/brave)")
        self.cfg_browser.setText(BROWSER_PATH)
        self.cfg_browser.setToolTip(
            "Full path to browser binary. Leave empty to use xdg-open / system default.")

        browse_btn = QPushButton("…")
        browse_btn.setFixedWidth(32)
        browse_btn.setFixedHeight(28)
        browse_btn.setToolTip("Pick browser binary")
        browse_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; border-radius:4px;")
        browse_btn.clicked.connect(self._pick_browser)

        test_browser_btn = QPushButton("🌐  Test")
        test_browser_btn.setFixedHeight(28)
        test_browser_btn.setStyleSheet(
            f"background:{CARD}; color:{ACCENT}; border:1px solid {ACCENT}; "
            f"border-radius:4px; padding:0 10px;")
        test_browser_btn.setToolTip("Open Binance in the configured browser")
        test_browser_btn.clicked.connect(
            lambda: open_url("https://www.binance.com"))

        blay.addWidget(browser_lbl)
        blay.addWidget(self.cfg_browser, 1)
        blay.addWidget(browse_btn)
        blay.addWidget(test_browser_btn)
        lay.addWidget(browser_grp)

        apply_btn = QPushButton("✓  Apply Settings")
        apply_btn.clicked.connect(self._apply_config)
        lay.addWidget(apply_btn)
        lay.addStretch()

        scroll.setWidget(w)
        outer_lay.addWidget(scroll)
        return outer

    def _pick_browser(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Browser Binary", "/usr/bin", "Executables (*)")
        if path:
            self.cfg_browser.setText(path)

    def _refresh_balance_display(self):
        if not TRADING_CFG["api_key"]:
            self._balance_lbl.setText("💰 —")
            return
        self._balance_lbl.setText("💰 …")

        class _BalFetch(QThread):
            done = pyqtSignal(bool, float)
            def run(self_):
                ok, bal = _trader.get_usdt_balance()
                self_.done.emit(ok, bal)

        def _on_done(ok, bal):
            if ok:
                env = "T" if TRADING_CFG["testnet"] else "L"
                self._balance_lbl.setText(f"💰 {bal:,.2f} USDT [{env}]")
                col = GREEN if TRADING_CFG["testnet"] else RED
                self._balance_lbl.setStyleSheet(
                    f"color:{col}; font-family:{MONO_CSS}; font-size:11px; "
                    f"font-weight:700; padding:0 8px;")
            else:
                self._balance_lbl.setText("💰 —")

        self._bal_fetch_thread = _BalFetch()
        self._bal_fetch_thread.done.connect(_on_done)
        self._bal_fetch_thread.start()

    def _start_update_check(self):
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    def _on_update_available(self, latest_tag: str, release_url: str):
        self._update_release_url = release_url
        self._update_banner_lbl.setText(
            f"⬆  New version available: {latest_tag}  —  you have {APP_VERSION}")
        self._update_banner.setVisible(True)

    def _refresh_trading_mode_btn(self):
        if not hasattr(self, 'cfg_testnet'):
            return
        if TRADING_CFG["testnet"]:
            self.cfg_testnet.setText("🧪  TESTNET MODE  (safe)")
            self.cfg_testnet.setStyleSheet(
                f"background:#003a1a; color:{GREEN}; border:1px solid {GREEN}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;")
        else:
            self.cfg_testnet.setText("🔴  LIVE MODE  — real money")
            self.cfg_testnet.setStyleSheet(
                f"background:#3a0000; color:{RED}; border:2px solid {RED}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;")

    def _on_trading_mode_toggle(self):
        is_live = not self.cfg_testnet.isChecked()
        if is_live:
            reply = QMessageBox.warning(
                self, "Switch to LIVE Trading",
                "⚠  You are switching to LIVE mode.\n\n"
                "Real money will be used for all trades.\n"
                "Make sure your API key has TRADE permission only — NO withdrawal.\n\n"
                "Are you sure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                self.cfg_testnet.setChecked(True)
                return
        TRADING_CFG["testnet"] = self.cfg_testnet.isChecked()
        self._refresh_trading_mode_btn()
        self.cfg_live_warning.setVisible(not TRADING_CFG["testnet"])
        self._refresh_live_banner()
        mode = "TESTNET" if TRADING_CFG["testnet"] else "LIVE"
        self.statusBar().showMessage(f"Trading mode switched to {mode}")
        self.cfg_conn_lbl.setText("Not tested")

    def _refresh_live_banner(self):
        is_live = not TRADING_CFG["testnet"]
        if hasattr(self, '_live_banner'):
            self._live_banner.setVisible(is_live)

    def _test_api_connection(self):
        self.cfg_conn_btn.setEnabled(False)
        self.cfg_conn_lbl.setText("Testing…")
        self.cfg_conn_lbl.setStyleSheet(f"color:{DIM}; font-size:11px;")

        TRADING_CFG["api_key"]    = self.cfg_api_key.text().strip()
        TRADING_CFG["api_secret"] = self.cfg_api_secret.text().strip()
        TRADING_CFG["oco_enabled"] = self.cfg_oco.isChecked()

        class _ConnTest(QThread):
            result = pyqtSignal(bool, str)
            def run(self_):
                ok, msg = _trader.test_connection()
                self_.result.emit(ok, msg)

        self._conn_thread = _ConnTest()
        def _on_result(ok, msg):
            self.cfg_conn_btn.setEnabled(True)
            if ok:
                self.cfg_conn_lbl.setText(msg)
                self.cfg_conn_lbl.setStyleSheet(
                    f"color:{GREEN}; font-size:11px; font-weight:700;")
            else:
                self.cfg_conn_lbl.setText(f"✗ {msg}")
                self.cfg_conn_lbl.setStyleSheet(
                    f"color:{RED}; font-size:11px; font-weight:700;")
        self._conn_thread.result.connect(_on_result)
        self._conn_thread.start()

    def _update_rr_label(self):
        rr = self.cfg_tp.value() / self.cfg_sl.value()
        col = GREEN if rr >= 1.5 else YELLOW if rr >= 1 else RED
        self.rr_lbl.setText(f"{rr:.2f}x")
        self.rr_lbl.setStyleSheet(
            f"color:{col}; font-family:{MONO_CSS}; font-weight:800; font-size:14px;")

    def _update_filter_label(self):
        pass

    def _apply_safety_config(self):
        SAFETY_CFG["signal_persistence"]          = self.sf_persistence.isChecked()
        SAFETY_CFG["btc_trend_check"]             = self.sf_btc_check.isChecked()
        SAFETY_CFG["btc_drop_pct"]                = self.sf_btc_drop.value()
        SAFETY_CFG["btc_drop_cooldown_mins"]      = self.sf_btc_cooldown_mins.value()
        SAFETY_CFG["btc_recovery_pct"]            = self.sf_btc_recovery.value()
        SAFETY_CFG["trend_1h_freshness"]          = self.sf_trend_freshness.isChecked()
        SAFETY_CFG["trend_1h_stale_pct"]          = self.sf_trend_stale.value()
        SAFETY_CFG["symbol_recovery_gate"]        = self.sf_sym_recovery.isChecked()
        SAFETY_CFG["symbol_recovery_pct"]         = self.sf_sym_recovery_pct.value()
        SAFETY_CFG["symbol_recovery_expiry_mins"] = self.sf_sym_expiry.value()
        SAFETY_CFG["coin_trend_check"]            = self.sf_coin_check.isChecked()
        SAFETY_CFG["coin_drop_pct"]               = self.sf_coin_drop.value()
        SAFETY_CFG["max_open_trades"]             = self.sf_max_trades.isChecked()
        SAFETY_CFG["max_open_trades_count"]       = self.sf_max_trades_n.value()
        SAFETY_CFG["daily_loss_limit"]            = self.sf_daily_loss.isChecked()
        SAFETY_CFG["daily_loss_amount"]           = self.sf_daily_loss_n.value()

    def _apply_config(self):
        global FONT_SIZE
        CFG["max_price"]            = self.cfg_max_price.value()
        CFG["min_volume_usdt"]      = self.cfg_min_vol.value()
        CFG["interval"]             = self.cfg_interval.currentText()
        CFG["top_n"]                = self.cfg_top_n.value()
        # Update subtitle to reflect new scan filters
        if hasattr(self, '_subtitle_lbl'):
            _v = CFG['min_volume_usdt']
            _vstr = f"${_v/1_000_000:.0f}M" if _v >= 1_000_000 else f"${_v:,.0f}"
            self._subtitle_lbl.setText(
                f"Binance Spot  ·  Price < ${CFG['max_price']:.0f}  ·  "
                f"Vol > {_vstr}  ·  {CFG['interval']}")
        CFG["picks_n"]              = self.cfg_picks_n.value()
        CFG["candle_limit"]         = self.cfg_candles.value()
        CFG["new_listing_filter"]   = self.cfg_new_listing.isChecked()
        CFG["new_listing_min_days"] = self.cfg_new_listing_min.value()
        CFG["new_listing_max_days"] = self.cfg_new_listing_max.value()
        # Parse blocklist — one symbol per line, USDT suffix optional
        _bl_lines = [l.strip().upper() for l in self.cfg_blocklist.toPlainText().splitlines() if l.strip()]
        CFG["symbol_blocklist"] = {(s if s.endswith("USDT") else s + "USDT") for s in _bl_lines}
        CFG["sl_pct"]          = self.cfg_sl.value()
        CFG["tp_pct"]          = self.cfg_tp.value()
        CFG["tp2_pct"]         = self.cfg_tp2.value()

        new_fs = self.cfg_font_size.value()
        if new_fs != FONT_SIZE:
            FONT_SIZE = new_fs
            QApplication.instance().setStyleSheet(make_stylesheet(FONT_SIZE))
            self._settings.setValue("fontSize", FONT_SIZE)

        global BROWSER_PATH
        BROWSER_PATH = self.cfg_browser.text().strip()
        self._settings.setValue("browserPath", BROWSER_PATH)

        TRADING_CFG["api_key"]     = self.cfg_api_key.text().strip()
        TRADING_CFG["api_secret"]  = self.cfg_api_secret.text().strip()
        TRADING_CFG["oco_enabled"] = self.cfg_oco.isChecked()
        self._settings.setValue("tradingApiKey",    TRADING_CFG["api_key"])
        self._settings.setValue("tradingApiSecret", TRADING_CFG["api_secret"])
        self._settings.setValue("tradingTestnet",   TRADING_CFG["testnet"])
        self._settings.setValue("tradingOco",       TRADING_CFG["oco_enabled"])

        self._settings.setValue("topN",   CFG["top_n"])
        self._settings.setValue("picksN", CFG["picks_n"])

        self.statusBar().showMessage(
            f"Config applied — font {FONT_SIZE}px  |  press Scan to refresh")


    def _restore_settings(self):
        global FONT_SIZE, BROWSER_PATH
        s = self._settings

        saved_fs = s.value("fontSize")
        if saved_fs is not None:
            FONT_SIZE = int(saved_fs)
            self.cfg_font_size.setValue(FONT_SIZE)
            QApplication.instance().setStyleSheet(make_stylesheet(FONT_SIZE))

        saved_bp = s.value("browserPath")
        if saved_bp is not None:
            BROWSER_PATH = str(saved_bp)
            self.cfg_browser.setText(BROWSER_PATH)

        for k in SAFETY_CFG:
            val = s.value(f"safety_{k}")
            if val is not None:
                if isinstance(SAFETY_CFG[k], bool):
                    SAFETY_CFG[k] = val in (True, "true", "True", "1")
                elif isinstance(SAFETY_CFG[k], float):
                    try: SAFETY_CFG[k] = float(val)
                    except (ValueError, TypeError): pass
                elif isinstance(SAFETY_CFG[k], int):
                    try: SAFETY_CFG[k] = int(val)
                    except (ValueError, TypeError): pass
        if hasattr(self, "sf_persistence"):
            self.sf_persistence.setChecked(SAFETY_CFG["signal_persistence"])
            self.sf_btc_check.setChecked(SAFETY_CFG["btc_trend_check"])
            self.sf_btc_drop.setValue(SAFETY_CFG["btc_drop_pct"])
            self.sf_btc_cooldown_mins.setValue(SAFETY_CFG.get("btc_drop_cooldown_mins", 60))
            self.sf_btc_recovery.setValue(SAFETY_CFG.get("btc_recovery_pct", 1.5))
            self.sf_trend_freshness.setChecked(SAFETY_CFG.get("trend_1h_freshness", True))
            self.sf_trend_stale.setValue(SAFETY_CFG.get("trend_1h_stale_pct", 1.5))
            self.sf_sym_recovery.setChecked(SAFETY_CFG.get("symbol_recovery_gate", True))
            self.sf_sym_recovery_pct.setValue(SAFETY_CFG.get("symbol_recovery_pct", 1.0))
            self.sf_sym_expiry.setValue(SAFETY_CFG.get("symbol_recovery_expiry_mins", 30))
            self.sf_coin_check.setChecked(SAFETY_CFG["coin_trend_check"])
            self.sf_coin_drop.setValue(SAFETY_CFG["coin_drop_pct"])
            self.sf_max_trades.setChecked(SAFETY_CFG["max_open_trades"])
            self.sf_max_trades_n.setValue(SAFETY_CFG["max_open_trades_count"])
            self.sf_daily_loss.setChecked(SAFETY_CFG["daily_loss_limit"])
            self.sf_daily_loss_n.setValue(SAFETY_CFG["daily_loss_amount"])

        self._load_alerts()

        tk = s.value("tradingApiKey")
        ts = s.value("tradingApiSecret")
        tt = s.value("tradingTestnet")
        to = s.value("tradingOco")
        if tk is not None:
            TRADING_CFG["api_key"] = str(tk)
            self.cfg_api_key.setText(TRADING_CFG["api_key"])
        if ts is not None:
            TRADING_CFG["api_secret"] = str(ts)
            self.cfg_api_secret.setText(TRADING_CFG["api_secret"])
        if tt is not None:
            TRADING_CFG["testnet"] = tt in (True, "true", "True", "1")
            self.cfg_testnet.setChecked(TRADING_CFG["testnet"])
            self._refresh_trading_mode_btn()
            self.cfg_live_warning.setVisible(not TRADING_CFG["testnet"])
            self._refresh_live_banner()
        if to is not None:
            TRADING_CFG["oco_enabled"] = to in (True, "true", "True", "1")
            self.cfg_oco.setChecked(TRADING_CFG["oco_enabled"])

        try:
            TRADING_CFG["api_key"]    = self.cfg_api_key.text().strip()
            TRADING_CFG["api_secret"] = self.cfg_api_secret.text().strip()
        except Exception:
            pass

        def _load(key, widget, cast, cfg_key=None):
            v = s.value(key)
            if v is not None:
                try:
                    val = cast(v)
                    widget.setValue(val) if hasattr(widget, 'setValue') else widget.setCurrentText(val)
                    if cfg_key:
                        CFG[cfg_key] = val
                except Exception:
                    pass

        _load("topN",     self.cfg_top_n,     int,   "top_n")
        _load("picksN",   self.cfg_picks_n,   int,   "picks_n")
        _load("maxPrice", self.cfg_max_price, float, "max_price")
        _load("minVol",   self.cfg_min_vol,   float, "min_volume_usdt")
        _load("candles",  self.cfg_candles,   int,   "candle_limit")
        _load("newListingMinDays", self.cfg_new_listing_min, int,  "new_listing_min_days")
        _load("newListingMaxDays", self.cfg_new_listing_max, int,  "new_listing_max_days")
        try:
            v = s.value("newListingFilter")
            if v is not None:
                self.cfg_new_listing.setChecked(str(v).lower() in ("true", "1"))
                CFG["new_listing_filter"] = self.cfg_new_listing.isChecked()
        except Exception:
            pass
        _load("slPct",    self.cfg_sl,        float, "sl_pct")
        _load("tpPct",    self.cfg_tp,        float, "tp_pct")
        _load("tp2Pct",   self.cfg_tp2,       float, "tp2_pct")

        # Restore symbol blocklist
        try:
            raw_bl = s.value("symbolBlocklist")
            if raw_bl is not None:
                lines = [l.strip().upper() for l in str(raw_bl).splitlines() if l.strip()]
                CFG["symbol_blocklist"] = {(l if l.endswith("USDT") else l + "USDT") for l in lines}
                self.cfg_blocklist.setPlainText(
                    "\n".join(sorted(sym.replace("USDT", "") for sym in CFG["symbol_blocklist"])))
        except Exception:
            pass

        # Restore new alert filter settings
        try:
            v = s.value("alert_block_doji")
            if v is not None:
                val = str(v).lower() in ("true", "1")
                ALERT_CFG["block_doji"] = val
                self.al_block_doji.setChecked(val)
            v = s.value("alert_block_neutral_pattern")
            if v is not None:
                val = str(v).lower() in ("true", "1")
                ALERT_CFG["block_neutral_pattern"] = val
                self.al_block_neutral_pat.setChecked(val)
            v = s.value("alert_squeeze_exempt_width")
            if v is not None:
                val = float(v)
                ALERT_CFG["squeeze_exempt_bb_width"] = val
                self.al_squeeze_exempt_width.setValue(val)
        except Exception:
            pass

        iv = s.value("interval")
        if iv is not None:
            self.cfg_interval.setCurrentText(str(iv))
            CFG["interval"] = str(iv)

        geo = s.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            w = int(screen.width()  * 0.85)
            h = int(screen.height() * 0.85)
            self.resize(w, h)
            self.move(
                screen.x() + (screen.width()  - w) // 2,
                screen.y() + (screen.height() - h) // 2)
        state = s.value("windowState")
        if state:
            self.restoreState(state)
        QTimer.singleShot(0, self._reflow_columns)

        for k, default in ALERT_CFG.items():
            saved = s.value(f"alert_{k}")
            if saved is not None:
                try:
                    if isinstance(default, bool):
                        ALERT_CFG[k] = saved in (True, "true", "True", "1")
                    elif isinstance(default, int):
                        ALERT_CFG[k] = int(saved)
                    elif isinstance(default, float):
                        ALERT_CFG[k] = float(saved)
                    else:
                        ALERT_CFG[k] = str(saved)
                except Exception:
                    pass
        try:
            self.al_enabled.setChecked(ALERT_CFG["enabled"])
            self.al_interval.setValue(int(ALERT_CFG["interval_sec"]))
            if hasattr(self, "al_max_rsi"):
                self.al_max_rsi.setValue(int(ALERT_CFG.get("max_rsi", 70)))
                self.al_max_bb.setValue(int(ALERT_CFG.get("max_bb_pct", 80)))
                self.al_vol_spike.setChecked(ALERT_CFG.get("require_vol_spike", False))
                self.al_min_adr.setValue(float(ALERT_CFG.get("min_adr_pct", 0.5)))
                self.al_block_downtrend.setChecked(ALERT_CFG.get("block_downtrend", True))
                self.al_block_1h_downtrend.setChecked(ALERT_CFG.get("block_1h_downtrend", True))
                self.al_min_vol_ratio.setValue(float(ALERT_CFG.get("min_vol_ratio", 0.8)))
                self.al_spike_cooldown.setChecked(ALERT_CFG.get("spike_cooldown", True))
                self.al_spike_cooldown_pct.setValue(float(ALERT_CFG.get("spike_pct", 15.0)))
                self.al_crash_cooldown.setChecked(ALERT_CFG.get("crash_cooldown", True))
                self.al_crash_cooldown_pct.setValue(float(ALERT_CFG.get("crash_pct", 8.0)))
                self.al_crash_cooldown_mins.setValue(int(ALERT_CFG.get("crash_cooldown_mins", 60)))
                self.al_require_macd.setChecked(ALERT_CFG.get("require_macd_rising", False))
                self.al_coin_cooldown.setChecked(ALERT_CFG.get("coin_cooldown", True))
                self.al_coin_cooldown_mins.setValue(int(ALERT_CFG.get("coin_cooldown_mins", 30)))
                self.al_block_doji.setChecked(ALERT_CFG.get("block_doji", True))
                self.al_block_neutral_pat.setChecked(ALERT_CFG.get("block_neutral_pattern", True))
                self.al_squeeze_exempt_width.setValue(float(ALERT_CFG.get("squeeze_exempt_bb_width", 2.0)))
            self.al_min_signal.setCurrentText(ALERT_CFG["min_signal"])
            self.al_min_pot.setValue(int(ALERT_CFG["min_potential"]))
            self.al_min_exp.setValue(float(ALERT_CFG["min_exp_move"]))
            self.al_sound.setChecked(ALERT_CFG["sound"])
            self.al_desktop.setChecked(ALERT_CFG["desktop"])
            self.al_tg.setChecked(ALERT_CFG["telegram"])
            self.al_tg_token.setText(ALERT_CFG["tg_token"])
            self.al_tg_chat.setText(ALERT_CFG["tg_chat_id"])
            self.al_wa.setChecked(ALERT_CFG["whatsapp"])
            self.al_wa_number.setText(ALERT_CFG["wa_number"])
            self.al_wa_queue.setText(ALERT_CFG["picoclaw_queue"])
        except Exception:
            pass

        # Restore surge detector settings
        try:
            for k, default in SURGE_CFG.items():
                val = s.value(f"surge_{k}")
                if val is not None:
                    if isinstance(default, bool):
                        SURGE_CFG[k] = val in (True, "true", "True", "1")
                    elif isinstance(default, float):
                        SURGE_CFG[k] = float(val)
                    elif isinstance(default, int):
                        SURGE_CFG[k] = int(float(val))
            self.sg_enabled.setChecked(SURGE_CFG.get("enabled", True))
            self.sg_vol_5m.setValue(float(SURGE_CFG.get("vol_5m_mult", 3.0)))
            self.sg_max_chg.setValue(float(SURGE_CFG.get("max_price_pct", 30.0)))
            self.sg_min_chg.setValue(float(SURGE_CFG.get("min_price_pct", 0.5)))
            self.sg_min_vol.setValue(float(SURGE_CFG.get("min_vol_usdt", 500_000)))
            self.sg_interval.setValue(int(SURGE_CFG.get("interval_sec", 30)))
            self.sg_max_cand.setValue(int(SURGE_CFG.get("max_candidates", 10)))
            self.sg_cooldown.setValue(int(SURGE_CFG.get("cooldown_mins", 60)))
        except Exception:
            pass

        # Refresh subtitle to reflect restored CFG values
        if hasattr(self, '_subtitle_lbl'):
            _v = CFG['min_volume_usdt']
            _vstr = f"${_v/1_000_000:.0f}M" if _v >= 1_000_000 else f"${_v:,.0f}"
            self._subtitle_lbl.setText(
                f"Binance Spot  ·  Price < ${CFG['max_price']:.0f}  ·  "
                f"Vol > {_vstr}  ·  {CFG['interval']}")

    def _save_settings(self):
        s = self._settings
        s.setValue("geometry",    self.saveGeometry())
        s.setValue("windowState", self.saveState())
        s.setValue("fontSize",    FONT_SIZE)
        s.setValue("browserPath", BROWSER_PATH)
        s.setValue("tradingApiKey",    TRADING_CFG["api_key"])
        s.setValue("tradingApiSecret", TRADING_CFG["api_secret"])
        s.setValue("tradingTestnet",   TRADING_CFG["testnet"])
        s.setValue("tradingOco",       TRADING_CFG["oco_enabled"])
        try:
            self._apply_safety_config()
        except Exception:
            pass
        for k, v in SAFETY_CFG.items():
            s.setValue(f"safety_{k}", v)
        try:
            s.setValue("topN",    self.cfg_top_n.value())
            s.setValue("picksN",  self.cfg_picks_n.value())
            s.setValue("maxPrice", self.cfg_max_price.value())
            s.setValue("minVol",   self.cfg_min_vol.value())
            s.setValue("interval", self.cfg_interval.currentText())
            s.setValue("candles",  self.cfg_candles.value())
            s.setValue("newListingFilter",   self.cfg_new_listing.isChecked())
            s.setValue("newListingMinDays",  self.cfg_new_listing_min.value())
            s.setValue("newListingMaxDays",  self.cfg_new_listing_max.value())
            s.setValue("symbolBlocklist",    self.cfg_blocklist.toPlainText())
            s.setValue("slPct",    self.cfg_sl.value())
            s.setValue("tpPct",    self.cfg_tp.value())
            s.setValue("tp2Pct",   self.cfg_tp2.value())
        except Exception:
            pass
        # Save new alert filter settings
        try:
            s.setValue("alert_block_doji",            self.al_block_doji.isChecked())
            s.setValue("alert_block_neutral_pattern", self.al_block_neutral_pat.isChecked())
            s.setValue("alert_squeeze_exempt_width",  self.al_squeeze_exempt_width.value())
        except Exception:
            pass
        self._save_alerts()
        s.sync()

    def _refresh_alert_toggle(self):
        if not hasattr(self, 'cfg_alert_enabled'):
            return
        on = ALERT_CFG["enabled"]
        self.cfg_alert_enabled.setChecked(on)
        if on:
            self.cfg_alert_enabled.setText("🔔  Alerts are ON  —  click to disable")
            self.cfg_alert_enabled.setStyleSheet(
                f"background:#003a1a; color:{GREEN}; border:1px solid {GREEN}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;")
        else:
            self.cfg_alert_enabled.setText("🔕  Alerts are OFF  —  click to enable")
            self.cfg_alert_enabled.setStyleSheet(
                f"background:#2a0000; color:{RED}; border:1px solid {RED}; "
                f"border-radius:4px; font-size:12px; font-weight:700; padding:0 14px;")

    def _on_alert_toggle(self):
        ALERT_CFG["enabled"] = self.cfg_alert_enabled.isChecked()
        self._refresh_alert_toggle()
        if hasattr(self, 'al_enabled'):
            self.al_enabled.setChecked(ALERT_CFG["enabled"])
        state = "ON" if ALERT_CFG["enabled"] else "OFF"
        self.statusBar().showMessage(f"Alerts turned {state}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'table'):
            QTimer.singleShot(0, self._reflow_columns)

    def _reset_column_widths(self):
        for i in range(15):
            self._settings.remove(f"col_{i}")
        self._reflow_columns()
        self.statusBar().showMessage("Column widths reset to auto")

    def keyPressEvent(self, event):
        key  = event.key()
        mods = event.modifiers()
        if key == Qt.Key.Key_Q and (mods == Qt.KeyboardModifier.NoModifier or
                                     mods == Qt.KeyboardModifier.ControlModifier):
            self.close()
        elif key == Qt.Key.Key_Escape:
            event.ignore()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._alert_engine.stop()
        self._surge_detector.stop()
        self._trades_refresh_timer.stop()
        self._dot_blink_timer.stop()
        self._alert_pnl_timer.stop()
        _outcome_tracker.stop()
        if self._ws_feed:
            self._ws_feed.stop()
        self._save_settings()
        super().closeEvent(event)

    def _show_status(self, msg, timeout_ms=10000):
        self.statusBar().showMessage(msg, timeout_ms)

    def _setup_timer(self):
        self._trades_refresh_timer = QTimer()
        self._trades_refresh_timer.setInterval(3000)
        self._trades_refresh_timer.timeout.connect(self._fetch_open_trade_prices)

        self._dot_blink_state = True
        self._dot_blink_timer = QTimer()
        self._dot_blink_timer.setInterval(500)
        self._dot_blink_timer.timeout.connect(self._blink_dot)

        # Fallback timer: refreshes alert P&L for any symbols the WS
        # hasn't ticked yet (e.g. very low-volume coins). WS handles
        # live updates per-tick; this is a 10s safety net only.
        self._alert_pnl_timer = QTimer()
        self._alert_pnl_timer.setInterval(10000)
        self._alert_pnl_timer.timeout.connect(self._update_alert_pnl)
        self._alert_pnl_timer.start()

    def _blink_dot(self):
        self._dot_blink_state = not self._dot_blink_state
        color = "#00aaff" if self._dot_blink_state else "#004488"
        self._scan_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    def _set_dot_scanning(self):
        self._dot_blink_timer.start()
        self._scan_dot.setToolTip("Scanning…")

    def _set_dot_idle(self, coin_count=None):
        self._dot_blink_timer.stop()
        self._scan_dot.setStyleSheet("color: #00cc66; font-size: 14px;")
        tip = f"Last scan: {coin_count} coins" if coin_count else "Scanner idle"
        self._scan_dot.setToolTip(tip)

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("⏳")
        self._set_dot_scanning()
        self.table.setRowCount(0)
        self.statusBar().showMessage("Fetching tickers...")

        self._scanner = Scanner()
        self._worker  = ScanWorker(self._scanner)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, done, total, status):
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(done)
        self.statusBar().showMessage(status[:80])

    def _on_finished(self, results):
        self._results = results
        self._refresh_display()
        self._populate_picks(results)
        self._check_sltp_hits(results)
        self._refresh_trades_table()
        self._refresh_balance_display()
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("⚡")
        self._set_dot_idle(len(results))
        threading.Thread(
            target=log_scan_results,
            args=(results,),
            kwargs={"trades": self._trades},
            daemon=True
        ).start()
        n = len(results)
        self._clear_status_alert()
        self.statusBar().showMessage(f"Scan complete — {n} coins analysed  [{datetime.now().strftime('%H:%M:%S')}]")

    def _refresh_display(self):
        if self._sort_col is not None and self._results:
            key_fn = self._SORT_KEY.get(self._sort_col)
            if key_fn:
                self._results = sorted(
                    self._results,
                    key=lambda r: key_fn(r, 0),
                    reverse=not self._sort_asc)
        self._populate_table(self._results)
        if self._sort_col is not None:
            self._update_header_arrows()

    def _on_error(self, msg):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("⚡")
        self.progress.setVisible(False)
        self.statusBar().showMessage(f"Error: {msg}")

    _SORT_KEY = {
        0:  lambda r, i: i,
        1:  lambda r, i: r["symbol"],
        2:  lambda r, i: r["price"],
        3:  lambda r, i: r["change_24h"],
        4:  lambda r, i: r["rsi"],
        5:  lambda r, i: r["stoch_rsi"],
        6:  lambda r, i: r["macd_hist"],
        7:  lambda r, i: (
                (r["price"] - r["bb_lower"]) / (r["bb_upper"] - r["bb_lower"]) * 100
                if r.get("bb_upper") and r.get("bb_lower") and r["bb_upper"] != r["bb_lower"]
                else 50.0),
        8:  lambda r, i: r["volume_24h"],
        9:  lambda r, i: {"PRE-BREAKOUT": 0, "STRONG BUY": 1, "BUY": 2,
                           "NEUTRAL": 3, "SELL": 4, "STRONG SELL": 5}.get(r["signal"], 2),
        10: lambda r, i: r.get("potential", 0),
        11: lambda r, i: r.get("expected_move", 0),
        12: lambda r, i: r.get("long_score", 0) - r.get("short_score", 0),
        13: lambda r, i: r["pattern"],
        15: lambda r, i: (datetime.now() - r["signal_age"]).total_seconds()
                         if r.get("signal_age") and r["signal"] != "NEUTRAL" else 99999,
        16: lambda r, i: r.get("signal_conf", 0),
        17: lambda r, i: {"up": 0, "flat": 1, "down": 2}.get(r.get("trend_1h", "flat"), 1),
    }

    def _on_header_clicked(self, col):
        if col in (14, 18):
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._apply_sort()
        self._update_header_arrows()

    def _apply_sort(self):
        if self._sort_col is None or not self._results:
            return
        self._refresh_display()

    def _update_header_arrows(self):
        cols = ["#", "Symbol", "Price", "24h%", "RSI", "StRSI",
                "MACD", "BB%", "Vol 24h", "Signal", "Pot%", "Exp%", "L/S", "Pattern", "Chart",
                "AGE", "CONF", "1H"]
        for i, base in enumerate(cols):
            if i == self._sort_col:
                arrow = " ▲" if self._sort_asc else " ▼"
                self.table.horizontalHeaderItem(i).setText(base + arrow)
            else:
                self.table.horizontalHeaderItem(i).setText(base)


    def _populate_table(self, results):
        try:
            self._do_populate_table(results)
        except Exception:
            import traceback; traceback.print_exc()

    def _do_populate_table(self, results):
        self.table.setRowCount(0)

        for idx, r in enumerate(results):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 38)

            sig   = r["signal"]
            chg   = r["change_24h"]
            rsi   = r["rsi"]
            srsi  = r["stoch_rsi"]
            mh    = r["macd_hist"]
            pot   = r.get("potential", 0)
            exp   = r.get("expected_move", 0)
            vol_m = r["volume_24h"] / 1_000_000
            sym   = r["symbol"].replace("USDT", "")

            if   sig == "STRONG BUY":  row_bg = QColor(STRONG_BUY_BG)
            elif sig == "STRONG SELL": row_bg = QColor(STRONG_SELL_BG)
            elif sig == "BUY":         row_bg = QColor(BUY_BG)
            elif sig == "SELL":        row_bg = QColor(SELL_BG)
            else:                      row_bg = None

            def cell(text, color=WHITE, bold=False,
                     align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                     sort_val=None):
                item = QTableWidgetItem(str(text))
                item.setForeground(QBrush(QColor(color)))
                if row_bg:
                    item.setBackground(QBrush(row_bg))
                if bold:
                    f = item.font(); f.setBold(True); item.setFont(f)
                item.setTextAlignment(align)
                if sort_val is not None:
                    item.setData(Qt.ItemDataRole.UserRole, sort_val)
                return item

            left = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft

            bb_num = 50.0
            bb_pos = "—"
            if r.get("bb_upper") and r.get("bb_lower") and r["bb_upper"] != r["bb_lower"]:
                bb_num = (r["price"] - r["bb_lower"]) / (r["bb_upper"] - r["bb_lower"]) * 100
                bb_pos = f"{bb_num:.0f}%"

            self.table.setItem(row, 0,  cell(str(idx+1), DIM, sort_val=idx))
            self.table.setItem(row, 1,  cell(sym, ACCENT, bold=True, align=left, sort_val=sym))
            self.table.setItem(row, 2,  cell(f"${r['price']:.5f}", WHITE, sort_val=r["price"]))
            self.table.setItem(row, 3,  cell(f"{chg:+.1f}%", GREEN if chg >= 0 else RED, sort_val=chg))
            self.table.setItem(row, 4,  cell(f"{rsi:.1f}", GREEN if rsi < 40 else RED if rsi > 60 else YELLOW, sort_val=rsi))
            self.table.setItem(row, 5,  cell(f"{srsi:.1f}", GREEN if srsi < 30 else RED if srsi > 70 else YELLOW, sort_val=srsi))
            self.table.setItem(row, 6,  cell("▲" if mh > 0 else "▼", GREEN if mh > 0 else RED, sort_val=mh))
            self.table.setItem(row, 7,  cell(bb_pos, YELLOW, sort_val=bb_num))
            self.table.setItem(row, 8,  cell(f"${vol_m:.1f}M", ACCENT, sort_val=r["volume_24h"]))

            sig_tier = {"PRE-BREAKOUT": 0, "STRONG BUY": 1, "BUY": 2,
                        "NEUTRAL": 3, "SELL": 4, "STRONG SELL": 5}.get(sig, 3)
            sig_item = QTableWidgetItem(sig)
            sig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sig_c = GREEN if "BUY" in sig else RED if "SELL" in sig else DIM
            sig_item.setForeground(QBrush(QColor(sig_c)))
            if row_bg: sig_item.setBackground(QBrush(row_bg))
            f = sig_item.font(); f.setBold("STRONG" in sig); sig_item.setFont(f)
            sig_item.setData(Qt.ItemDataRole.UserRole, sig_tier)
            ctx_reason = r.get("ctx_reason", "")
            ctx_blocked = r.get("ctx_blocked", False)
            if ctx_reason:
                prefix = "Blocked: " if ctx_blocked else "Context: "
                sig_item.setToolTip(prefix + ctx_reason)
            self.table.setItem(row, 9, sig_item)

            pot_c = GREEN if pot >= 70 else YELLOW if pot >= 40 else RED
            exp_c = GREEN if exp >= 8  else GREEN  if exp >= 5  else YELLOW
            ls_val = r.get("long_score", 0) - r.get("short_score", 0)
            self.table.setItem(row, 10, cell(f"{pot}%",   pot_c, sort_val=pot))
            self.table.setItem(row, 11, cell(f"{exp:.1f}%", exp_c, sort_val=exp))
            self.table.setItem(row, 12, cell(f"L{r.get('long_score',0)}/S{r.get('short_score',0)}", DIM, sort_val=ls_val))
            self.table.setItem(row, 13, cell(r["pattern"], DIM, align=left, sort_val=r["pattern"]))

            candles = r.get("candles", [])
            if candles:
                closes   = [c["close"] for c in candles[-20:]]
                trend_up = closes[-1] > closes[0]
                spark    = Sparkline(closes, GREEN if trend_up else RED)
                self.table.setCellWidget(row, 14, spark)

            sig_age_dt = r.get("signal_age")
            if sig_age_dt and sig != "NEUTRAL":
                age_secs = int((datetime.now() - sig_age_dt).total_seconds())
                if age_secs < 60:
                    age_str = f"{age_secs}s"
                    age_col = GREEN if age_secs < 30 else YELLOW
                else:
                    age_mins = age_secs // 60
                    age_str  = f"{age_mins}m"
                    age_col  = YELLOW if age_mins < 5 else RED
                age_sort = age_secs
            else:
                age_str, age_col, age_sort = "—", DIM, 99999
            self.table.setItem(row, 15, cell(age_str, age_col, sort_val=age_sort))

            conf = r.get("signal_conf", 0) if sig != "NEUTRAL" else 0
            if conf == 0:
                conf_str = "—";      conf_col = DIM
            elif conf == 1:
                conf_str = "▮░░░░";  conf_col = YELLOW
            elif conf == 2:
                conf_str = "▮▮░░░";  conf_col = YELLOW
            elif conf == 3:
                conf_str = "▮▮▮░░";  conf_col = GREEN
            elif conf == 4:
                conf_str = "▮▮▮▮░";  conf_col = GREEN
            else:
                conf_str = "▮▮▮▮▮";  conf_col = ACCENT
            self.table.setItem(row, 16, cell(conf_str, conf_col, sort_val=conf))

            trend_1h = r.get("trend_1h", "flat")
            if trend_1h == "up":
                t1h_str = "↑"
                t1h_col = GREEN if "BUY" in sig else (RED if "SELL" in sig else ACCENT)
                t1h_sort = 0
            elif trend_1h == "down":
                t1h_str = "↓"
                t1h_col = RED if "BUY" in sig else (GREEN if "SELL" in sig else RED)
                t1h_sort = 2
            else:
                t1h_str = "→"
                t1h_col = DIM
                t1h_sort = 1
            self.table.setItem(row, 17, cell(t1h_str, t1h_col,
                align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter,
                sort_val=t1h_sort))

        if self._sort_col is not None:
            self._update_header_arrows()


    def _populate_picks(self, results):
        while self.picks_lay.count():
            item = self.picks_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        n      = CFG.get("picks_n", 5)
        pre_bo = [r for r in results if r["signal"] == "PRE-BREAKOUT"][:n]
        buys   = [r for r in results if r["signal"] in ("STRONG BUY", "BUY")][:n]
        sells  = [r for r in results if r["signal"] in ("STRONG SELL", "SELL")][:n]

        for section_name, section_results, color, is_long in [
            ("⚡  PRE-BREAKOUT",     pre_bo, "#ff9900", True),
            ("🟢  LONG CANDIDATES",  buys,   GREEN,     True),
            ("🔴  SHORT CANDIDATES", sells,  RED,       False),
        ]:
            if not section_results:
                continue
            lbl = QLabel(section_name)
            lbl.setStyleSheet(f"color:{color}; font-size:15px; font-weight:800; padding:8px 0 4px 0;")
            self.picks_lay.addWidget(lbl)
            for r in section_results:
                self.picks_lay.addWidget(self._build_pick_card(r, is_long))

        self.picks_lay.addStretch()

    def _build_pick_card(self, r, is_long):
        price    = r["price"]
        sl_pct   = CFG["sl_pct"]
        tp_pct   = CFG["tp_pct"]
        tp2_pct  = CFG["tp2_pct"]
        sl       = round(price * (1 - sl_pct/100)  if is_long else price * (1 + sl_pct/100),  8)
        tp1      = round(price * (1 + tp_pct/100)  if is_long else price * (1 - tp_pct/100),  8)
        tp2      = round(price * (1 + tp2_pct/100) if is_long else price * (1 - tp2_pct/100), 8)
        rr       = round(tp_pct / sl_pct, 2)
        accent   = GREEN if is_long else RED
        sym      = r["symbol"].replace("USDT", "/USDT")
        sym_bare = r["symbol"].replace("USDT", "")
        pot      = r.get("potential", 0)
        exp      = r.get("expected_move", 0)
        sig      = r["signal"]
        rsi      = r.get("rsi", 50)
        strsi    = r.get("stoch_rsi", 50)
        bb_pct   = r.get("bb_pct", 50)
        macd_h   = r.get("macd_hist", 0)
        chg24    = r.get("change_24h", 0)
        vol_r    = r.get("vol_ratio", 1)
        pattern  = r.get("pattern", "—")
        trend_1h = r.get("trend_1h", "flat")
        conf     = r.get("signal_conf", 1)
        age_dt   = r.get("signal_age")
        support  = r.get("support", 0)
        resist   = r.get("resist", 0)
        lscore   = r.get("long_score", 0)
        sscore   = r.get("short_score", 0)
        total_sc = lscore + sscore
        win_sc   = lscore if is_long else sscore

        if age_dt and sig != "NEUTRAL":
            secs = int((datetime.now() - age_dt).total_seconds())
            age_str = f"{secs}s" if secs < 60 else f"{secs//60}m{secs%60:02d}s"
            age_col = GREEN if secs < 30 else (YELLOW if secs < 300 else RED)
        else:
            age_str, age_col = "—", DIM

        conf_filled = min(conf, 5)
        conf_bar    = "▮" * conf_filled + "░" * (5 - conf_filled)
        conf_col    = ACCENT if conf >= 5 else (GREEN if conf >= 3 else YELLOW)

        trend_sym = {"up": "↑", "down": "↓", "flat": "→"}.get(trend_1h, "→")
        if (is_long and trend_1h == "up") or (not is_long and trend_1h == "down"):
            trend_col = GREEN
        elif (is_long and trend_1h == "down") or (not is_long and trend_1h == "up"):
            trend_col = RED
        else:
            trend_col = DIM

        if support > 0 and price > 0:
            sup_pct = (price - support) / price * 100
            sup_str = f"-{sup_pct:.1f}%"
        else:
            sup_str = "—"
        if resist > 0 and price > 0:
            res_pct = (resist - price) / price * 100
            res_str = f"+{res_pct:.1f}%"
        else:
            res_str = "—"

        badge_colors = {
            "STRONG BUY":  ("#003322", "#00ff88"),
            "BUY":         ("#002211", "#00cc66"),
            "STRONG SELL": ("#330011", "#ff3366"),
            "SELL":        ("#220011", "#cc2244"),
            "NEUTRAL":     ("#1a2235", "#4a5568"),
        }

        class PickCard(QWidget):
            def __init__(self_):
                super().__init__()
                self_.setMinimumHeight(160)
                self_.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            def paintEvent(self_, event):
                p = QPainter(self_)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                W, H = self_.width(), self_.height()

                p.setPen(QPen(QColor(BORDER), 1))
                p.setBrush(QBrush(QColor(CARD)))
                p.drawRoundedRect(1, 1, W-2, H-2, 7, 7)

                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(accent)))
                p.drawRoundedRect(1, 1, 4, H-2, 2, 2)

                L = 14

                def txt(x, y, text, color, pt=10, bold=False, mono=False,
                        align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                        w=None, h=18):
                    f = mono_font(pt, bold) if mono else QFont()
                    if not mono:
                        f.setPointSize(pt); f.setBold(bold)
                    p.setFont(f)
                    p.setPen(QColor(color))
                    draw_w = (w if w is not None else W - x - 4)
                    fm = p.fontMetrics()
                    elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, draw_w)
                    p.drawText(x, y, draw_w, h, align, elided)

                def txt_w(text, pt=10, bold=False, mono=False):
                    f = mono_font(pt, bold) if mono else QFont()
                    if not mono:
                        f.setPointSize(pt); f.setBold(bold)
                    p.setFont(f)
                    return p.fontMetrics().horizontalAdvance(text)

                y1 = 12
                f_sym = mono_font(14, bold=True)
                p.setFont(f_sym)
                sym_w = p.fontMetrics().horizontalAdvance(sym)

                f_b = QFont(); f_b.setPointSize(8); f_b.setBold(True); p.setFont(f_b)
                btext = f" {sig} "
                bw = p.fontMetrics().horizontalAdvance(btext) + 4
                bh = 18

                pot_str = f"⚡{pot}%"
                exp_str = f"Exp {exp:.1f}%"
                chg_str = f"{'+'if chg24>=0 else ''}{chg24:.2f}% 24h"
                f_med = QFont(); f_med.setPointSize(10); f_med.setBold(True); p.setFont(f_med)
                pot_w  = p.fontMetrics().horizontalAdvance(pot_str) + 10
                exp_w  = p.fontMetrics().horizontalAdvance(exp_str) + 10
                chg_w  = p.fontMetrics().horizontalAdvance(chg_str) + 6

                badge_x = L + sym_w + 8
                pot_x   = badge_x + bw + 10
                exp_x   = pot_x + pot_w
                chg_x   = W - chg_w - 8

                ROW_H = 22
                ry = 6

                p.setFont(f_sym)
                p.setPen(QColor(ACCENT))
                p.drawText(L, ry, sym_w + 2, ROW_H,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sym)

                bg_c, fg_c = badge_colors.get(sig, ("#1a2235", "#4a5568"))
                p.setBrush(QBrush(QColor(bg_c))); p.setPen(QPen(QColor(fg_c), 1))
                p.drawRoundedRect(badge_x, ry + 2, bw, bh - 4, 3, 3)
                p.setFont(f_b); p.setPen(QColor(fg_c))
                p.drawText(badge_x, ry + 2, bw, bh - 4, Qt.AlignmentFlag.AlignCenter, btext)

                pot_col = "#00ff88" if pot >= 70 else (YELLOW if pot >= 40 else DIM)
                p.setFont(f_med); p.setPen(QColor(pot_col))
                p.drawText(pot_x, ry, pot_w, ROW_H,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pot_str)

                exp_col = GREEN if exp >= 3 else DIM
                p.setPen(QColor(exp_col))
                p.drawText(exp_x, ry, exp_w, ROW_H,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, exp_str)

                chg_col = GREEN if chg24 >= 0 else RED
                p.setFont(f_med)
                chg_w = p.fontMetrics().horizontalAdvance(chg_str) + 6
                chg_x = W - chg_w - 8
                p.setPen(QColor(chg_col))
                p.drawText(chg_x, ry, chg_w, ROW_H,
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, chg_str)

                y2 = ry + ROW_H + 4
                bar_h = 6
                bar_r = 3

                score_w = 90
                txt(L, y2, "Score", DIM, 8)
                bx = L + 38
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(bx, y2+2, score_w, bar_h, bar_r, bar_r)
                fill_w = int(min(win_sc, 10) / 10 * score_w)
                p.setBrush(QBrush(QColor(accent)))
                p.drawRoundedRect(bx, y2+2, max(fill_w, 4), bar_h, bar_r, bar_r)
                txt(bx + score_w + 4, y2, f"{win_sc}/10", accent, 8, bold=True)

                rsi_x = bx + score_w + 44
                txt(rsi_x, y2, "RSI", DIM, 8)
                rbx = rsi_x + 24
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rbx, y2+2, 60, bar_h, bar_r, bar_r)
                rsi_col = GREEN if rsi < 40 else (RED if rsi > 60 else YELLOW)
                p.setBrush(QBrush(QColor(rsi_col)))
                p.drawRoundedRect(rbx, y2+2, int(rsi/100*60), bar_h, bar_r, bar_r)
                txt(rbx+63, y2, f"{rsi:.0f}", rsi_col, 8, bold=True)

                bb_x = rbx + 90
                txt(bb_x, y2, "BB%", DIM, 8)
                bbx = bb_x + 24
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(bbx, y2+2, 60, bar_h, bar_r, bar_r)
                bb_col = GREEN if bb_pct < 25 else (RED if bb_pct > 75 else YELLOW)
                p.setBrush(QBrush(QColor(bb_col)))
                p.drawRoundedRect(bbx, y2+2, int(bb_pct/100*60), bar_h, bar_r, bar_r)
                txt(bbx+63, y2, f"{bb_pct:.0f}", bb_col, 8, bold=True)

                f_sm = QFont(); f_sm.setPointSize(8); p.setFont(f_sm)
                fm_sm = p.fontMetrics()
                age_label  = f"Age: {age_str}"
                conf_label = f"Conf: {conf_bar}"
                aw = fm_sm.horizontalAdvance(age_label)
                cw = fm_sm.horizontalAdvance(conf_label)
                right_col_w = max(aw, cw) + 4
                rx2 = W - right_col_w - 8

                p.setPen(QColor(age_col))
                p.drawText(rx2, y2, right_col_w, 14,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, age_label)
                p.setPen(QColor(conf_col))
                p.drawText(rx2, y2 + 14, right_col_w, 14,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, conf_label)

                y3 = y2 + 20
                pill_h = 34
                pill_items = [
                    ("Entry", f"${price:.6f}", WHITE),
                    ("SL",    f"${sl:.6f}",    RED),
                    ("TP1",   f"${tp1:.6f}",   GREEN),
                    ("TP2",   f"${tp2:.6f}",   "#00cc66"),
                    ("R/R",   f"{rr:.2f}x",    YELLOW),
                    ("Sup",   sup_str,          "#00aaff"),
                    ("Res",   res_str,          "#ff6699"),
                ]

                px = L
                for plbl, pval, pcol in pill_items:
                    f_lbl = QFont(); f_lbl.setPointSize(7); p.setFont(f_lbl)
                    lw = p.fontMetrics().horizontalAdvance(plbl)
                    f_val = mono_font(9, bold=True); p.setFont(f_val)
                    vw = p.fontMetrics().horizontalAdvance(pval)
                    pw = max(lw, vw) + 14
                    if px + pw > W - 8:
                        break
                    p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(px, y3, pw, pill_h, 4, 4)
                    p.setFont(f_lbl); p.setPen(QColor(DIM))
                    p.drawText(px, y3+2, pw, 14, Qt.AlignmentFlag.AlignCenter, plbl)
                    p.setFont(f_val); p.setPen(QColor(pcol))
                    p.drawText(px, y3+16, pw, 16, Qt.AlignmentFlag.AlignCenter, pval)
                    px += pw + 5

                y4 = y3 + pill_h + 6
                macd_str  = f"MACD {'▲ Positive' if macd_h > 0 else '▼ Negative'}  ({macd_h:+.4f})"
                macd_col  = GREEN if macd_h > 0 else RED
                strsi_str = f"StRSI {strsi:.0f}"
                strsi_col = GREEN if strsi < 30 else (RED if strsi > 70 else YELLOW)

                txt(L, y4, macd_str,  macd_col,  9, bold=True)
                mx = L + txt_w(macd_str, 9, bold=True) + 14
                txt(mx, y4, strsi_str, strsi_col, 9, bold=True)

                trend_label = f"1H {trend_sym}  Vol"
                tl_w = txt_w(trend_label, 9) + 4
                vol_bar_w = 50
                gap = 6
                f_sm2 = QFont(); f_sm2.setPointSize(8); p.setFont(f_sm2)
                vol_val_str = f"{vol_r:.1f}x"
                vvw = p.fontMetrics().horizontalAdvance(vol_val_str) + 4

                right_block_x = W - tl_w - gap - vol_bar_w - gap - vvw - 8

                p.setFont(QFont())
                txt(right_block_x, y4, f"1H {trend_sym}", trend_col, 9)
                tl_actual = txt_w(f"1H {trend_sym}", 9) + 6
                txt(right_block_x + tl_actual, y4, "Vol", DIM, 9)
                vol_lbl_w = txt_w("Vol", 9) + 4

                vbx = right_block_x + tl_actual + vol_lbl_w
                p.setBrush(QBrush(QColor(DARK2))); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(vbx, y4+3, vol_bar_w, bar_h, bar_r, bar_r)
                vol_fill = min(int((vol_r / 5) * vol_bar_w), vol_bar_w)
                vol_col = "#00ff88" if vol_r >= 2 else (YELLOW if vol_r >= 1.2 else DIM)
                p.setBrush(QBrush(QColor(vol_col)))
                p.drawRoundedRect(vbx, y4+3, max(vol_fill, 3), bar_h, bar_r, bar_r)
                txt(vbx + vol_bar_w + 3, y4, vol_val_str, vol_col, 8, bold=True)

            def sizeHint(self_):
                from PyQt6.QtCore import QSize
                return QSize(500, 160)

            def minimumSizeHint(self_):
                from PyQt6.QtCore import QSize
                return QSize(400, 160)

        return PickCard()

    def _on_row_double_click(self, item):
        row = self.table.currentRow()
        if 0 <= row < len(self._results):
            self._show_detail_popup(self._results[row])

    def _show_detail_popup(self, r):
        sig    = r["signal"]
        is_buy = "BUY" in sig
        accent = GREEN if is_buy else RED

        dlg = QDialog(self)
        dlg.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.setModal(False)

        outer = QFrame(dlg)
        outer.setObjectName("detailPopup")
        outer.setStyleSheet(f"""
            QFrame#detailPopup {{
                background: {DARK2};
                border: 2px solid {accent};
                border-radius: 12px;
            }}
        """)

        outer_lay = QVBoxLayout(dlg)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.addWidget(outer)

        main_lay = QVBoxLayout(outer)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        title_bar = QFrame()
        title_bar.setFixedHeight(42)
        title_bar.setStyleSheet(f"background: {accent}22; border-radius: 10px 10px 0 0;")
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(16, 0, 12, 0)

        sym = r["symbol"].replace("USDT", "/USDT")
        sym_lbl = QLabel(sym)
        sym_lbl.setStyleSheet(f"color:{ACCENT}; font-size:18px; font-weight:900; font-family:{MONO_CSS};")

        price_lbl = QLabel(f"${r['price']:.6f}")
        price_lbl.setStyleSheet(f"color:{WHITE}; font-size:15px; font-weight:700; font-family:{MONO_CSS};")

        chg   = r["change_24h"]
        chg_c = GREEN if chg >= 0 else RED
        chg_lbl = QLabel(f"{chg:+.2f}%")
        chg_lbl.setStyleSheet(f"color:{chg_c}; font-size:13px; font-weight:700;")

        badge = SignalBadge(sig)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(
            f"background: transparent; color: {DIM}; border: none; "
            f"font-size: 14px; font-weight: 700;")
        close_btn.clicked.connect(dlg.accept)

        tb_lay.addWidget(sym_lbl)
        tb_lay.addSpacing(12)
        tb_lay.addWidget(price_lbl)
        tb_lay.addSpacing(8)
        tb_lay.addWidget(chg_lbl)
        tb_lay.addStretch()
        tb_lay.addWidget(badge)
        tb_lay.addSpacing(8)
        tb_lay.addWidget(close_btn)
        main_lay.addWidget(title_bar)

        detail_panel = DetailPanel()
        detail_panel.load(r)
        main_lay.addWidget(detail_panel)

        hint = QLabel("Right-click row in Scanner to open a trade  |  Click outside or Esc to close")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color:{DIM}; font-size:10px; padding:6px; background:transparent;")
        main_lay.addWidget(hint)

        mw = self.geometry()
        w  = max(700, int(mw.width()  * 0.55))
        h  = max(600, int(mw.height() * 0.80))
        dlg.setFixedSize(w, h)
        dlg.move(
            mw.x() + (mw.width()  - w) // 2,
            mw.y() + (mw.height() - h) // 2)

        QShortcut(QKeySequence("Escape"), dlg).activated.connect(dlg.accept)

        class _OutsideClickFilter(QObject):
            def eventFilter(self_, obj, event):
                if event.type() == event.Type.MouseButtonPress:
                    gpos = event.globalPosition().toPoint()
                    if not dlg.geometry().contains(gpos):
                        dlg.accept()
                        QApplication.instance().removeEventFilter(self_)
                        return False
                return False

        click_filter = _OutsideClickFilter(dlg)
        QApplication.instance().installEventFilter(click_filter)
        dlg.finished.connect(
            lambda: QApplication.instance().removeEventFilter(click_filter))

        dlg.show()

    def _update_signal_log_size(self):
        if not hasattr(self, '_signal_log_size_lbl'):
            return
        import glob
        log_dir  = APP_LOGS_DIR
        log_path = _get_signal_log_path()
        try:
            today_rows = 0
            if os.path.exists(log_path):
                with open(log_path) as f:
                    today_rows = max(0, sum(1 for _ in f) - 1)
            all_files = glob.glob(os.path.join(log_dir, "signal_log_*.csv"))
            total_kb  = sum(os.path.getsize(f) for f in all_files) // 1024
            n_files   = len(all_files)
            if n_files:
                self._signal_log_size_lbl.setText(
                    f"Today: {today_rows:,} rows  |  {n_files} files  {total_kb}KB total")
            else:
                self._signal_log_size_lbl.setText("No log yet")
        except Exception:
            self._signal_log_size_lbl.setText("No log yet")

    def _show_outcome_analysis(self):
        import glob, csv as _csv
        log_dir   = APP_LOGS_DIR
        all_files = sorted(glob.glob(os.path.join(log_dir, "signal_log_*.csv")))

        if not all_files:
            self._show_status("No signal logs found — run scans first")
            return

        wins = losses = flats = pending = total_alerted = 0
        by_symbol = {}
        pct_moves = []

        for fpath in all_files:
            try:
                with open(fpath, newline="") as f:
                    for row in _csv.DictReader(f):
                        if row.get("alert_fired") != "True":
                            continue
                        total_alerted += 1
                        sym     = row.get("symbol", "").replace("USDT", "")
                        outcome = row.get("outcome", "")
                        pct_1h  = row.get("pct_1h", "")

                        if sym not in by_symbol:
                            by_symbol[sym] = {"W": 0, "L": 0, "F": 0, "P": 0}

                        if outcome == "WIN":
                            wins += 1; by_symbol[sym]["W"] += 1
                        elif outcome == "LOSS":
                            losses += 1; by_symbol[sym]["L"] += 1
                        elif outcome == "FLAT":
                            flats += 1; by_symbol[sym]["F"] += 1
                        else:
                            pending += 1; by_symbol[sym]["P"] += 1

                        if pct_1h not in ("", None):
                            try: pct_moves.append(float(pct_1h))
                            except: pass
            except Exception:
                pass

        resolved = wins + losses + flats
        win_rate = wins / resolved * 100 if resolved > 0 else 0
        avg_move = sum(pct_moves) / len(pct_moves) if pct_moves else 0

        report_lines = [
            f"OUTCOME ANALYSIS  —  {len(all_files)} day(s) of data",
            "",
            f"Total alerted signals  : {total_alerted}",
            f"Resolved (1h outcome)  : {resolved}",
            f"Pending  (< 1h old)    : {pending}",
            "",
            f"WIN   (>= +3% in 1h)  : {wins}  ({win_rate:.1f}%)",
        ]
        if resolved > 0:
            report_lines += [
                f"LOSS  (<= -2% in 1h)  : {losses}  ({losses/resolved*100:.1f}%)",
                f"FLAT  (between)        : {flats}  ({flats/resolved*100:.1f}%)",
            ]
        report_lines += [
            f"Avg 1h price move      : {avg_move:+.2f}%",
            "",
            "-" * 45,
            "BY SYMBOL  (W / L / F / Pending):",
        ]
        for sym, c in sorted(by_symbol.items(), key=lambda x: x[1]["W"], reverse=True):
            res = c["W"] + c["L"] + c["F"]
            wr  = c["W"] / res * 100 if res > 0 else 0
            report_lines.append(
                f"  {sym:10} W:{c['W']} L:{c['L']} F:{c['F']} P:{c['P']}  WR:{wr:.0f}%")

        if resolved == 0:
            report_lines += [
                "",
                "No outcomes yet — outcome tracking needs",
                "at least 1 hour after alerts fire.",
                "Keep the app running and check back later.",
            ]

        dlg = QDialog(self)
        dlg.setWindowTitle("Outcome Analysis")
        dlg.setMinimumSize(520, 460)
        dlg.setStyleSheet(f"background:{DARK}; color:{WHITE};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 16)

        title_lbl = QLabel("📊  Alert Outcome Analysis")
        title_lbl.setStyleSheet(
            f"color:{ACCENT}; font-size:16px; font-weight:800; margin-bottom:8px;")
        lay.addWidget(title_lbl)

        txt_edit = QTextEdit()
        txt_edit.setReadOnly(True)
        txt_edit.setFont(QFont("JetBrains Mono,DejaVu Sans Mono,Monospace", 11))
        txt_edit.setStyleSheet(
            f"background:{CARD}; color:{WHITE}; border:1px solid {BORDER}; "
            f"border-radius:6px; padding:12px;")
        txt_edit.setPlainText("\n".join(report_lines))
        lay.addWidget(txt_edit)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        close_btn.setStyleSheet(
            f"background:{CARD}; color:{DIM}; border:1px solid {BORDER}; "
            f"border-radius:4px; padding:6px 20px;")
        lay.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _open_signal_log(self):
        log_path = _get_signal_log_path()
        if os.path.exists(log_path):
            open_url(f"file://{log_path}")
        else:
            self._show_status("No signal log yet — run a scan first")

    def _clear_signal_log(self):
        import glob
        log_dir   = APP_LOGS_DIR
        all_files = glob.glob(os.path.join(log_dir, "signal_log_*.csv"))
        if not all_files:
            self._show_status("No signal logs to clear")
            return
        try:
            for f in all_files:
                os.remove(f)
            self._update_signal_log_size()
            self._show_status(f"Cleared {len(all_files)} signal log file(s)")
        except Exception as e:
            self._show_status(f"Could not clear logs: {e}")

    def _export(self):
        if not self._results:
            self.statusBar().showMessage("Nothing to export — run a scan first")
            return
        fname = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        fpath = os.path.join(APP_LOGS_DIR, fname)
        os.makedirs(APP_LOGS_DIR, exist_ok=True)
        clean = [{k: v for k, v in r.items() if k not in ("sig_clr", "candles")}
                 for r in self._results]
        with open(fpath, "w") as f:
            def _json_serial(obj):
                if hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                if hasattr(obj, '__str__'):
                    return str(obj)
                raise TypeError(f"Not serializable: {type(obj)}")
            json.dump(clean, f, indent=2, default=_json_serial)
        self._show_status(f"Exported → {fpath}")
        if hasattr(self, 'cfg_export_lbl'):
            self.cfg_export_lbl.setText(f"Saved: {fname}")
            self.cfg_export_lbl.setStyleSheet(f"color:{GREEN}; font-size:11px;")


# ─────────────────────────────────────────────────────────────
#  MODULE-LEVEL HELPERS AND ENTRY POINT
# ─────────────────────────────────────────────────────────────

def _global_exception_handler(exc_type, exc_value, exc_tb):
    import traceback, datetime as _dt
    log_path = os.path.join(APP_LOGS_DIR, "crash.log")
    try:
        with open(log_path, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"CRASH: {_dt.datetime.now()}\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        print(f"[CRASH LOGGED] → {log_path}")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    os.makedirs(APP_LOGS_DIR, exist_ok=True)

    os.environ.setdefault("QT_LOGGING_RULES",
        "qt.qpa.theme=false;qt.qpa.theme.gnome=false")

    sys.excepthook = _global_exception_handler
    app = QApplication(sys.argv)
    app.setApplicationName("Crypto Scalper Scanner")
    app.setStyleSheet(make_stylesheet(FONT_SIZE))

    import os as _os
    for _p in [
        _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app_icon.png"),
        _os.path.join(_os.getcwd(), "app_icon.png"),
        _os.path.join(_os.path.dirname(_os.path.abspath(sys.argv[0])), "app_icon.png"),
    ]:
        if _os.path.exists(_p):
            app.setWindowIcon(QIcon(_p))
            break

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(DARK))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(WHITE))
    palette.setColor(QPalette.ColorRole.Base,            QColor(DARK2))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(PANEL))
    palette.setColor(QPalette.ColorRole.Text,            QColor(WHITE))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(WHITE))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(DARK))
    app.setPalette(palette)

    win = CryptoScannerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
