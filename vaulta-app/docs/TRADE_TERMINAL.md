# Trade terminal and community chat

The authenticated **Trade** page (`/app/#/trade`) is now a trading workspace: market explorer, interactive chart, order panel, market details, order book / recent trades (only where real data exists), community chat and "My orders". Nothing was added as a separate Markets or Community page.

## Architecture discovered (and what that meant for the design)
* Backend: Flask (Python), SQLite, versioned SQL migrations (`server/migrations`), RBAC permissions, hash-chained audit log, CSRF + session auth, unittest suite. Frontend: static HTML + vanilla JS, **no framework and no build step**. The dashboard is a hash-routed SPA (`web/js/app.js`) with an extension hook (`window.VAULTA_EXT`).
* The existing Trade page used Vaulta's **internal asset registry**: demo quotes, orders filled on an internal ledger (`non_live`). That behaviour is preserved.
* Decisions: reuse the existing backend rather than adding Supabase (it already owns identity, sessions, CSRF, RBAC and the database); no new frameworks; no npm packages (offline build).

## What was added
| Area | Files |
|---|---|
| Market data service | `server/marketdata.py` |
| Terminal + chat API | `server/routes_terminal.py`, `server/community.py` |
| Database | `server/migrations/007_trade_terminal.sql` |
| UI | `web/js/terminal.js`, `web/js/tchart.js`, `web/js/tchat.js`, `web/css/terminal.css`, `web/js/chat-admin.js` (moderation page) |
| Tests | `tests/test_terminal.py` (35), `tests/tools/mock_coingecko.py` (dev/e2e mock provider) |
Modified: `server/app.py` (blueprints, CSP img-src), `server/rbac.py` (`chat.moderate`), `server/util.py` (`ts_after(seconds=)`), `web/js/app.js` (risk-gate re-applied after extensions), `web/app/index.html`, `web/admin/index.html`, `.env.example`.

## Market data
Verified against the CoinGecko docs: Demo base `https://api.coingecko.com/api/v3` + `x-cg-demo-api-key`; Pro `https://pro-api.coingecko.com/api/v3` + `x-cg-pro-api-key`; `/coins/markets` (`vs_currency, order, per_page<=250, page, ids, sparkline, price_change_percentage`), `/search/trending`, `/search`, `/coins/{id}`, `/coins/{id}/ohlc` (`days` 1/7/14/30/90/180/365/max; **timestamps are candle close times; granularity is 30 min for 1-2 days, 4 h for 3-30 days, 4 days for 31+**; Demo history limited to 365 days).
* The browser only calls `/api/terminal/*`. Responses are validated and normalised; nothing is invented (invalid or inconsistent candles are dropped, never repaired).
* Caching: markets 60 s, trending/search 5 min, coin 2 min, OHLC 10 min, persisted in `market_cache` so a restart can still show **labelled stale** data (up to 24 h). Concurrent requests for the same key share one provider call.
* Rate protection: local per-minute budget, automatic cool-down after HTTP 429 (honours `Retry-After`), 8 s timeout. The UI shows `Live` / `Cached` / `Stale · age` and a clear error state when nothing is available.
* Tabs: All, Trending (provider), Top gainers/losers (**ranked within the top 250 by market cap**, stated in the UI), Favourites, Search. Sorting by market cap/volume is provider-side; by price/24h/7d it is local to the loaded set.
* Chart: candles (real OHLC), line and area (closes), zoom (wheel / +/-), pan (drag / arrows), crosshair, reset. Built as a small canvas component because TradingView Lightweight Charts could not be installed offline; it is isolated in `tchart.js` and can be swapped.
* Attribution: the explorer shows "Data: CoinGecko".

## Trading: what is real and what is simulated
* **All order execution is simulated** on Vaulta's internal ledger at Vaulta's own demo quote, exactly as before. The order panel says so and shows both the Vaulta quote and the live market price. No exchange is contacted.
* Only coins mapped to an active, tradable registry asset (`market_asset_map`: BTC, ETH, SOL, USDT, USDC) get an order form. Everything else is tagged "Market data only" and has no order controls. The server still rejects unsupported symbols.
* Validation, balance checks, risk gate, fees, idempotency and audit are the existing server-side code; the terminal only calls `POST /api/orders`.
* **Order book:** unavailable (CoinGecko has no depth and no exchange is integrated). **Recent trades:** Vaulta's own anonymised internal fills, labelled as such.

## Community chat
* Persisted in SQLite (`chat_channels`, `chat_messages`, `chat_reports`, `chat_mutes`, `chat_blocks`, `chat_presence`). Message text is immutable (DB trigger); moderators can only remove.
* Sender identity comes from the session; client-supplied ids/roles/names are ignored. Verified email required to post. `announcements` is staff-only (server-enforced).
* Limits: 500 chars, max 2 links, control/bidi characters stripped, 5 messages per 10 s per user, identical-message block, per-user idempotency nonce (a retried send never duplicates). Reports, per-user blocking, moderator remove/mute (audit-logged), moderation queue in the admin console (`#/community`, permission `chat.moderate`).
* **Real-time model:** cursor polling (`GET .../messages?after=<id>`) every ~3 s while visible, with back-off and states Connecting / Connected / Reconnecting / Unable to connect. Presence is a heartbeat table ("online" = polled in the last 45 s). Subscriptions/timers are cleaned up on leaving Trade.
* Messages render with `textContent` only. Not implemented: emoji reactions, typing indicators, server-side unread counts (a dot marks channels with new messages since you last opened them).

## Configuration
See `.env.example` (`COINGECKO_API_KEY`, `COINGECKO_PLAN`, `COINGECKO_BASE_URL`, `MARKET_MAX_CALLS_PER_MINUTE`, `MARKET_TIMEOUT_SECONDS`, `MARKET_IMAGE_HOSTS`). Keys are read from the environment, never sent to the browser, never logged, never returned by `/api/terminal/status`.

## Run
```
pip install -r requirements.txt
export COINGECKO_API_KEY=...        # free Demo key
python -m server.seed               # dev data (writes credentials to instance/dev-credentials.txt)
python run.py                       # migrations (incl. 007) apply automatically at startup
# offline development: python tests/tools/mock_coingecko.py 5090 &  COINGECKO_BASE_URL=http://127.0.0.1:5090/api/v3 python run.py
python -m unittest discover -s tests
```
Production: use a WSGI server (gunicorn), HTTPS, and see the scaling note below.

## Limitations / next steps
* **Not verified against the live CoinGecko service** (the build environment has no internet). Request shapes follow the documentation; everything was exercised against a local mock that mimics the documented responses, plus unit tests of timeouts, 429s, malformed data and stale cache. Do a smoke test with your key.
* Coin logos load from CoinGecko image hosts (CSP allows only `MARKET_IMAGE_HOSTS`); a failed logo falls back to initials.
* Polling + SQLite is fine for a small deployment. For many concurrent users move chat to Postgres/Redis with SSE or WebSockets (or Supabase Realtime) and a shared cache; the in-process market cache and rate budget are per worker.
* No WebSocket price stream: CoinGecko streaming needs a paid plan. Prices refresh by REST every 60 s.
* Real execution, real order books and leverage/liquidation are **not** implemented and are not simulated.
