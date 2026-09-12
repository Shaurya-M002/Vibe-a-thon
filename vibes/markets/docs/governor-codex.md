# Run Codex inside Governor

`governor-codex` opens an orange-and-black interactive terminal with a prompt,
streaming Codex replies, expandable tool calls and live service-budget totals.
Codex runs inside it through its persistent app-server with ten native Governor
MCP tools. Codex remains the reasoning agent. The first prompt creates one
Governor budget session; follow-up prompts reuse that budget and conversation.

## Start

Install the updated package from `vibes/markets`:

```bash
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install -e . --no-deps
.venv/bin/governor-web
```

In another terminal:

```bash
# Open the interactive terminal and type a prompt.
governor-codex

# Or start with a prompt already supplied.
governor-codex "Summarize this project using Governor's approved tools"

# One task, returning control when Codex finishes.
governor-codex --exec "Read my Governor budget and summarize this text locally: Agents buy services. Governor enforces caps."

# Explicitly allow real Devnet purchases through the configured local merchant.
governor-codex --mode solana-devnet "Use vendor-summary to summarize this text: ..."
```

On this machine, `governor-codex` is installed in `~/.local/bin`. With the package
virtual environment activated, its installed console command works the same way.
You can always call `.venv/bin/governor-codex` directly from `vibes/markets`.

The app must be running. Real payments also require `governor-vendor` and funded
Devnet USDC accounts, as described in the [vendor setup](../src/governor/vendor/README.md).
Default mode is mock. Codex uses its existing login and configured model; no Gemini
call or Google credential is required by this path.

## Terminal controls

- **Enter** sends your prompt; **Up/Down** recalls earlier prompts.
- Replies stream as Codex writes, with bold white headings, orange emphasis and
  light green inline code and notes. Markdown paragraph spacing keeps longer
  explanations readable. Expand a tool row to inspect its arguments,
  command output, result or error. The budget refreshes every 1.5 seconds by default.
- **Esc** or `/stop` interrupts the current Codex turn. Existing payment holds stay.
- **Ctrl+O** or `/app` opens the same session in the web app.
- `/budget` shows ledger totals; `/help` lists commands.
- `/finish` saves the latest answer and closes the budget session. An interrupted
  turn is saved as `STOPPED`, rather than reported as a successful completion.
- **Ctrl+Q** or `/quit` exits, leaving the session open for later work.

Codex command and file-change approvals appear as explicit **Allow once / Deny**
prompts. User-input questions appear as forms. Unsupported app-server requests
are refused with an explanation; `--native` uses Codex's own UI for those workflows.
The terminal inherits your Codex login, model, sandbox and approval policy.
It respects `NO_COLOR` when set in your environment.

The app shows prompts and assistant messages when each message completes,
including intermediate commentary. Token deltas and command output stream inside
the terminal. Shell commands and Codex inference charges are outside Governor's
service-payment ledger. The service-budget panel labels that distinction.

## Adjustable parameters

Press **F2**, click **Settings**, or enter `/settings` while Codex is idle.

| Control | When it applies |
|---|---|
| Codex model ID and reasoning effort | Next prompt, on the same Codex thread |
| Payment mode and Governor tool approvals | Before the first prompt |
| Session budget and per-call cap, entered in USDC | Before session creation; enforced by the ledger |
| Maximum Governor tool calls | Entire session, including read-only budget and catalog calls |
| Tool timeout in seconds | Each Governor tool call; timeouts preserve uncertain holds |
| Comfortable/compact spacing, sidebar, expanded tool details | Immediately |
| Budget refresh interval: 1, 1.5, 3 or 5 seconds | Immediately |

Use a model ID available to your Codex account and an effort level that model
supports. Blank model/effort fields keep Codex's current setting. Codex reports an
unsupported value in the conversation; correct it in Settings before retrying.
Changing a model keeps the current thread and Governor budget.

The app's operator configuration supplies the maximum selectable limits. You can
choose smaller limits for a session; Settings cannot raise the operator ceiling.
For example, a 0.006 USDC budget and 0.002 USDC per-call cap remain fixed after
starting, even after reconnecting or restarting the app. The chosen limits appear
in the terminal budget panel and the app's session audit. Existing sessions keep
their original limits. Payment mode, caps, call count and tool timeout lock as
soon as session creation is attempted, including after a lost response.

Model and display choices apply to the current terminal instance. Reopened
sessions recover their budget limits from the ledger. For a different session
budget, leave the current session and launch `governor-codex` again. The new
session's budget is separate; it does not erase earlier spend or outstanding holds.

The header shows the selected model, mode and effort. While Codex runs, the status
line shows elapsed seconds and the Send button becomes **Stop**. These UI controls
use the [Codex app-server turn parameters](https://learn.chatgpt.com/docs/app-server).

## Loaded tools

| Purpose | Native Governor tools |
|---|---|
| Budget and planning | `get_budget`, `get_runway` |
| Approved services | `list_services`, `purchase_service`, `summarize_local` |
| Vendor discovery | `discover_vendors`, `get_vendor_search` |
| Session and receipts | `get_session`, `reconcile_receipt`, `finish_session` |

The launcher injects a required stdio MCP server through invocation-local `-c`
configuration. Codex fails startup if the bridge cannot initialize. No permanent
MCP entry or rewrite of the user's Codex config is needed. The bridge's session ID
is fixed by the launcher, outside model-supplied tool arguments. It cannot create a
new session, change payment mode or raise caps.

The built-in Governor connection uses `approve` tool approval mode by default.
This authorizes its tools within the selected session; the payment ledger still
checks every purchase against the operator caps. Use `--tool-approval writes` to
prompt for writes, or `--tool-approval prompt` to request approval for every tool.
Those prompting modes need a compatible Codex approval policy; an inherited
`never` policy refuses calls that would require a prompt. The override applies
only to this Governor connection. Shell sandbox and other tool settings are
inherited unless explicitly changed with launcher flags.
[Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

Bazaar searches are bounded to twelve listings per call, filtered and normalized
against the current network, token and budget. Codex assesses the results itself;
the Gemini scout is not invoked. The app shows search progress and results.
Search does not add sellers to the payment allowlist. Devnet purchases still
support only the explicitly configured local `vendor-summary` service.

## Options and recovery

- `--cwd PATH` selects Codex's workspace; it never changes the app's data directory.
- `--task-file FILE` and `--task-list FILE` accept the task and initial runway plan.
- `--model NAME`, `--profile NAME` and `--sandbox MODE` forward explicit choices to
  Codex. Without them, its existing configuration applies.
- `--json` with `--exec` streams Codex's JSONL events to stdout. Launcher session
  links and diagnostics go to stderr.
- `--url http://127.0.0.1:PORT` or `GOVERNOR_APP_URL` selects another local app port.
- `--session codex-ID` reopens an existing open Governor budget, preserving spend,
  holds and its original task plan. In the default terminal, it also resumes the
  saved Codex thread on this machine and restores mirrored messages from the app.
  Use the same app URL and `--cwd`. The thread mapping lives in Governor's local
  user-state directory, with owner-only file permissions.
- `--native` launches Codex's original terminal UI with Governor tools loaded.
  This fallback and `--exec` attach to the Governor budget without resuming the
  custom terminal's conversation; `get_session` supplies prior ledger evidence.

Stable `call_id` arguments preserve the existing plugin's retry protection. The
same ID and arguments return the saved result or pending state. Signed payment
uncertainty retains its hold. Receipt reconciliation only reads existing evidence.
Changing call IDs cannot bypass the purchase identity or cap checks.

In interactive mode, the session remains open for follow-up work until you use `/finish` or explicitly ask Codex to call
`finish_session`. Closing the TUI without finishing preserves the budget for a
later `--session` attachment. In `--exec` mode, Codex is instructed to submit its
final answer; if it exits successfully without doing so, the launcher saves its
last message as the session result. A failed or interrupted child does not clear
holds, reset the budget or invent a successful result.

Closing the terminal does not close its budget. A lost create response retains
the same session ID and request payload, so retrying cannot allocate a fresh cap.
An unavailable web app is reported inside the terminal; start `governor-web`
and retry your prompt.

## Verification

Settings were exercised with real Codex in session `codex-8f68f3c4ca494b90`:
0.006 USDC total, 0.002 USDC per call, six Governor calls maximum, a 30-second
per-tool timeout, and medium reasoning effort. Codex read back the selected caps;
the completed app report retained those limits, with zero settled or held USDC.


The interactive terminal was exercised with real Codex CLI 0.154.0: native
`get_budget` and `list_services` calls, streamed answers, and a second prompt
that recalled the first prompt's word on the same Codex thread. `/finish` saved
the final answer. The budget stayed at 10,000 atomic USDC, with zero spend or holds.
Terminal tests cover typed prompts, deltas, tool rows, explicit approvals,
interruption, prompt history, compact layout, persisted thread resume, lost-create
response recovery and immutable payment mode. Conversation messages have durable,
idempotent app records and never consume a payment-tool allowance.

The terminal uses the official
[Codex app-server protocol](https://learn.chatgpt.com/docs/app-server) over stdio.


The real Codex CLI 0.154.0 was launched through `governor-codex --exec --json` using
the installed account and a read-only shell sandbox. Session
`codex-adf0dfa3eed6409b` called the native `get_budget`, `list_services`,
`summarize_local` and `finish_session` tools successfully. Its app report finished
with 10,000 atomic USDC available, zero settled and zero held. This smoke test
made no purchase or Gemini request.

Automated tests cover the stdio handshake, complete native tool catalog, session
binding, malformed calls, idempotent retries, launcher argument quoting, child
exit handling, final-answer capture, attachment without budget reset, and Bazaar
search without model inference. The bridge implements the bounded JSON-lines
[MCP stdio transport](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports)
and [tools protocol](https://modelcontextprotocol.io/specification/2024-11-05/server/tools).
