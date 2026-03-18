# Solana Token Scanner - PRD

## Original Problem Statement
Create a website that scans for new launched Solana tokens from pump.fun or other sources.

## Current Filter Criteria (Configurable via UI)
1. Volume >= $80K
2. Market Cap $10K - $1M
3. Liquidity $1K - $100K
4. Age <= 1 hour
5. Liq/MCap ratio filter (Min & Max %) — configurable, toggle-enabled

## User Choices
- Data Source: DexScreener API (free, with exponential backoff)
- Auto-refresh: Every 30 seconds
- Notifications: Telegram alerts with HTML format and clickable tag-style links
- No paid APIs
- All filter parameters configurable via Settings panel (persisted in localStorage)

## Architecture
- **Frontend**: React + Tailwind CSS + Framer Motion + Shadcn/UI (Label, Switch, Dialog, Input, Button)
- **Backend**: FastAPI + MongoDB + httpx
- **External APIs**: DexScreener (token data), Telegram (alerts)

## Implemented Features
| Feature | Status | Date |
|---------|--------|------|
| DexScreener-style table layout | Done | Mar 2026 |
| Configurable filter settings panel | Done | Mar 2026 |
| Filter persistence (localStorage) | Done | Mar 2026 |
| Real-time 30-second auto-refresh | Done | Mar 2026 |
| Exponential backoff for DexScreener | Done | Mar 2026 |
| Telegram notifications (HTML, tag-style links) | Done | Mar 2026 |
| Frontend only uses /tokens/scan | Done | Mar 2026 |
| Watchlist (backend only) | Done | Mar 2026 |
| Manual token check endpoint | Done | Mar 2026 |
| **Min Liq/MCap % filter** | Done | Mar 2026 |
| Pump.fun integration | Rolled back | Mar 2026 |

## Key API Endpoints
- `GET /api/tokens/scan` - Main scan (all params configurable, includes min_liq_mcap_pct)
- `POST /api/tokens/check/{address}` - Manual check + alert trigger
- `POST/GET/DELETE /api/tokens/watch/{address}` - Watchlist CRUD
- `POST /api/telegram/test` - Test notification
- `POST /api/telegram/subscribe` - Subscribe chat

## Next Tasks
1. **P1**: Refactor server.py into modules (API clients, filtering, notifications)
2. **P1**: Refactor App.js into smaller components
3. **P2**: Watchlist UI (frontend component to manage watchlist)
