# Admin console architecture (redesign)
- **Shell**: built once in `web/js/admin.js` (`shell()`); navigation = `NAV` + `GROUPS` + `META` (single source of truth; extension modules splice into `NAV`). Mobile uses an off-canvas drawer.
- **Design system**: `web/css/admin.css`, scoped to `body.adm` (user dashboard unaffected).
- **Dialogs**: `UI.dialog` in `web/js/ui.js` (`open`, `confirm({run})`, `form`, `details`, `close`, `closeAll`); `UI.modal` / `UI.confirmBox` are wrappers. Stack with inert parent, keyed singletons, async actions run inside the dialog.
- **Page lifecycle**: each navigation/refresh creates a fresh page node, bumps a token (late GET responses are dropped) and closes all dialogs.
- **Popup bug root cause**: list pages added `addEventListener` to the persistent `#view` node on every render, so handlers stacked (one click opened N dialogs / sent N requests); dialogs survived navigation; detail + confirm were unmanaged sibling modals.
- **Tests**: `python -m unittest tests.test_admin_ui -v` (needs Playwright + Chromium; skipped otherwise).

## Sidebar navigation (14 primary entries)

One registry drives everything: `NAV` (page key, label, icon, permission) plus `TREE` (the hierarchy) in `web/js/admin.js`. The sidebar, breadcrumbs,
active state, direct-URL routing and the permission filter all read them; extension modules (`crypto-admin.js`, `email-admin.js`, `legal-admin.js`,
`chat-admin.js`) splice their pages into `NAV`, and any page that is in `NAV` but in no group is shown under **More**, so a page can never be orphaned.

* A group with **one** visible page is a plain link. A group with several has a **landing link** (its first visible page; clicking it navigates and keeps the
  sub-pages open) and a separate **chevron button** (`aria-expanded`, `aria-controls`) that only expands/collapses and never navigates.
* The active page is highlighted, its group is highlighted and expanded, and it stays expanded while moving between its children. Expanded groups are remembered
  per tab in `sessionStorage` (convenience only). Keyboard: Up/Down/Home/End move between visible links, Right/Left expand/collapse, Enter/Space toggle.
* Routes are `#/<page>`; System Settings sub-pages are `#/settings/<tab>`. `#/settings` is an alias of `#/settings/general` (rewritten in the address bar).
  A route the administrator may not see falls back to the overview; the API re-checks every permission regardless.
* Breadcrumbs: `Dashboard › <group> › <page>` (the page is omitted where it would only repeat the group name).

| Primary entry | Pages (route) | Permission |
|---|---|---|
| Dashboard | Overview (`overview`) | none |
| Customers | Customers (`users`), Accounts (`accounts`) | `users.read`, `accounts.read` |
| Verification & Compliance | KYC & Compliance (`kyc`) | `kyc.review` |
| Crypto Operations | Deposits (`deposits`), Withdrawals (`withdrawals`), Wallets & Reconciliation (`settings-crypto`) | `crypto.deposits.read`, `crypto.withdrawals.read`, any of `crypto.wallets.read` / `crypto.recon.read` / `crypto.deposits.read` |
| Finance & Accounting | Transactions (`transactions`) | `transactions.read` |
| Assets & Markets | Assets & Markets (`assets`) | `assets.manage` |
| Investments | Investments (`investments`), Savings (`savings`), Retirement (`retirement`) | `investments.manage`, `savings.read`, `retirement.read` |
| Trading | Orders & Trading (`orders`) | `orders.read` |
| Customer Support | Support (`support`) | `support.manage` |
| Reports | Reports (`reports`) | `reports.read` |
| Communications | Notifications (`notifications`), Email Center (`email`), Community (`community`) | `notifications.send`, any `email.*`, `chat.moderate` |
| Content & Legal | Content (`content`), Legal (`legal`) | `content.manage`, `legal.read` |
| Audit & Security | Audit (`audit`), Roles & permissions (`roles`) | `audit.read`, `roles.manage` |
| System Settings | General, Security, API Management, Notification, Payment, Integration (`settings/<tab>`) | `settings.manage` |

Documented mappings (decided, not duplicated): the **Deposits** and **Withdrawals** pages are the crypto request/approval pages (`crypto-admin.js` gates them with
`crypto.*` permissions and replaces their content), so they live under Crypto Operations; Finance & Accounting holds the ledger (Transactions) and reconciliation
reports stay under Reports. Company wallets (`settings-crypto`) was previously reachable only from a card on the Settings page and as a "hidden" route open to any
staff member; it is now a normal permission-gated page (the Settings card still links to it).

## Platform switches (System Settings > Display Settings)

Enforced on the server (`server/platform_gate.py`, `server/marketdata.py`, `server/routes_auth.py`), stored in `system_settings`, audited. `python -m server.platform_cli
maintenance off|on` (also `registration`, `live-prices`, `require-2fa`) changes a switch without signing in, for emergency access. Details and tests:
`tests/test_platform_switches.py`; typed settings API and tests: `server/platform_settings.py`, `tests/test_platform_settings.py`.

## Dialog lifecycle rules (verified by `TestPopupLifecycle`, `TestResponsiveSweep` in `tests/test_admin_ui.py`)

All admin popups go through `UI.dialog` (stack, `inert` parents, singleton `key`, per-dialog `AbortController`, `closeAll()` on every navigation). Rules the pages rely on:

* **An action runs inside its dialog** (`confirm({run})`, `form({onSubmit})`): buttons lock, a failure stays in the dialog with the server's message, success closes it.
* **Navigating away mid-request:** the dialog is closed (nothing opens over the new page) and the page-level follow-up (`route()`, toast) is skipped, so the new page is never re-rendered or overwritten.
  The request still finishes and the manager reports its outcome with one notice (`<action> — completed.` / `<action> failed: <server message>`), so a result is never silent.
  Callers must show their own success toast *after* the dialog promise resolves (not inside `run`), otherwise a navigated-away action would notify twice.
* **Escape** is swallowed by a capture-phase keydown handler while the top dialog is busy or not dismissible. Preventing `cancel` alone is not enough: Chromium closes a `<dialog>` on a repeated Escape.
* **Rows:** the shared list handler opens a row's dialog only for clicks that are not on a button, link, field, label or `[data-menu]` inside the row, so an inline action never also opens the row's review/edit dialog.
* **Layout:** grid tracks that hold free text use `minmax(0,1fr)` (a plain `1fr`/auto track grows to the widest unbreakable word and widens the page); native controls are `max-width:100%`.

Root causes found while reproducing (each had a failing test before the fix): an action's outcome was lost when its dialog was force-closed by navigation; repeated Escape closed a busy dialog;
the generic row handler had no guard for controls inside a row; an auto grid track in the Overview activity list and an unconstrained `<select>` on the Legal page overflowed at 320px.
