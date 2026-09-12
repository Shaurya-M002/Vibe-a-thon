# Budget Runway

Runway plans ahead; the ledger enforces the spending cap. A `SHORTFALL` forecast
cannot reject an otherwise allowed purchase, and a `HEALTHY` forecast cannot
authorize a refused one. The estimator imports no ledger or payment adapter.

## Try the demo

From `vibes/markets`:

```bash
.venv/bin/governor runway-demo --session runway-1
.venv/bin/governor report runway-1
```

Or open the local dashboard and click **Budget runway → Try demo**. The original
**Run sandbox demo** still exercises hard caps and uncertain settlement separately.

With default caps (10000 session / 3000 per call), three summaries settle for 6000
atomic USDC. Seven remaining items project to 14000 at p90, against 4000 available.
The scripted planner reads the `SHORTFALL` option and uses previously approved local
extraction for those seven items. All ten complete with 4000 left. It also requests
an overpriced service to demonstrate that the original cap still refuses it.
Payments are simulated, including with live Gemini inference.

## Supply the workload

Runway never extracts a task count from model prose. Pass a JSON array using
`governor run "Process the caller task list in order" --task-list tasks.json`, or
send `task_list` in the dashboard's `POST /api/runs` body. Each item is one text
to process, with a unique ID and exact text:

```json
[
  {"id": "a", "type": "summary", "text": "Document A. First item.", "allow_local": true},
  {"id": "b", "type": "summary", "text": "Document B. Second item.", "allow_local": true},
  {"id": "c", "type": "summary", "text": "Document C. Third item.", "allow_local": false},
  {"id": "d", "type": "summary", "text": "Document D. Fourth item.", "allow_local": true}
]
```

The launcher accepts one `type | text` item per line under **Optional task list**.
Text without a type defaults to `summary`. The checkbox explicitly approves
extractive fallback; its default is unchecked. The CLI/API support permissions
per item. Lists allow at most 100 items / 60000 combined characters. IDs and texts
must be unique because identical purchases are cached in the existing runtime.

Caller order defines priority. Plans are persisted with the session and cannot be
replaced on resume. The agent receives the exact plan and is instructed to preserve
each item text. Successful settled results match the existing deterministic
purchase identities; local results count only when explicitly permitted. Replays
do not count twice. Refused and uncertain paid attempts do not complete an item.
If a model stops with items outstanding, its status is `PLAN_INCOMPLETE`.

## Estimator and confidence

- Use available budget after both settled spend and outstanding holds.
- Require at least three known paid-cost observations. Below that, emit `UNKNOWN`
  with no burn rate, projected cost, shortfall, or affordable-task count.
- Compute empirical nearest-rank p50 and p90, plus exact population variance
  represented as a rational number in atomic-unit-squared terms. No floating-point
  arithmetic is used for costs, thresholds, or scope calculations.
- Keep separate histories per caller task type. Each remaining type needs three
  samples; a new or undersampled type keeps the completion forecast `UNKNOWN`.
- Local completion advances progress but does not dilute paid cost distributions.
  A local result with an unresolved paid attempt is not a known total-cost sample.
- Sum per-type scenario costs over the caller's remaining mix. Weighted per-task
  rates round upwards. Without a task list, use the min-p50/max-p90 envelope across
  observed service types and report affordable calls only, keeping state `UNKNOWN`.
- Classify using p90: ≤70% of available is `HEALTHY`, >70% through 100% is `TIGHT`,
  and >100% is `SHORTFALL`. A zero observed rate has no finite `runwayTasks` value
  (`null`); no infinity or NaN enters JSON.

These are empirical cost scenarios, not calibrated confidence intervals or future
seller quotes. Three observations establish a minimal baseline, not high certainty.
Gemini inference, hosting costs, and future unconfirmed charges are not included
in the simulated purchase-cost forecast.

## Advice and audit

Every settlement and refusal recomputes runway. Reserves, releases, initial plan
attachment, and approved local completions refresh it too. The same SQLite
transaction records `RUNWAY_CHECK` with its causal event sequence and calculation
inputs. `RUNWAY_STATE_CHANGED` is emitted only when the state changes. The report,
`get_runway` tool, every successful tool response, payment result, and final agent
result expose the latest forecast. Existing sessions without checks show `UNKNOWN`
until new qualifying activity; no workload is invented for historical sessions.

Options are proposals, not new spending permissions:

- **route-local:** estimated avoided p90 payment cost for caller-approved items;
  unapproved fallback requires a request. Quality deltas remain `null` because no
  quality benchmark exists. Local computation is not represented as financially free.
- **compare-quotes:** request prices before choosing a cheaper paid provider; this
  change does not add a quote-fetching adapter or a marketplace.
- **reduce-scope / prioritized-subset:** propose the affordable prefix of the
  caller's ordered list; require approval before dropping anything.
- **raise-cap:** request the exact shortfall; no cap-mutation endpoint or automatic
  grant is added. Existing sessions retain their original immutable policy.

In the feature's illustrative 39000-remaining / 9800-p90 example, dropping two of
six tasks still costs 39200. The implementation therefore proposes dropping three
and requests 19800 for a full-workload cap increase, rather than inventing rounded
amounts or quality changes.

Forecast computation failures produce `UNKNOWN` without changing the ledger's
decision. Ledger integrity and audit-write failures retain the existing fail-closed
behavior; advisory isolation does not bypass corrupt accounting storage.

Tests cover FR-16–FR-20, percentile and threshold boundaries, mixed types, replay,
restart/hold persistence, forecast failures, optimistic forecasts against hard caps,
allowed payments during shortfall, caller permissions, and the complete demo.
A live Gemini browser run also finished six caller-listed items: three simulated
paid summaries, followed by three approved local results after the early shortfall.

Validation: 90 automated tests and lint pass. Chrome checks covered the visible shortfall
options, completion, both themes, and widths from 360 to 1440px without page overflow
or browser console errors. No new dependencies were added.
