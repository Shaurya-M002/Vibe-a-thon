# Governor control room

A local, responsive dashboard for the existing Governor runtime. Vanilla HTML,
CSS and JavaScript are served by the Python package; no Node install or frontend
build step is needed. Orange accents, pixel type and two transparent clay assets
are shared by the light and dark themes. Fonts are self-hosted.

## Start

From `vibes/markets`, with the existing virtual environment:

```bash
.venv/bin/pip install -e . --no-deps
.venv/bin/governor-web
```

Open <http://127.0.0.1:8787>. Use `--port 8790` for another port, or
`--data-dir /path/to/local/state` for a separate ledger and wallet directory.
The server loads only the `.env` in its working directory.

## Working interactions

- **Overview:** selected session's available budget, settled spend, persistent
  holds, refusals, agent status and an activity stream with payment/refusal filters.
- **Task launcher:** enter a task or use a preset, then run the configured Gemini
  model. The existing tool allowlist and operator budget policy remain in force.
- **Sandbox demo:** runs the real agent loop and payment gate with a scripted
  model. No credentials or network calls are needed. With default caps, it ends
  with 8000 atomic USDC settled, 2000 held, and three refused payments.
- **Sessions:** search local tasks, inspect their ledger and final response,
  download audit JSON and settled-expense CSV. Older CLI sessions have their
  existing audit events; final answer text is available for dashboard-created runs.
- **Codex plugin sessions:** Codex calls Governor tools directly over the local
  CLI bridge. Session details show Codex ownership, waiting/running state,
  expandable tool results, budget and its submitted final answer. No Gemini model
  runs in this path. See [Codex plugin](codex-plugin.md).
- **Services:** live Bazaar search, vendor comparisons and recommendations, plus the
  four simulated services and task-launcher shortcuts. Discovered sellers remain
  advisory listings with purchases disconnected. See [Bazaar discovery](bazaar.md).
- **Local vendor:** start `governor-vendor`, then use Services → **Pay 0.002 USDC**
  for one actual Devnet purchase, or **Use Gemini + Devnet payment** for a live
  Gemini task. The separate seller console at <http://127.0.0.1:8788> shows its
  wallet, confirmed order revenue and receipts. See [vendor setup](../src/governor/vendor/README.md).
- **Devnet receipts:** session details link confirmed transactions to the explorer.
  Recheck an uncertain payment using the existing signed transaction's evidence;
  this never signs or sends a second payment.
- **Vendor scout:** optional parallel Gemini child context with live search branches,
  task-fit/price/usage ranking, explicit no-match and error states, token usage and a
  discovery activity filter. Enabled by default in the Gemini launcher.
- **Wallet:** read verified Solana Devnet balances, copy the public address, and
  open the explorer. A missing wallet or failed RPC does not become a zero balance.
- **Policy:** inspect the configured caps, turn/tool limits and run deadline.
- **Themes:** light/orange and black/orange, remembered locally across reloads.
- **Budget Runway:** optional caller task list, p50/p90 cost scenarios, early warnings,
  planning options, task progress, and a runway activity filter. **Try demo** exercises
  early local fallback while the original sandbox demo preserves the hard-stop beat.
  See [runway details](runway.md).

Only one dashboard run executes at a time. The page polls the ledger while open;
closing the page does not cancel a run. Stopping the server can interrupt the
worker, but already persisted payment holds remain reserved. There is no browser
resume action yet; the existing CLI remains available for recovery.

## Local access boundary

This is a development interface, bound to `127.0.0.1`. It validates Host and Origin,
rejects cross-site requests, requires same-origin JSON for launching tasks, serves
only packaged static assets, and applies a restrictive Content Security Policy.
It does not add SAML, user accounts, cookies, JWTs, or multi-user authorization.
Local users/processes remain trusted. Do not expose it as a hosted app without an
appropriate server and authentication layer. Python documents `http.server` as
[unsuitable for production](https://docs.python.org/3/library/http.server.html).

Google credentials, wallet keys and signed payment payloads stay on the Python
side. Mock runs simulate payments. Explicit `vendor-demo` and `gemini-devnet` runs
transfer real Devnet USDC to the allowlisted local merchant through the budget
gate. Session payment mode is immutable. There is no arbitrary transfer endpoint.
Gemini inference costs remain separate from the USDC allowance.

## Verification

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest -q
```

`test_web.py` covers the HTTP browser boundary, invalid inputs, single-run limit,
actual sandbox budget outcomes, exports, restart persistence, and absent wallets.
The UI has also been exercised in Chrome at desktop and mobile sizes: navigation,
theme persistence, task launch, refusal filters, report download, session search,
and live wallet reads. All five views were checked at 360, 390, 768, 1024 and 1440px
without horizontal page overflow or browser console errors. A live Gemini task
returned the 10000 atomic USDC budget through the UI, and the wallet view verified
20 devnet USDC. Additional discovery tests exercise the scout's concurrency, ranking,
failure boundaries and parent handoff. Live Gemini/Bazaar runs showed planning,
searching, ranking and recommendation in Chrome at desktop and mobile sizes with
no page overflow or JavaScript errors; the USDC budget stayed untouched.
Payment tests additionally cover x402 challenge/signature/receipt handling,
idempotency, uncertain holds, reconciliation, and the merchant HTTP boundary.

See [artwork.md](artwork.md) for the exact built-in imagegen prompts and font
license locations. The transparent PNGs are packaged at
`src/governor/static/assets/guardian.png` and `src/governor/static/assets/vault.png`.
