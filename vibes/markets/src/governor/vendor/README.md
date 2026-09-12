# Local demo vendor

This subfolder implements the merchant side of a real x402 v2 Solana Devnet payment.
The service is a demo extractive summarizer. Its USDC transfers are on-chain.

From `vibes/markets`, use two terminals:

```bash
.venv/bin/governor-vendor
.venv/bin/governor-web
```

The seller console is <http://127.0.0.1:8788>, and the buyer's Services page is
<http://127.0.0.1:8787/#services>. Ports are fixed for this local demo. Both servers
must use the same `--data-dir` (default `.governor`); state directories are private
and ignored by Git. The merchant HTTP server is for local development only.

## Account setup

The vendor command creates its own keypair at `.governor/vendor/wallet.json` with
owner-only permissions and pins its public identity in `public.json`. It never
reads the buyer key. The buyer adapter reads only the pinned vendor public file
and its own `.governor/wallets/solana-devnet.json` keypair.

Before the first purchase, the vendor needs an associated USDC token account.
Claim **USDC → Solana Devnet** at <https://faucet.circle.com/> using the address
shown on the vendor page. This creates/funds the token account. Faucet deposits
are setup funds: they increase wallet balance but never count as order revenue.
The buyer also needs funded Devnet USDC. The x402 facilitator sponsors transaction
fees; neither demo wallet needs SOL for the payment itself.

## Purchase

In Governor's **Services** page, **Pay 0.002 USDC · one-call demo** runs one purchase
through the actual agent tool registry, budget ledger, signing adapter, local HTTP
merchant and public testnet facilitator. It uses a deterministic model so the
payment demonstration does not require a Gemini request.

**Use Gemini + Devnet payment** opens a Gemini task with real Devnet payment mode.
Only `vendor-summary` is purchasable in that mode. The old sandbox catalog remains
available in mock modes. Bazaar discovery never adds arbitrary sellers to the
signing allowlist. A session cannot change payment modes on resume.

The CLI also supports this adapter through operator configuration:

```bash
GOVERNOR_PAYMENT_MODE=solana-devnet .venv/bin/governor run \
  "Use vendor-summary to summarize: Agents buy services. Governor enforces the budget."
```

## Payment protocol

1. Buyer requests `POST /api/summary` with deterministic order ID and text.
2. Merchant returns HTTP 402 and the standard `PAYMENT-REQUIRED` header. It quotes
   exactly 2,000 atomic USDC, pins its receiving wallet, and binds the order in a memo.
3. Governor validates x402 version, scheme, Devnet network, USDC mint, recipient,
   resource, timeout and memo. The existing ledger checks caps and reserves funds
   before signing. An advertised-price increase is refused.
4. Preflight checks the Devnet genesis and both associated token accounts. A setup
   failure releases the unsigned hold. The signer verifies the facilitator's fee
   payer and builds only the supported compute-budget, TransferChecked and memo
   instructions. The buyer signs its part of the transaction.
5. The signed payload is saved privately before submission and sent in the
   `PAYMENT-SIGNATURE` header. The merchant atomically claims the order, checks the
   exact transaction layout and buyer signature, then calls the facilitator's
   `/verify` and `/settle` once.
6. The merchant reads the confirmed on-chain transaction and checks its signatures
   and complete message hash. Only then does it count revenue and return output
   with `PAYMENT-RESPONSE`. The buyer independently performs the same chain check
   before committing the reserved amount to its ledger.

The transaction transfers exactly 0.002 USDC. No instruction can move native SOL
from the buyer, approve a delegate, change the recipient or transfer another token.
The network is fixed to `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1`, and the mint is
`4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU` (six decimals).

## Pending payments and replay

After possible signature exposure, an error retains the buyer's hold. The adapter
does not automatically resubmit or generate a new transaction. **Recheck receipt**
reads the existing merchant order and, if needed, searches recent recipient
transactions for its memo and exact message hash. Confirmed evidence reconciles
the existing hold; lack of evidence leaves it pending. It never authorizes a new
payment or releases a potentially signed hold.

The buyer button is in session details; the seller page also offers a receipt
check for pending orders. A seller-only check updates seller evidence; the buyer
check then reconciles the spending ledger. The original model answer remains a
historical record; current ledger/receipt state is authoritative.

Identical service/text calls within a session reuse the original purchase. A
merchant order rejects different text or a different signed transaction under
the same ID. Concurrent submissions cannot cause a second facilitator settlement.
Recovery scans are bounded to the twenty most recent recipient transactions; an
older uncertain payment can require operator investigation. No timeout releases
funds automatically, and no claim of failure is treated as proof of nonpayment.

## Files and verification

- `server.py`: local merchant, durable orders and receipt reconciliation.
- `static/`: separate white/orange and black/orange seller console.
- `../live_payments.py`: allowlisted buyer adapter and private signing outbox.
- `../devnet_chain.py`: narrow SVM construction and confirmed transaction checks.
- `.governor/vendor/orders.sqlite3`: order evidence and earned revenue.
- `.governor/payments/reports/`: private signed payloads for recovery; never served
  to the browser, agent model, or public session audit.

The pinned `x402==2.22.0` package supplies wire schemas and HTTP-header encoding.
Transaction construction uses the existing `solders==0.29.0` primitives and the
reference exact-SVM layout. The current Python x402 SVM extra imports the removed
`solana.rpc.api` path with the latest Solana package, so this implementation does
not install that incompatible extra or use an automatic-payment HTTP wrapper.

Offline integration tests exercise the actual signing/HTTP/ledger code against
mocked facilitator/RPC responses, including cap refusals, changed quotes, replay,
network mismatch, missing accounts and lost-response reconciliation. Those tests
do not prove a live transfer; on-chain verification is a separate step.

Sources: [x402 seller quickstart](https://docs.x402.org/getting-started/quickstart-for-sellers),
[exact-SVM specification](https://github.com/x402-foundation/x402/blob/main/specs/schemes/exact/scheme_exact_svm.md).

## Verified live purchase — 12 September 2026

Session `vendor-demo-756c2e6bd6` completed a real 0.002 USDC purchase through the
merchant and x402 facilitator. Both adapters verified transaction
[`49FyjsTH…7ED1ZX`](https://explorer.solana.com/tx/49FyjsTH6ejZ76PU7Jrm7jSys9m15xaPFXcGUv2MFDRFkC6uRU7v4LPb3kWG6yeBd3uGu4xQmMamp27usn7ED1ZX?cluster=devnet)
at confirmed slot `497226439`.

| Evidence | Before | After |
|---|---:|---:|
| Buyer `6cNkKA…VMjDw3` USDC | 40.000 | 39.998 |
| Vendor `8nsF1n…LLj3mp` USDC | 20.000 | 20.002 |
| Vendor earned revenue USDC | 0.000 | 0.002 |
| Successful session settled / held atomic USDC | 0 / 0 | 2000 / 0 |

The initial development attempt (`vendor-demo-17d16c7b03`) was rejected by the
facilitator's simulation: its 20,000-compute-unit limit was insufficient for the
73-byte invoice memo. It transferred no USDC. A read-only RPC simulation confirmed
the failure, unchanged token balances, and expiry of its blockhash before a fresh
session was started. The fixed 40,000-unit transaction simulated successfully with
27,824 units consumed, then settled in the successful session above. The old
session's conservative 2000-unit hold and pending vendor row remain as audit
history; failed/expired hold release is not automated.

Chrome verification checked both pages at 1440px and 360px with no horizontal
overflow or JavaScript errors. The seller page showed one paid order, 0.002 earned,
and 20.002 wallet balance separately; buyer session details linked the same receipt.
