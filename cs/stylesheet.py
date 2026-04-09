from PyQt6.QtGui import QFont

# ─────────────────────────────────────────────────────────────
DARK  = "#0a0a0a"
DARK2 = "#0a0a0a"
PANEL = "#0a0a0a"
CARD  = "#0f0f0f"
BORDER= "#2a2a2a"
ACCENT= "#f0c040"
GREEN = "#4caf50"
RED   = "#e53935"
YELLOW= "#ffa726"
WHITE = "#cccccc"
DIM   = "#555555"
STRONG_BUY_BG  = "#0a0f0a"
STRONG_SELL_BG = "#0f0a0a"
BUY_BG         = "#0a0a0a"
SELL_BG        = "#0a0a0a"

CURRENT_THEME = "TRADER"

_THEMES = {
    "CYBER": {
        "DARK":  "#0a0e1a", "DARK2": "#0f1525", "PANEL": "#131929", "CARD":  "#1a2235",
        "BORDER": "#1e2d47", "ACCENT": "#00d4ff",
        "GREEN": "#00ff88", "RED": "#ff3366", "YELLOW": "#ffcc00", "WHITE": "#e8f0fe", "DIM": "#4a5568",
        "STRONG_BUY_BG": "#002a1a", "STRONG_SELL_BG": "#2a0010", "BUY_BG": "#001a10", "SELL_BG": "#1a000a",
    },
    "TRADER": {
        "DARK":  "#0a0a0a", "DARK2": "#0a0a0a", "PANEL": "#0a0a0a", "CARD":  "#0f0f0f",
        "BORDER": "#2a2a2a", "ACCENT": "#f0c040",
        "GREEN": "#4caf50", "RED": "#e53935", "YELLOW": "#ffa726", "WHITE": "#cccccc", "DIM": "#555555",
        "STRONG_BUY_BG": "#0a0f0a", "STRONG_SELL_BG": "#0f0a0a", "BUY_BG": "#0a0a0a", "SELL_BG": "#0a0a0a",
    },
}

# ── Cross-platform monospace font stack ──────────────────────────────────────
# Arch Linux built-in:  DejaVu Sans Mono, Liberation Mono
# Windows built-in:     Cascadia Code (Win11), Consolas (Vista+), Courier New
# macOS built-in:       SF Mono (10.12+), Menlo (10.6+), Monaco
# Common dev install:   JetBrains Mono, Fira Code, Hack, Source Code Pro
# Final fallback:       "monospace" Qt/CSS generic hint (always resolves)
MONO_FAMILIES = [
    # Preferred — popular dev fonts, available on all platforms if installed
    "JetBrains Mono", "Fira Code", "Fira Mono", "Cascadia Code", "Cascadia Mono",
    "Source Code Pro", "Hack", "Iosevka", "Inconsolata",
    # Linux guaranteed
    "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Noto Mono",
    # macOS guaranteed
    "SF Mono", "Menlo", "Monaco", "Andale Mono",
    # Windows guaranteed
    "Consolas", "Lucida Console", "Courier New",
    # Generic Qt hint — must be last, always resolves to something monospaced
    "monospace",
]

# For Qt stylesheets (font-family CSS property)
MONO_CSS = (
    "'JetBrains Mono','Fira Code','Fira Mono','Cascadia Code','Cascadia Mono',"
    "'Source Code Pro','Hack','Ubuntu Mono','DejaVu Sans Mono','Liberation Mono',"
    "'Noto Mono','SF Mono','Menlo','Monaco','Consolas','Lucida Console',"
    "'Courier New',monospace"
)

# Single name used where Qt API needs one string (setFamilies handles the rest)
MONO = "JetBrains Mono"

def mono_font(size=10, bold=False):
    """
    Return a QFont using the full cross-platform monospace fallback chain.
    Qt walks MONO_FAMILIES in order and uses the first one found on the system.
    StyleHint.Monospace + setFixedPitch(True) ensure a fixed-width font is
    always selected even if none of the named families are installed.
    """
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPointSize(size)
    f.setBold(bold)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True)
    return f

SANS  = "Inter,Segoe UI,SF Pro Display,sans-serif"

FONT_SIZE    = 13   # default — user can change in Config tab
BROWSER_PATH = ""   # empty = use system default; set via Config tab

def make_stylesheet(fs=13, theme="CYBER"):
    """Generate the full app stylesheet at a given base font size and theme.

    TRADER theme returns a completely independent stylesheet — nothing is
    inherited from the CYBER palette.
    """
    if theme == "TRADER":
        return _make_trader_stylesheet(fs)
    return _make_cyber_stylesheet(fs)


# ── TRADER stylesheet ────────────────────────────────────────────────────────

def _make_trader_stylesheet(fs=13):
    """Pure-black terminal aesthetic — monospace everywhere, amber accent only."""
    fs   = max(fs - 1, 9)
    fs_s = fs - 1
    fs_x = fs - 2
    fs_l = fs + 1
    fs_h = fs + 4
    M    = MONO_CSS     # alias
    return f"""
QMainWindow, QWidget {{
    background-color: #0a0a0a;
    color: #cccccc;
    font-family: {M};
    font-size: {fs}px;
}}

QTabWidget::pane {{
    border: 1px solid #1e1e1e;
    background: #0a0a0a;
    border-radius: 0px;
}}
QTabBar::tab {{
    background: #0a0a0a;
    color: #555555;
    padding: 6px 16px;
    border: 1px solid #1e1e1e;
    border-bottom: none;
    font-family: {M};
    font-weight: 500;
    font-size: {fs_s}px;
}}
QTabBar::tab:selected {{
    background: #0a0a0a;
    color: #f0c040;
    border-bottom: 2px solid #f0c040;
}}
QTabBar::tab:hover:!selected {{
    color: #888888;
    background: #0f0f0f;
}}

QTableWidget {{
    background-color: #0a0a0a;
    gridline-color: #1a1a1a;
    border: 1px solid #1e1e1e;
    border-radius: 0px;
    font-family: {M};
    font-size: {fs_s}px;
    selection-background-color: #111111;
    selection-color: #cccccc;
    outline: none;
}}
QTableWidget::item {{
    padding: 4px 8px;
    border-bottom: 1px solid #141414;
}}
QTableWidget::item:hover {{
    background-color: #111111;
}}
QHeaderView::section {{
    background-color: #0a0a0a;
    color: #555555;
    padding: 4px 8px;
    border: none;
    border-right: 1px solid #1a1a1a;
    border-bottom: 1px solid #2a2a2a;
    font-family: {M};
    font-size: 10px;
    font-weight: 500;
}}
QHeaderView::section:hover {{
    background-color: #0f0f0f;
    color: #888888;
}}

QPushButton {{
    background-color: #0a0a0a;
    color: #888888;
    border: 1px solid #2a2a2a;
    border-radius: 3px;
    padding: 6px 16px;
    font-family: {M};
    font-size: {fs}px;
}}
QPushButton:hover {{
    border-color: #f0c040;
    color: #f0c040;
}}
QPushButton:pressed {{
    background-color: #f0c040;
    color: #0a0a0a;
}}
QPushButton#scanBtn {{
    background: #0a0a0a;
    color: #f0c040;
    border: 1px solid #f0c040;
    padding: 4px 8px;
    font-family: {M};
    font-size: {fs_l}px;
    font-weight: 700;
    border-radius: 3px;
    min-width: 0px;
}}
QPushButton#scanBtn:hover {{
    background: #f0c040;
    color: #0a0a0a;
}}
QPushButton#scanBtn:disabled {{
    background: #0a0a0a;
    color: #333333;
    border-color: #2a2a2a;
}}

QProgressBar {{
    background: #0f0f0f;
    border: 1px solid #2a2a2a;
    border-radius: 2px;
    height: 4px;
    color: transparent;
}}
QProgressBar::chunk {{
    background: #f0c040;
    border-radius: 1px;
}}

QLabel#statusLabel {{
    color: #444444;
    font-family: {M};
    font-size: {fs_x}px;
    padding: 2px 8px;
}}
QLabel#titleLabel {{
    color: #f0c040;
    font-family: {M};
    font-size: {fs_h}px;
    font-weight: 700;
}}
QLabel#subtitleLabel {{
    color: #555555;
    font-family: {M};
    font-size: {fs_x}px;
}}
QLabel#versionLabel {{
    color: #0a0a0a;
    background: #f0c040;
    font-family: {M};
    font-size: {fs_x}px;
    font-weight: 700;
    border-radius: 2px;
    padding: 1px 6px;
}}

QFrame#cardFrame {{
    background: #0f0f0f;
    border: 1px solid #2a2a2a;
    border-radius: 3px;
    padding: 8px;
}}
QFrame#accentCard {{
    background: #0f0f0f;
    border: 1px solid #2a2a2a;
    border-left: 2px solid #f0c040;
    border-radius: 3px;
    padding: 8px;
}}

QGroupBox {{
    color: #f0c040;
    border: 1px solid #2a2a2a;
    border-radius: 3px;
    margin-top: 14px;
    font-family: {M};
    font-weight: 600;
    font-size: {fs_x}px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 8px;
}}

QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit, QTextEdit {{
    background: #0f0f0f;
    color: #cccccc;
    border: 1px solid #2a2a2a;
    border-radius: 2px;
    padding: 4px 8px;
    font-family: {M};
    font-size: {fs_s}px;
    min-width: 80px;
}}
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
    border-color: #f0c040;
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: #0f0f0f;
    color: #cccccc;
    border: 1px solid #2a2a2a;
    selection-background-color: #1a1a1a;
}}

QScrollBar:vertical {{
    background: #0a0a0a;
    width: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: #1e1e1e;
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #2a2a2a;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{
    background: #0a0a0a;
    height: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: #1e1e1e;
    border-radius: 3px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #2a2a2a;
}}

QCheckBox {{
    color: #888888;
    font-family: {M};
    font-size: {fs_s}px;
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid #2a2a2a;
    border-radius: 2px;
    background: #0f0f0f;
}}
QCheckBox::indicator:hover {{
    border-color: #f0c040;
}}
QCheckBox::indicator:checked {{
    background: #f0c040;
    border-color: #f0c040;
    image: none;
}}
QCheckBox:disabled {{
    color: #333333;
}}
QCheckBox::indicator:disabled {{
    border-color: #222222;
    background: #0a0a0a;
}}

QStatusBar {{
    background: #0a0a0a;
    color: #444444;
    border-top: 1px solid #1a1a1a;
    font-family: {M};
    font-size: {fs_x}px;
}}

QMenu {{
    background: #0f0f0f;
    color: #cccccc;
    border: 1px solid #2a2a2a;
    font-family: {M};
}}
QMenu::item:selected {{
    background: #1a1a1a;
    color: #f0c040;
}}

QToolTip {{
    background: #0f0f0f;
    color: #cccccc;
    border: 1px solid #2a2a2a;
    font-family: {M};
    font-size: {fs_x}px;
}}
"""


# ── CYBER stylesheet ─────────────────────────────────────────────────────────

def _make_cyber_stylesheet(fs=13):
    """Original blue neon aesthetic."""
    c      = _THEMES["CYBER"]
    DARK   = c["DARK"];  DARK2 = c["DARK2"]; PANEL = c["PANEL"]; CARD = c["CARD"]
    BORDER = c["BORDER"]; ACCENT = c["ACCENT"]
    GREEN  = c["GREEN"]; RED = c["RED"]; YELLOW = c["YELLOW"]
    WHITE  = c["WHITE"]; DIM = c["DIM"]

    fs0  = fs
    fs_s = fs - 1
    fs_x = fs - 2
    fs_l = fs + 1
    fs_h = fs + 5
    return f"""
QMainWindow, QWidget {{
    background-color: {DARK};
    color: {WHITE};
    font-family: {SANS};
    font-size: {fs0}px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {DARK2};
    border-radius: 6px;
}}
QTabBar::tab {{
    background: {PANEL};
    color: {DIM};
    padding: 8px 20px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: 600;
    font-size: {fs_s}px;
    letter-spacing: 0.5px;
}}
QTabBar::tab:selected {{
    background: {CARD};
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover:!selected {{
    color: {WHITE};
    background: {CARD};
}}

QTableWidget {{
    background-color: {DARK2};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: {MONO_CSS};
    font-size: {fs_s}px;
    selection-background-color: #1a3a5c;
    selection-color: {WHITE};
    outline: none;
}}
QTableWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {BORDER};
}}
QTableWidget::item:hover {{
    background-color: #162030;
}}
QHeaderView::section {{
    background-color: {PANEL};
    color: {ACCENT};
    padding: 8px 10px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 2px solid {ACCENT};
    font-family: {SANS};
    font-size: {fs_x}px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QHeaderView::section:hover {{
    background-color: {CARD};
    color: {WHITE};
}}

QPushButton {{
    background-color: {CARD};
    color: {WHITE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: {fs0}px;
    letter-spacing: 0.3px;
}}
QPushButton:hover {{
    background-color: {BORDER};
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: {DARK};
}}
QPushButton#scanBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0066aa, stop:1 #00aacc);
    color: white;
    border: none;
    padding: 4px 8px;
    font-size: {fs_l}px;
    font-weight: 700;
    border-radius: 6px;
    min-width: 0px;
}}
QPushButton#scanBtn:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0088cc, stop:1 #00ccee);
}}
QPushButton#scanBtn:disabled {{
    background: {DIM};
    color: #888;
}}

QProgressBar {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 #0066ff);
    border-radius: 3px;
}}

QLabel#statusLabel {{
    color: {DIM};
    font-size: {fs_x}px;
    padding: 2px 8px;
}}
QLabel#titleLabel {{
    color: {ACCENT};
    font-size: {fs_h}px;
    font-weight: 800;
    letter-spacing: 2px;
    font-family: {MONO_CSS};
}}
QLabel#subtitleLabel {{
    color: {DIM};
    font-size: {fs_x}px;
    letter-spacing: 1px;
}}
QLabel#versionLabel {{
    color: {DARK};
    background: {ACCENT};
    font-size: {fs_x}px;
    font-weight: 800;
    font-family: {MONO_CSS};
    border-radius: 4px;
    padding: 1px 7px;
}}

QFrame#cardFrame {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 12px;
}}
QFrame#accentCard {{
    background: {CARD};
    border: 1px solid {ACCENT};
    border-left: 3px solid {ACCENT};
    border-radius: 8px;
    padding: 12px;
}}

QGroupBox {{
    color: {ACCENT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 16px;
    font-weight: 700;
    font-size: {fs_x}px;
    letter-spacing: 1px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 10px;
}}

QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit {{
    background: {PANEL};
    color: {WHITE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 10px;
    font-family: {MONO_CSS};
    font-size: {fs_s}px;
    min-width: 80px;
}}
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    color: {WHITE};
    border: 1px solid {BORDER};
    selection-background-color: {CARD};
}}

QScrollBar:vertical {{
    background: {DARK2};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QCheckBox {{
    color: {WHITE};
    font-size: {fs_s}px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {ACCENT};
    border-radius: 3px;
    background: {PANEL};
}}
QCheckBox::indicator:hover {{
    border-color: {WHITE};
    background: {CARD};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}
QCheckBox::indicator:checked:hover {{
    background: {WHITE};
    border-color: {WHITE};
}}
QCheckBox:disabled {{
    color: {DIM};
}}
QCheckBox::indicator:disabled {{
    border-color: {DIM};
    background: {DARK2};
}}

QStatusBar {{
    background: {PANEL};
    color: {DIM};
    border-top: 1px solid {BORDER};
    font-size: {fs_x}px;
}}
"""

STYLESHEET = make_stylesheet(FONT_SIZE)


def set_theme(name):
    """Update all module-level color constants to the named theme and regenerate STYLESHEET."""
    global CURRENT_THEME, DARK, DARK2, PANEL, CARD, BORDER, ACCENT, GREEN, RED, YELLOW, WHITE, DIM
    global STRONG_BUY_BG, STRONG_SELL_BG, BUY_BG, SELL_BG, STYLESHEET
    if name not in _THEMES:
        return
    c              = _THEMES[name]
    CURRENT_THEME  = name
    DARK           = c["DARK"];   DARK2  = c["DARK2"];  PANEL  = c["PANEL"];  CARD   = c["CARD"]
    BORDER         = c["BORDER"]; ACCENT = c["ACCENT"]
    GREEN          = c["GREEN"];  RED    = c["RED"];     YELLOW = c["YELLOW"]; WHITE  = c["WHITE"]; DIM = c["DIM"]
    STRONG_BUY_BG  = c["STRONG_BUY_BG"];  STRONG_SELL_BG = c["STRONG_SELL_BG"]
    BUY_BG         = c["BUY_BG"];         SELL_BG        = c["SELL_BG"]
    STYLESHEET     = make_stylesheet(FONT_SIZE, name)
