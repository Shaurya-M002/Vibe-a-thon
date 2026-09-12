---
name: governor
description: Use Governor budget and paid-service tools directly from Codex, with live session details in the local Governor app. Use when tracking a task in Governor, buying an approved service, checking runway, or inspecting payment receipts.
---

Codex is the reasoning agent. Governor supplies the ledger and tools; do not
delegate this workflow to Gemini. The app shows Governor-mediated tool calls and
the final answer you submit, not the entire Codex conversation or unrelated shell activity.

The executable is [../../scripts/governor.py](../../scripts/governor.py), relative
to this skill. Resolve its absolute path from this installed skill location. Run
it with `python3` from any working directory; it needs no pip packages. Below,
`CLI` means that absolute script path, quoted as a shell argument.

Run `python3 CLI doctor` first. The default app is `http://127.0.0.1:8787`;
`--url http://127.0.0.1:PORT` before the command overrides it. An unavailable app
needs `governor-web` started from the user's configured Governor runtime directory.
Do not create a replacement ledger or load credentials from unrelated projects.

Create one session for the user's task. Generate an ID with `python3 CLI new-id`,
retain it, and use `start --session ID --task-file /absolute/task.txt`.
Optional `--task-list /absolute/plan.json` attaches the caller's runway plan at
creation. Default payment mode is `mock`. Use `--mode solana-devnet` when the user
has authorized Devnet purchases; this transfers wallet USDC to the local vendor.
The resulting `app_url` opens this session; share it with the user early.

Call tools yourself with stable call IDs:

```bash
python3 CLI call SESSION --call-id budget-1 --tool get_budget
python3 CLI call SESSION --call-id catalog-1 --tool list_services
python3 CLI call SESSION --call-id runway-1 --tool get_runway
python3 CLI call SESSION --call-id purchase-1 --tool purchase_service --arguments-file /absolute/purchase.json
```

Purchase JSON is `{"service_id":"vendor-summary","text":"Input to summarize."}`
for Devnet, or `summary` for the mock catalog. Only returned approved service IDs
are purchasable; Bazaar discovery does not expand this allowlist. A free fallback
is `summarize_local` with `{"text":"Input to summarize."}`. It extracts opening
sentences. Prefer argument files for long or user-supplied text to avoid shell
quoting errors. Tool responses are JSON with `ok`, `code`, and budget details.

The same session ID plus identical creation arguments returns the existing
session. The same call ID plus identical arguments returns its saved result or
`CALL_PENDING`; it never repeats the tool. After a timeout or lost response,
inspect `status SESSION` or retry those exact IDs and arguments. Do not create a
new session to bypass a refusal or reset a spent budget. A new call ID does not
bypass the existing service/text purchase identity either.

`status SESSION` returns the full report and tool results. `watch SESSION --seconds
30 --after LAST_SEQ` emits new events followed by a snapshot; repeat with the
returned `last_seq` when useful. `WAITING` means Governor is ready for Codex's
next call, not that a separate model is running. `INTERRUPTED` can indicate a tool
request whose result was lost during a server restart. Treat its payment as
uncertain until reconciled. `reconcile SESSION ATTEMPT_ID` reads existing Devnet
receipt evidence and never signs another payment. Holds remain until verified.

When the task is done, use `finish SESSION --answer-file /absolute/result.txt`.
Use `--status STOPPED` when deliberately ending incomplete work. Finishing does
not clear holds or invent payment success. Check `ok`/`code` and confirmed ledger
receipts before claiming a purchase succeeded. Existing final results cannot be
replaced. `export SESSION --format json` or `csv` writes the audit to stdout.

Operator caps and the per-session tool-call limit remain authoritative. Neither
the plugin nor runway can raise them. Codex inference usage is outside Governor's
USDC ledger. No wallet keys, Google credentials, or signed payloads are needed in
Codex's context. Shell work outside these tools is not controlled by Governor.
