# Bazaar vendor discovery

Governor now discovers live x402 listings through Coinbase Bazaar. A bounded Gemini
vendor scout plans searches in its own context, queries the registry concurrently,
assesses task fit, and produces an advisory shortlist. It has no payment, signing,
seller execution, recursive delegation, or policy-write tools.

## Use it

In the dashboard, open **Services**, describe the capability, and select **Find
vendors**. This creates a discovery-only session with live progress and a saved
audit. For example: `Find an API to check whether an agent circuit breaker permits
another call.` Google credentials are required, as for normal Gemini runs.

The task launcher also enables **Run a vendor scout in parallel** by default for
Gemini runs. The main agent can read its budget and do other work while the scout
searches. It obtains findings through `get_vendor_search` before its final answer.
Turning off automatic scouting still lets the main agent explicitly request one
pass with `discover_vendors`; the two offline demos expose neither discovery tool.

From the CLI:

```bash
governor run "Find a Devnet circuit breaker checking API. Do not purchase anything." --discover-vendors
```

Without the flag, the agent can still choose the discovery tool when its task
requires it. Each invocation gets at most one scout pass; further starts reuse it.
A resumed CLI invocation can do a fresh pass; past discovery events remain in the
ledger. This does not reset the session's spending allowance.

## What the scout compares

The registry request filters on x402 `exact`, Solana Devnet's CAIP-2 identifier and
Circle's Devnet USDC mint. Returned metadata is independently checked against those
values and x402 v2. Prices use canonical integer atomic units, never float parsing.
Candidate identities are stable hashes of resource URLs; duplicate resources are
merged. Round-robin sampling across branches limits assessment to twelve endpoints.

Gemini estimates task fit from the task and sanitized listing description, URL and
HTTP method. It must return known candidate IDs and a brief supporting reason.
This is an assessment of advertised capability, not a measured service-quality score.

An eligible recommendation needs all of:

- A supported advertised network, token, scheme and protocol version.
- An advertised price within both the per-call cap and current available budget.
- Task fit of at least 60/100.

Eligible candidates are ranked by `0.7 * task_fit + price_score + usage_score`.
The price contribution is `floor(20 * (ceiling - amount) / ceiling)` where ceiling
is the smaller current budget limit; usage contributes one point per reported
unique payer in the last thirty days, capped at ten. Thus task fit contributes
up to 70 points, cheaper prices up to 20, and reported usage up to 10. Missing
usage is zero contribution, not evidence of poor quality. Ties use stable IDs.
The scout refreshes the budget after assessment to account for concurrent work.

No eligible vendor produces `NO_MATCH`, with reasons. Failed or malformed fit
assessments produce no recommendation and an explicit assessment error. One failed
search can leave useful partial results; all failed searches produce `FAILED`.
Coverage is always bounded, and the registry's partial-result flag is preserved.
“Best” means best among these assessed listings, not a claim about the entire market.

## Runtime and visibility

- At most two extra Gemini calls, each capped at the smaller of twenty seconds
  and the configured model timeout; at most three concurrent registry requests.
- Each HTTP request has a twelve-second timeout, a 2 MB decoded response limit,
  no automatic retry, and no redirect following.
- The scout has a sixty-second deadline, further bounded by the configured run
  deadline. Parent exit cancels and joins outstanding child work.
- `GOVERNOR_MAX_TURNS` bounds parent calls. The two scout calls are additional;
  their input, output and thought-token counts are recorded separately. Gemini
  inference is billed outside the USDC payment allowance.
- Planning, each query start/finish, candidates, assessment usage, recommendation,
  failures and cancellation are persisted as `DISCOVERY_*` decision events.
- Services, Overview and session details render those snapshots with the existing
  1.5-second polling. The activity stream has a discovery filter. Closing the page
  leaves the server's bounded run active; restarting preserves recorded evidence.

The public registry receives the model-generated capability queries. The configured
Gemini backend receives the task and listing metadata for planning/assessment.
Queries are prompted to omit private content; they are visible in the audit.

## Discovery does not enable settlement

Every candidate has `payment_ready: false`. The scout contacts only the fixed
Coinbase discovery endpoint. It does not fetch seller URLs, run seller-provided
skills, submit task data to sellers, verify fresh 402 challenges, or purchase calls.
For this version, resource URLs must be public HTTPS endpoints without credentials,
query strings, fragments or nonstandard ports; other listings are skipped.

The four sandbox services are purchasable in mock sessions. Real Devnet sessions
allow only `vendor-summary`, the explicitly configured [local demo vendor](../src/governor/vendor/README.md).
That adapter validates live challenges, signs, settles through the facilitator,
and verifies on-chain receipts. A discovered Bazaar ID passed to `purchase_service`
is still refused; external seller onboarding remains unimplemented. Discovery
never expands the allowlist, changes a cap, or reserves any USDC.

## HTTP contract and checks

`POST /api/discovery` accepts only `{"query":"capability, 1–400 characters"}` and
returns `202 {"session_id":"discovery-…"}`. `POST /api/runs` additionally accepts
`discover: true|false` for Gemini; the API default is false. Both retain the
same-origin JSON boundary and single active dashboard run limit. Poll
`GET /api/sessions/<id>` for the `discovery` snapshot, events and final result.

Offline tests cover network/token/price validation, concurrent searches, bounded
responses, malformed model output, untrusted IDs, budget refresh, no-match/partial
failure, cancellation cleanup, parent-child handoff and HTTP/audit persistence.
Live Chrome verification exercised all four progress stages with Gemini and Bazaar,
recommended the circuit checking endpoint over heartbeat/sealing endpoints, and
left all 10,000 atomic USDC available. These runs verified discovery, not seller
availability or settlement.

Protocol sources: [Coinbase resource search API](https://docs.cdp.coinbase.com/api-reference/v2/rest-api/x402-facilitator/search-x402-resources)
and [x402 buyer discovery guidance](https://docs.x402.org/getting-started/quickstart-for-buyers).
