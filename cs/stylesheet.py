from PyQt6.QtGui import QFont

# ─────────────────────────────────────────────────────────────
DARK  = "#0a0e1a"
DARK2 = "#0f1525"
PANEL = "#131929"
CARD  = "#1a2235"
BORDER= "#1e2d47"
ACCENT= "#00d4ff"
GREEN = "#00ff88"
RED   = "#ff3366"
YELLOW= "#ffcc00"
WHITE = "#e8f0fe"
DIM   = "#4a5568"
STRONG_BUY_BG  = "#002a1a"
STRONG_SELL_BG = "#2a0010"
BUY_BG         = "#001a10"
SELL_BG        = "#1a000a"

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

def make_stylesheet(fs=13):
    """Generate the full app stylesheet at a given base font size."""
    fs0  = fs        # base
    fs_s = fs - 1    # small (labels, headers)
    fs_x = fs - 2    # extra small (hints, status)
    fs_l = fs + 1    # large (buttons, titles)
    fs_h = fs + 5    # heading (symbol name in detail)
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

QStatusBar {{
    background: {PANEL};
    color: {DIM};
    border-top: 1px solid {BORDER};
    font-size: {fs_x}px;
}}
"""

STYLESHEET = make_stylesheet(FONT_SIZE)
