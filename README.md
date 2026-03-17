# Crypto Scalper Scanner v2.2.0

A professional PyQt6 desktop application for scanning Binance spot markets, identifying high-probability scalping opportunities, and executing live trades with automated stop-loss protection.

---

## Features

### Scanner
- Scans Binance Spot market automatically on launch and every configurable interval (30s–1hr)
- Filters coins by price (< $1) and 24h volume (> $1M) — fully configurable
- Analyses up to 30 top coins by volume per scan
- Calculates RSI, StochRSI, MACD, Bollinger Bands, ATR, support/resistance, candlestick patterns
- Confluence-based signal scoring — **PRE-BREAKOUT / STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL**
- Columns: Symbol, Price, 24H%, RSI, StochRSI, MACD, BB%, Volume, Signal, POT%, EXP%, L/S score, Pattern, Sparkline chart, Age, Confidence, 1H trend
- Live sparkline mini-charts per coin
- Sortable columns, resizable, column width reset button
- Right-click context menu: LONG, SHORT, View Details, Open on Binance (5m chart), Open on TradingView
- Top Picks tab — card view with PRE-BREAKOUT, STRONG BUY and STRONG SELL sections
- Detail popup with full indicator breakdown, support/resistance, BB levels

### PRE-BREAKOUT Detection
Fires when all 4 conditions are true simultaneously — early warning before a price spike:
- BB width < 5% — Bollinger Bands squeezed tight (price coiling)
- Volume ≥ 1.5x average — unusual volume building quietly
- RSI between 35–55 — recovering but not overbought
- Price in bottom 25% of BB range — sitting at support

Shows as orange badge in scanner. Gets its own ⚡ PRE-BREAKOUT section in Top Picks. Has a distinct 3-tone ascending alert sound.

### WebSocket Live Prices
- Connects to Binance WebSocket stream after first scan
- Real-time price updates every 3 seconds via persistent connection
- No REST polling overhead — zero API rate limit usage for prices
- TP/SL detection fires on every price update
- Auto-reconnects on disconnect
- Falls back to REST polling if websocket-client not installed
- Status indicator ⚡ WS in status bar — green=connected, yellow=reconnecting
- Testnet: wss://stream.testnet.binance.vision
- Live: wss://stream.binance.com:9443

### Trade Safety System
All rules individually toggleable in Config tab → TRADE SAFETY:

| Rule | Default | Description |
|------|---------|-------------|
| Signal persistence | On | Signal must hold 2+ consecutive scans |
| BTC trend check | On | Skip if BTC dropping > 2% |
| Coin trend check | On | Skip if coin down > 5% in 24h |
| Max open trades | On | Hard limit of 3 concurrent trades |
| Daily loss limit | On | Stop trading if losses exceed $100 |

When a safety rule blocks a trade, a dialog explains why and offers an override. Daily loss counter resets automatically each day.

### Live Trading (Binance API)
- Testnet and Live mode — toggle in Config tab with confirmation dialog
- Red banner displayed prominently when in Live mode
- Market BUY order execution with real fill price
- OCO stop-loss placed automatically after every BUY
- Stop-loss lives on Binance servers — protects you even when app is closed
- TP/SL auto-detection via WebSocket — fires in milliseconds
- Journal fallback for coins not available on testnet
- Pre-checks symbol availability before attempting order
- Sell qty uses actual step size — no precision errors
- On insufficient balance error: automatically retries with actual held balance
- Balance displayed in top bar — click to refresh

### Trade Dialog
- Shows TESTNET / LIVE mode banner prominently
- Fetches live USDT balance from Binance
- 25% / 50% / 75% / 100% quick-fill buttons
- USDT amount field — click to select all and type instantly
- Entry price (auto-filled from current price)
- Stop Loss and Take Profit — price field + % field (bidirectional sync)
- Cost / SL risk / TP gain / Risk:Reward ratio shown live

### Trades Journal
- Persists between sessions (JSON file, atomic writes)
- Open trades with live unrealised P&L via WebSocket
- Closed trades showing final P&L in USDT and %
- Equity curve chart
- Trade statistics: total, open, wins, losses, win rate, avg win/loss, profit factor, total P&L
- Right-click: Close, Edit, Delete, Open on Binance, Open on TradingView
- Export to CSV, Remove Closed button, multi-select delete

### Alerts
- Auto-scan with configurable interval (30s–1hr)
- Two-column layout: filters left, notification channels right
- Filters: min signal, min potential %, min exp move %, max RSI, max BB%, require volume spike
- Sound alerts, desktop notifications, Telegram, WhatsApp (PicoClaw)
- Alert log panel, signal age and confidence tracking

### Config Tab
- SCAN FILTERS — max price, min volume, interval, top N coins, candles
- RISK MANAGEMENT — SL%, TP%, TP2%, R/R ratio display
- TRADE SAFETY — 5 safety rules with individual toggles and thresholds
- UI APPEARANCE — font size
- BINANCE API — key/secret, testnet/live toggle, OCO, test connection

### UI / UX
- Dark theme — teal/cyan accent, professional trading aesthetic
- Fixed top bar: title, version, subtitle, balance, scan button
- Balance shows 💰 23,934.48 USDT [T] — click to refresh
- Scan dot in status bar: green=idle, blinking blue=scanning
- WebSocket indicator ⚡ WS in status bar
- Status messages auto-clear after 10 seconds
- Custom app icon (teal rounded square)

---

## Installation

### Requirements
- Python 3.10+
- Linux (CachyOS / Arch recommended)

### Install dependencies
```bash
pip install PyQt6 requests websocket-client
```

### Run from source
```bash
python crypto_scanner.py
```

### Build local binary
```bash
cd ~/apps/cryptoscanner
./build.sh
```

After building, launch with `crypto_scanner` or from app menu under Finance.

### First-time app menu setup
```bash
mkdir -p ~/.local/share/icons
cp ~/apps/cryptoscanner/app_icon.png ~/.local/share/icons/crypto_scanner.png
sed "s|%h|$HOME|g" ~/apps/cryptoscanner/crypto_scanner.desktop \
    > ~/.local/share/applications/crypto_scanner.desktop
update-desktop-database ~/.local/share/applications
```

---

## Development Workflow

```
1. Edit code
2. Test:    python crypto_scanner.py
3. Build:   ./build.sh
4. Launch:  crypto_scanner
5. Release: ./push_release.sh
```

---

## Configuration

### First launch
1. App opens and begins scanning automatically
2. Config tab → BINANCE API → enter API Key and Secret
3. Keep Testnet Mode enabled for testing
4. Click Test Connection → Apply Settings

### Testnet setup
- Create keys at testnet.binance.vision (login with GitHub)
- Fund USDT by running `refill_testnet.py` (sells testnet BTC for USDT)

### Going Live
1. Create keys at binance.com → API Management
2. Enable TRADE and USER_DATA permissions only — never withdrawal
3. Config tab: paste keys, click TESTNET button → confirm → turns red LIVE
4. Test Connection → verify real balance
5. Review Trade Safety settings before first live trade

---

## How Trading Works

```
1. Scanner finds STRONG BUY or PRE-BREAKOUT signal
2. Trade Safety checks pass (signal held 2 scans, BTC not falling, etc.)
3. Right-click → LONG
4. Confirm → market BUY placed on Binance
5. OCO placed automatically: TP limit + SL stop
6. Trade appears with live P&L via WebSocket
7. WebSocket detects TP hit → market SELL + cancel OCO (milliseconds)
8. If SL hit: OCO fires on Binance automatically (even if app closed)
```

### OCO Protection
Your stop-loss lives on Binance's servers. Even if the app crashes, your PC restarts, or internet drops — Binance will automatically sell if price hits your SL.

---

## Architecture

| Component | Description |
|-----------|-------------|
| Scanner | Fetches tickers, calculates indicators |
| ScanWorker | QThread for manual scan button |
| AlertEngine | Background auto-scan thread |
| BinanceTrader | All order operations |
| BinanceWebSocketPrices | Real-time price feed |
| CryptoScannerWindow | Main UI window |
| check_trade_safety() | Validates safety rules before trades |

---

## File Structure

### App files
```
cryptoscanner/
├── crypto_scanner.py          # Main application
├── app_icon.png               # App icon (required alongside .py)
├── app_icon.ico               # Windows icon
├── crypto_scanner.desktop     # Linux app menu entry
├── README.md                  # This file
├── tutorial.html              # Usage tutorial
├── crypto_scanner_guide.odt   # Full guide
├── build.sh                   # Linux binary build script
├── push_release.sh            # GitHub release script
└── binary/                    # Built binaries
```

### Runtime data directory (created automatically on first launch)

| OS | Path |
|----|------|
| Linux | `~/.config/CryptoScalper/` |
| Windows | `%APPDATA%\CryptoScalper\` |
| macOS | `~/Library/Application Support/CryptoScalper/` |

```
CryptoScalper/
└── logs/
    ├── trades.json                    # trade journal (persists between sessions)
    ├── trade_log.txt                  # full order audit trail
    ├── signal_log_2026-03-17.csv      # today's scan signal log
    ├── signal_log_2026-03-16.csv      # yesterday's log
    └── crash.log                      # crash reports (only if app crashes)
```

Signal logs are created daily and files older than 7 days are deleted automatically. Open today's log from Config tab → Export Scan Results → **📋 Open Signal Log**.

---

## Indicators

| Indicator | Description |
|-----------|-------------|
| RSI | 14-period relative strength index |
| StochRSI | Stochastic of RSI |
| MACD | 12/26/9 histogram |
| Bollinger Bands | 20-period, 2 std dev; BB% and width |
| ATR | 14-period average true range |
| Volume ratio | vs 20-period average |
| Candlestick patterns | Doji, Hammer, Engulfing, Star patterns |
| Support/Resistance | Local swing highs/lows |
| 1H trend | Independent hourly timeframe |
| PRE-BREAKOUT | BB squeeze + volume + RSI + support |

---

## Changelog

### v2.2.0 (current)
- **Signal Audit Log** — every scan logs all coins to `CryptoScalper/logs/signal_log_YYYY-MM-DD.csv` with 22 columns including RSI, BB%, ADR, vol_ratio, alert_fired, safety_blocked, safety_reason — full audit trail for post-analysis
- **Daily log rotation** — new file each day, files older than 7 days auto-deleted — never grows unmanageable
- **Cross-platform data directory** — app data stored in OS-native location (Linux: `~/.config/CryptoScalper/`, Windows: `%APPDATA%\CryptoScalper\`, macOS: `~/Library/Application Support/CryptoScalper/`)
- **ADR Filter (Average Daily Range)** — calculates avg candle range % per coin, filters out flat/choppy coins. Default 0.5% for 5m candles
- **BUY/SELL renamed** — LONG renamed to BUY, SHORT disabled and marked "coming soon (margin)"
- **Open Signal Log button** in Config tab — opens today's CSV, shows row count and total size
- **Config tab 2-column layout** — setting groups arranged side by side

### v2.1.0
- PRE-BREAKOUT signal detection (BB squeeze + volume + RSI + support)
- WebSocket real-time price feed (wss://stream.binance.com:9443)
- Trade Safety System — 5 individually toggleable rules (signal persistence, BTC trend, coin trend, max trades, daily loss limit)
- Open in TradingView local app from right-click menu
- Alert filters: Max RSI, Max BB%, require volume spike
- Two-column Alerts tab layout

### v2.0.1
- Live trading: market BUY, OCO, market SELL
- Testnet/Live mode toggle
- Trade journal with real-time P&L
- Auto TP/SL detection
- SL/TP % fields in trade dialog
- Balance in top bar, auto-scan on launch
- Crash handler, atomic trade saves

### v1.3.2
- RSI/StochRSI/MACD/BB scoring
- Top Picks, Config, Alerts (sound/desktop/Telegram/WhatsApp)
- Trades journal, sparklines, detail popup

---

## License

Private — all rights reserved.
