# Governor CLI plugin for Codex

To launch Codex with native Governor tools preloaded, use
[`governor-codex`](governor-codex.md). The standalone plugin described below remains
available for existing Codex threads that call Governor through its Python CLI.

Codex remains the reasoning agent. It opens a Governor session and calls the same
budget, runway, service-purchase and local-summary tools used by the runtime.
Governor records each request, result, refusal and payment in its app ledger.
This path makes **no Gemini calls** and needs no Gemini credentials.

The plugin source is [`plugins/governor`](../plugins/governor). It contains a Codex
manifest, a usage skill, and a Python standard-library CLI. It works from any
working directory and communicates only with a loopback Governor app. The plugin
does not read local wallet keys, signed payloads or Google credentials.

## Use in Codex

The local installation is `governor@personal`. Start a new Codex thread after
installation, then ask:

> Use the Governor plugin for this task. Keep reasoning in Codex, check my budget,
> use an approved summary service in mock mode, and give me the app session link.

To make real Devnet purchases instead, explicitly choose `solana-devnet` mode and
start the local merchant using the [vendor guide](../src/governor/vendor/README.md).
The only approved live service is `vendor-summary`, priced at 0.002 USDC per call.
The plugin itself does not turn discovered Bazaar sellers into payable services.

Keep `governor-web` running from the configured runtime directory. The CLI uses
`http://127.0.0.1:8787`, or `GOVERNOR_APP_URL` / a global `--url` flag for another
loopback port. It uses the running app's ledger, wallet and operator policy;
it never creates a second data directory based on Codex's current repository.

## Run the CLI directly

From `vibes/markets` (replace the script path with its absolute path elsewhere):

```bash
python3 plugins/governor/scripts/governor.py doctor
python3 plugins/governor/scripts/governor.py new-id
python3 plugins/governor/scripts/governor.py start --session codex-example --task "Summarize this input through Governor"
python3 plugins/governor/scripts/governor.py call codex-example --call-id budget-1 --tool get_budget
python3 plugins/governor/scripts/governor.py call codex-example --call-id services-1 --tool list_services
python3 plugins/governor/scripts/governor.py call codex-example --call-id summary-1 --tool purchase_service --arguments '{"service_id":"summary","text":"Codex reasons. Governor enforces the budget."}'
python3 plugins/governor/scripts/governor.py status codex-example
python3 plugins/governor/scripts/governor.py finish codex-example --answer "Completed the summary."
python3 plugins/governor/scripts/governor.py export codex-example --format csv
```

Use the generated ID for each new task. Retain that same ID for every operation on
the task. `--task-file`, `--arguments-file` and `--answer-file` accept UTF-8 files,
avoiding command-line quoting problems. `start --task-list plan.json` accepts the
existing immutable [runway task-list schema](runway.md).

Commands return JSON, except CSV exports and `watch`, which emits newline-delimited
JSON events followed by a snapshot. `watch SESSION --seconds 30 --after SEQUENCE`
can be continued with the returned `last_seq`. A successful CLI HTTP exchange uses
exit 0; inspect a tool's `ok` and `code` to distinguish success, refusal and pending
payment. CLI errors use exit 2 and JSON on stderr. Interrupting a watcher does not
stop work in the app.

## Session behavior

| State | Meaning |
|---|---|
| `WAITING` | Ready for Codex's next tool call or final result |
| `RUNNING` | Governor is executing a Codex tool call |
| `INTERRUPTED` | A durable tool request has no result after a server restart |
| `COMPLETED` | Codex submitted its final answer |
| `STOPPED` | Codex deliberately ended the task incomplete |

The app labels these sessions **CODEX / CLI PLUGIN**. Session details show expandable
tool results, budget, runway, payment attempts, on-chain receipts when applicable,
activity and the submitted final answer. Only Governor-mediated work is recorded;
this is not an automatic mirror of Codex's conversation, shell commands or token
usage. Codex inference costs are outside the USDC ledger.

## Retry and enforcement rules

- Creation binds the supplied `codex-…` session ID to the original task, mode and
  plan hash. An identical retry returns the existing session, including after
  restart or completion. Conflicting input is rejected without creating a budget.
- Each call needs a stable `call_id`. The request is persisted before executing
  the tool. Identical retries return the result or `CALL_PENDING`; they do not
  run the tool again. Different arguments under the same call ID are rejected.
- A lost HTTP response is not evidence that a payment failed. Inspect the report
  and use `reconcile SESSION ATTEMPT_ID` for existing Devnet receipt evidence.
  The reconciliation command never signs another payment.
- Session and per-call caps remain in the existing ledger. The operator's tool
  call limit counts all new calls, including reads. Calls are serialized with
  active dashboard runs; a busy response can be retried with the same identifiers.
- `finish` stores the final answer durably and closes the session to new calls.
  It never clears holds or changes a failed payment into a success. Existing
  final answers cannot be replaced, while identical finish retries are safe.
- The runtime remains a trusted local-operator app. These endpoints retain its
  Host, Origin and JSON checks; they do not add user accounts or hosted auth.

## Installation and verification

On this machine the plugin source is copied to `~/plugins/governor`, with an entry
in `~/.agents/plugins/marketplace.json`. `codex plugin add governor@personal` installs
it into Codex's cache. Source edits in the repository do not automatically change
that cached installation: update the local source, refresh its version cachebuster,
and reinstall before testing in a new thread.

The package follows the [official plugin architecture](https://learn.chatgpt.com/docs/plugins)
and [skill packaging guidance](https://learn.chatgpt.com/docs/build-skills).

Validation includes 177 passing tests, plugin/skill manifest checks, Ruff lint and
formatting, and Chrome checks at 1440px and 360px. The installed CLI was run from
`/tmp` against the app. Session `codex-plugin-verification` recorded a budget read,
one mock summary purchase, an identical retry with the same receipt, and Codex's
final answer. It settled 2000 simulated atomic USDC and retained 8000 available.
This plugin verification made no Gemini call or new on-chain transfer.
