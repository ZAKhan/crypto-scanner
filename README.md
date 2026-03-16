# Crypto Scalper Scanner v2.0.0

A professional PyQt6 desktop application for scanning Binance spot markets, identifying high-probability scalping opportunities, and executing live trades with automated stop-loss protection.

---

## Screenshots

> Scanner tab with live signals, Top Picks cards, Trades journal with real-time P&L.

---

## Features

### Scanner
- Scans Binance Spot market automatically on launch and every configurable interval (30s–1hr)
- Filters coins by price (< $1) and 24h volume (> $1M) — fully configurable
- Analyses up to 30 top coins by volume per scan
- Calculates RSI, StochRSI, MACD, Bollinger Bands, ATR, support/resistance, candlestick patterns
- Confluence-based signal scoring — STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL
- Columns: Symbol, Price, 24H%, RSI, StochRSI, MACD, BB%, Volume, Signal, POT%, EXP%, L/S score, Pattern, Sparkline chart, Age, Confidence, 1H trend
- Live sparkline mini-charts per coin
- Sortable columns, resizable, column width reset button
- Right-click context menu: LONG, SHORT, View Details, Open on Binance (5m chart)
- Top Picks tab — card view of best STRONG BUY / STRONG SELL signals
- Detail popup with full indicator breakdown, support/resistance, BB levels

### Live Trading (Binance API)
- Testnet and Live mode — toggle in Config tab with confirmation dialog
- Red banner displayed prominently when in Live mode
- Market BUY order execution with real fill price
- OCO (One Cancels the Other) stop-loss placed automatically after every BUY
  - Stop-loss lives on Binance servers — protects you even when app is closed
- Market SELL on Take Profit — detected every 3 seconds
- SL/TP auto-detection runs every 3 seconds independently of scan interval
- Journal fallback — if a coin is not available on testnet, option to record as journal trade
- Pre-checks symbol availability before attempting order
- Sell qty uses actual step size from Binance exchange info — no precision errors
- On insufficient balance error: automatically retries with actual held balance
- Balance displayed in top bar — click to refresh, auto-refreshes after each scan

### Trade Dialog
- Shows TESTNET / LIVE mode banner prominently
- Fetches live USDT balance from Binance
- 25% / 50% / 75% / 100% quick-fill buttons
- USDT amount field — plain number, click to select all and type new value
- Entry price (auto-filled from current price)
- Stop Loss — price field + % field (bidirectional sync)
- Take Profit — price field + % field (bidirectional sync)
- Cost / SL risk / TP gain / Risk:Reward ratio shown live
- OCO note shown when OCO is enabled

### Trades Journal
- Persists between sessions (JSON file)
- Open trades with live unrealised P&L updating every 3 seconds
- Closed trades showing final P&L in USDT and %
- Equity curve chart
- Trade statistics: total, open, wins, losses, win rate, avg win/loss, best/worst trade, profit factor, total P&L
- Summary bar: open count, closed count, win rate, total P&L
- Right-click: Close, Edit, Delete, Open on Binance
- Close dialog with pre-filled current price
- Edit dialog for manual adjustments
- Export to CSV
- Remove Closed button — removes all WIN and LOSS trades, keeps OPEN
- Multi-select delete

### Alerts
- Auto-scan with configurable interval (30s–1hr)
- Minimum signal threshold filter (STRONG BUY, BUY, etc.)
- Sound alerts (WAV playback)
- Desktop notifications
- Telegram alerts with bot token + chat ID
- WhatsApp via PicoClaw integration
- Alert log panel showing recent signals
- Signal age and confidence tracking across scans

### Config Tab
- Max price filter, min volume filter
- Scan interval, candle limit, number of top coins
- Top Picks count
- SL % and TP % defaults
- Browser path for opening charts
- Binance API key/secret (masked, show/hide toggle)
- Testnet/Live toggle
- OCO enable/disable
- Test Connection button with live balance check
- Apply Settings button

### UI / UX
- Dark theme throughout — teal/cyan accent, professional trading aesthetic
- Fixed top bar: title, version badge, subtitle, balance, status, progress, SCAN button
- Balance label shows `💰 23,934.48 USDT [T]` (T=testnet, L=live) — click to refresh
- Progress bar during scan (indeterminate spinner for background scans)
- SCAN button shows `⏳ Scanning...` while active
- Tab bar: Scanner, Top Picks, Config, Alerts, Trades
- Custom app icon (teal rounded square, candlestick chart, sparkle)
- Tooltip headers on scanner table columns
- Status bar with scan results and trade confirmations
- Flash effect on strong signals

---

## Installation

### Requirements
- Python 3.10+
- Linux (CachyOS / Arch recommended)

### Install dependencies
```bash
pip install PyQt6 requests
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

The script will:
- Check and install PyQt6, requests, PyInstaller if missing
- Build a standalone binary → `dist/linux/crypto_scanner`
- Copy to `binary/crypto_scanner` for releases
- Install to `~/.local/bin/crypto_scanner` — run from anywhere
- Register in app menu (requires `crypto_scanner.desktop`)

After building, launch with:
```bash
crypto_scanner
```
Or find it in your app menu under **Finance**.

### First-time app menu setup
Only needed once after first build:
```bash
# Install icon
mkdir -p ~/.local/share/icons
cp ~/apps/cryptoscanner/app_icon.png ~/.local/share/icons/crypto_scanner.png

# Install desktop entry
sed "s|%h|$HOME|g" ~/apps/cryptoscanner/crypto_scanner.desktop \
    > ~/.local/share/applications/crypto_scanner.desktop

# Refresh app menu
update-desktop-database ~/.local/share/applications
```

---

## Development Workflow

```
1. Edit code
        ↓
2. Test:   python crypto_scanner.py
        ↓
3. Build:  ./build.sh
        ↓
4. Launch: crypto_scanner   (or from app menu)
        ↓
5. Release: ./push_release.sh  →  GitHub Actions builds all platforms
```

### Releasing a new version
```bash
./push_release.sh
```
When prompted:
- Enter version number (e.g. `2.0.2`)
- Confirm APP_VERSION update — script patches it automatically
- Enter commit message
- Confirm

GitHub Actions will then build binaries for Linux, Windows and macOS automatically and attach them to the release.

---

## Configuration

### First launch
1. App opens and begins scanning automatically
2. Go to **Config** tab → **BINANCE API** section
3. Enter your API Key and Secret
4. Keep **Testnet Mode** enabled for testing
5. Click **Test Connection** to verify
6. Click **Apply Settings**

### Testnet setup
- Create API keys at `testnet.binance.vision` (login with GitHub)
- Fund testnet USDT by running `refill_testnet.py` (sells testnet BTC for USDT)

### Going Live
1. Create API keys at `binance.com` → Account → API Management
2. Enable **TRADE** and **USER_DATA** permissions only — never enable withdrawal
3. In Config tab: paste live keys, click the green TESTNET button → confirm → it turns red LIVE
4. Click Test Connection to verify real balance
5. Start with small position sizes

---

## How Trading Works

```
1. Scanner identifies STRONG BUY signal on COSUSDT
2. Right-click → LONG (buy COS)
3. Dialog shows: balance, entry price, SL/TP fields
4. Confirm → market BUY placed on Binance
5. OCO placed automatically: TP limit + SL stop
6. Trade appears in Trades tab with live P&L
7. Every 3 seconds: app checks if TP hit → market SELL + cancel OCO
8. If SL hit: OCO on Binance fires automatically (even if app closed)
```

### OCO Protection
OCO (One Cancels the Other) places two orders simultaneously on Binance:
- A limit sell at your TP price
- A stop-loss sell at your SL price

When one executes, the other cancels automatically. This means **your stop-loss is always active on Binance's servers** — even if the app crashes, your PC restarts, or your internet drops.

---

## File Structure

```
cryptoscanner/
├── crypto_scanner.py          # Main application
├── app_icon.png               # App icon (required alongside .py)
├── app_icon.ico               # Windows icon
├── README.md                  # This file
├── tutorial.html              # Usage tutorial
├── crypto_scanner_guide.odt   # Full guide
├── build.sh                   # Linux binary build script
├── push_release.sh            # GitHub release script (not tracked)
└── binary/                    # Built binaries
```

### Runtime files (created automatically)
```
~/.crypto_scanner_trades.json     # Trade journal
~/.crypto_scanner_crash.log       # Crash log (if any)
~/.crypto_scanner_trade_log.txt   # Audit trail of all orders
```

---

## Architecture

| Component | Description |
|-----------|-------------|
| `Scanner` | Fetches Binance tickers, calculates all indicators |
| `ScanWorker` | QThread wrapper for manual SCAN button |
| `AlertEngine` | Background thread: auto-scans every N seconds, fires alerts |
| `BinanceTrader` | All order operations: buy, sell, OCO, balance, symbol info |
| `CryptoScannerWindow` | Main PyQt6 window, all UI |
| Trade monitor | QTimer every 3s: fetches prices, checks TP/SL, updates P&L |

### Scan flow
```
AlertEngine._loop()
  → Scanner.start_scan()          # fetch tickers + candles
  → analyse() per coin            # indicators + scoring
  → scan_done.emit(results)       # → UI update (smooth, no table clear)
  → sleep(interval_sec)
  → repeat
```

### Trade monitor (every 3 seconds, always running)
```
_fetch_open_trade_prices()
  → GET /api/v3/ticker/price per open symbol
  → update _live_prices
  → _check_sltp_hits()            # execute sell if TP crossed
  → _refresh_trades_table()       # update P&L display
```

---

## Indicators

| Indicator | Description |
|-----------|-------------|
| RSI | 14-period relative strength index |
| StochRSI | Stochastic of RSI, 14-period |
| MACD | 12/26/9 MACD histogram |
| Bollinger Bands | 20-period, 2 std dev; BB% position |
| ATR | 14-period average true range |
| Volume | 24h quote volume vs average |
| Candlestick patterns | Doji, Hammer, Engulfing, Morning/Evening Star |
| Support/Resistance | Local swing highs/lows |
| 1H trend | Independent 1-hour timeframe fetch |
| Signal age | How many consecutive scans at same signal |
| Confidence | Confluence score 0–100% |

---

## Changelog

### v2.0.0 (current)
- Live trading: market BUY, OCO stop-loss, market SELL
- Testnet and Live mode toggle
- Trade journal with real-time P&L (3s refresh)
- Auto TP/SL detection every 3 seconds
- SL/TP % fields in trade dialog (bidirectional sync with price fields)
- USDT balance in top bar
- Auto-scan on launch (no manual scan needed)
- Symbol pre-check before order (testnet vs live)
- Journal fallback for coins not on testnet
- Remove Closed button (WIN + LOSS)
- Binance URL opens 5m chart
- Fixed symbol handling for ROBO_USDT style symbols
- App icon (teal rounded square)
- Global crash handler → `~/.crypto_scanner_crash.log`
- Atomic trade file saves (no corruption)
- All critical methods wrapped in try/except
- Division guards on all indicators

### v1.3.2
- RSI/StochRSI/MACD/BB scoring
- Top Picks cards
- Config tab with all settings
- Alerts: sound, desktop, Telegram, WhatsApp
- Trades journal with P&L
- Sparklines, detail popup, column tooltips
- Multi-select delete, SL/TP % fields

---

## License

Private — all rights reserved.
