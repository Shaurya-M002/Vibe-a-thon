# Governor agent

A Gemini agent that can discover approved services, request purchases through a
deterministic budget gate, and fall back to a local excerpt. Based on the
[Governor PRD](PRD.md). Work lives on `vibes/markets` in
`AmRitJain0442/Vibe-a-thon`.

**Implemented:** Gemini tool calling, strict tool inputs, run limits, SQLite
reservations, restart-safe purchase identities, offline scenarios, audit exports,
Budget Runway, and live Bazaar discovery with a parallel Gemini vendor scout.
**Payment adapters:** sandbox simulation and real x402 Solana Devnet payments to
the separately allowlisted local demo vendor. Sandbox remains the default.
The vendor adapter signs with the existing buyer wallet, verifies chain receipts,
and records actual USDC spend. Arbitrary Bazaar sellers are not yet purchasable.
`run` uses real Gemini inference; the original `demo` stays fully offline.

## Codex CLI plugin

Run `governor-codex` for an interactive orange-and-black terminal with streamed
Codex replies, expandable tool activity and a live budget. Governor tools are already
loaded. Use `--exec` for a single noninteractive task. See the
[embedded Codex launcher](docs/governor-codex.md).

Codex can directly use Governor's budget and paid-service tools while remaining
the reasoning agent. The app shows its session, calls, spending and final answer.
See [plugin setup and usage](docs/codex-plugin.md); source lives in
[`plugins/governor`](plugins/governor).

For the active Solana Devnet configuration, see [wallet and faucet setup](docs/solana-devnet.md).

## Local dashboard

From `vibes/markets`, run `.venv/bin/pip install -e . --no-deps`, then
`.venv/bin/governor-web` and open <http://127.0.0.1:8787>.
The pixel-style control room includes light/dark orange themes, Gemini task launch,
an offline demo, session budgets and activity, JSON/CSV downloads, and live devnet
wallet balances. No frontend build step is required.
See [dashboard usage and access boundary](docs/dashboard.md).

## Demo vendor with real Devnet payments

Start `.venv/bin/governor-vendor` in a second terminal, then open
<http://127.0.0.1:8788>. The merchant has a separate wallet and an incoming-order
console. In Governor's **Services** page, use the one-call **Pay 0.002 USDC** demo
or launch **Gemini + Devnet payment**. Initialize the vendor's USDC token account
before the first purchase. See [vendor setup, protocol and recovery](src/governor/vendor/README.md).

## Bazaar vendor scout

Open **Services** to search live Bazaar listings, or enable the scout in the Gemini
launcher to search alongside the main agent. The frontend shows parallel queries,
candidate prices, task-fit assessments, budget compatibility and a recommendation.
CLI: `governor run "Find a Devnet circuit breaker API" --discover-vendors`.
Discovered sellers are advisory listings; their purchases are not connected yet.
See [discovery behavior, ranking and limits](docs/bazaar.md).

## Budget Runway

`governor runway-demo` demonstrates an early p90 shortfall warning and finishes ten
caller-listed items through approved local fallback, with budget left over.
For live Gemini runs, pass `--task-list tasks.json` or add the optional task list in
the dashboard launcher. Runway forecasts are advisory; spending caps are unchanged.
See [runway inputs, confidence and demo](docs/runway.md).

## Setup

Python 3.12 or newer; tested with Python 3.14. From the repository root:

```bash
cd vibes/markets
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
cp .env.example .env
```

`requirements.lock` pins the tested runtime and development dependencies. For
dependency development, use `pip install -e '.[dev]'`, then regenerate the lock with
`pip freeze --exclude-editable > requirements.lock` after validation.

## Offline demo

```bash
governor demo --session demo-1
governor report demo-1
governor report demo-1 --format csv
governor demo --session demo-1 --resume
```

With default limits, the first run demonstrates:

1. Listing services and inspecting the budget.
2. Paying for a simulated summary.
3. Refusing a per-call cap violation before authorization.
4. Refusing a price increase above the advertised quote.
5. Retaining a reservation after a simulated lost settlement response.
6. Spending the remaining allowance and refusing the next purchase.
7. Producing a local excerpt without another payment.

The final budget is **8000 settled + 2000 held + 0 available = 10000 atomic USDC**.
There are five simulated authorizations: four settled purchases and one unresolved
attempt. On resume, settled outputs are reused and the pending attempt stays held;
the simulated authorization count for the resumed run is zero.

Use a new session name for a new task. Reusing a name without `--resume` is an error.
The scenario follows the configured limits, so changing them changes its outcomes.

## Run with Gemini

For the Gemini Developer API, edit the local `.env`:

```dotenv
GEMINI_BACKEND=developer
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.5-flash
```

Then run:

```bash
governor run "Use the summary service for this text: Agents buy services. Governor enforces a budget. Preserve funds on ambiguous failures." --session research-1
governor resume research-1
```

The model ID is configurable. Availability depends on the selected API and project.
Missing credentials cause an explicit error; the CLI never silently substitutes
the scripted model for Gemini.

For Google Cloud authentication:

The current development project is configured and has passed a live Gemini tool
call. See [Google Cloud connection](docs/gcp.md) for the project and reproducible setup.

```bash
gcloud auth application-default login
```

```dotenv
GEMINI_BACKEND=vertex
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
```

The project needs billing and access to the Gemini model API. On a hosted workload,
use its service identity via Application Default Credentials. The `vertex` backend
selects the Google Cloud path using the current SDK's `enterprise=True` option;
it does not send a Developer API key. See the
[Google Gen AI SDK authentication documentation](https://googleapis.github.io/python-genai/).

Model and hosting charges are **outside** Governor's simulated USDC allowance.

## Configuration

The CLI loads `.env` only from the working directory. Shell variables take precedence.

| Variable | Default | Meaning |
|---|---|---|
| `GOVERNOR_SESSION_CAP` | `10000` | Total purchase allowance in atomic USDC |
| `GOVERNOR_PER_CALL_CAP` | `3000` | Maximum per purchase |
| `GOVERNOR_MAX_TURNS` | `8` | Maximum parent model requests; scout adds at most two |
| `GOVERNOR_MAX_TOOL_CALLS` | `16` | Maximum tool executions per invocation |
| `GOVERNOR_RUN_TIMEOUT_SECONDS` | `120` | Deadline for the whole invocation |
| `GOVERNOR_MODEL_TIMEOUT_SECONDS` | `30` | Deadline for one model request |
| `GOVERNOR_DATA_DIR` | `.governor` | Local ledger and report directory |
| `GOVERNOR_PAYMENT_MODE` | `mock` | CLI adapter: `mock` or local-vendor `solana-devnet` |

One USDC is 1,000,000 atomic units. Monetary inputs must be canonical decimal
integer strings: `"2000"`, never `0.002`, scientific notation, or a float.
`--data-dir /path/to/state` is available on every command. Use the same persistent
directory and budget configuration when resuming.

## Architecture and behavior

```text
CLI → Gemini model → bounded agent loop → validated tool registry
                                             ├── list_services
                                             ├── get_budget
                                             ├── get_runway
                                             ├── discover_vendors → bounded scout → parallel Bazaar searches
                                             ├── get_vendor_search → advisory shortlist
                                             ├── summarize_local
                                             └── purchase_service
                                                   ↓
                                              PaymentGate
                                                   ↓
                                    SQLite reserve → mock authorize → settle
```

- The SDK receives tool declarations, not executable Python functions. Automatic
  tool execution is disabled. Every model-requested purchase passes through the gate.
- Full model content is preserved between turns, including thought signatures and
  function-call IDs, following Google's
  [function-calling context guidance](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures).
- A model's text or tool output cannot change the operator's budget configuration.
  Only named tools are available; there is no shell, arbitrary URL fetch, or key tool.
  Live runs also expose the read-only scout; discovered IDs do not join the payment allowlist.
- The ledger performs check-and-reserve inside a SQLite `BEGIN IMMEDIATE` transaction.
  Multiple local processes using the same ledger share one allowance.
- The invariant is `session_cap = settled + held + available`. Caps come from config;
  the persisted policy fingerprint detects configuration changes on resume.
- A reservation is durably marked `AUTHORIZING` before the adapter is called.
  Timeouts, cancellation, or ambiguous failures keep the hold. Only reservations
  still known to be unsigned can be released locally.
- Settlement amounts must exactly match the reservation. Mismatches are logged;
  the amount is never silently clamped.
- Identical `(session, service, text)` purchases reuse the original attempt. Settled
  outputs are cached; pending attempts are not reauthorized. A deliberate repeat
  purchase of identical input requires a new task session in this first version.
- Resume restores the budget and previous payment outcomes, with a fresh model
  conversation. It does not replay a persisted Gemini transcript.
- `COMPLETED` means the model ended its turn with an answer; it is not independent
  proof that the user's task was successfully completed.

The ledger and audit contain task text and service outputs. They stay under the
ignored `.governor/` directory by default. Reports are written atomically to
`.governor/reports/<session>.json`; CSV exports contain settled expenses only.
Mock receipts start with `mock:`. Devnet receipts contain actual transaction
signatures and link to Solana Explorer; signed payloads are stored privately.

## Checks

```bash
ruff check .
ruff format --check .
pytest -q
```

Tests cover cap boundaries, concurrent reservations, malformed prices, refusals
before authorization, policy-change rejection, missing/corrupt state, duplicate
purchases, cancellation, retained holds, tool validation, model/tool limits,
Gemini request serialization, and CLI demo/resume/export behavior. The SDK test
uses an HTTP mock; it does not require an API key or call Google.

## Next integration steps

1. Extend the now-working local vendor adapter to explicitly approved external
   Bazaar sellers, including their input schemas and fresh quote validation.
2. Broaden receipt reconciliation beyond the bounded local-vendor recovery path.
   There is no automatic timeout release or reset command.
3. Isolate the signing key and budget writes in a separate service before allowing
   an agent to execute arbitrary code. A local Python wrapper does not protect
   against a process that can modify its own code, database, or credentials.
4. Replace local SQLite with transactional shared storage for Cloud Run deployment.
   The current ledger requires a persistent local disk; do not rely on an ephemeral
   container filesystem for spending state.
