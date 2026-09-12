# Solana Devnet wallet and faucets

Governor's active test chain is now **Solana Devnet**, superseding the Base Sepolia
target in the original PRD. Solana also has a separate cluster called Testnet;
choose **Devnet** for this integration.

## Verified x402 configuration

Checked against the official documentation and the live facilitator `/supported`
endpoint on 12 September 2026:

| Setting | Value |
|---|---|
| x402 version / scheme | `2` / `exact` |
| Network | `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1` |
| RPC | `https://api.devnet.solana.com` |
| USDC mint | `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU` |
| USDC decimals | `6` |
| Facilitator | `https://x402.org/facilitator` |
| Support discovery | `GET https://x402.org/facilitator/supported` |

x402 lists Solana Devnet as supported by its public testing facilitator.
[Network documentation](https://docs.x402.org/core-concepts/network-and-token-support).
The mint above is Circle's official devnet USDC mint.
[Circle contract addresses](https://developers.circle.com/stablecoins/usdc-contract-addresses).

## Create the wallet

From `vibes/markets`, activate `.venv` and install the updated dependencies:

```bash
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
governor-wallet init
```

This creates `.governor/wallets/solana-devnet.json` with owner-only permissions
(`600`). It is a standard 64-byte Solana CLI JSON keypair generated with `solders`.
The command prints only public information and the file path. Running it again
reuses the wallet; it does not replace an existing key. Keep the key file locally;
Git ignores the entire `.governor/` directory.

Optional `.env` overrides:

```dotenv
SOLANA_WALLET_PATH=.governor/wallets/solana-devnet.json
SOLANA_RPC_URL=https://api.devnet.solana.com
```

A custom RPC is allowed, but the command verifies its genesis hash before reading
balances or requesting funds and refuses other clusters.

## Fund USDC

1. Copy the public `address` printed by `governor-wallet init`.
2. Open [Circle's public faucet](https://faucet.circle.com/).
3. Select **USDC**, then **Solana Devnet**.
4. Paste the wallet address and submit the claim.
5. Run `governor-wallet status` to verify the actual token balance.

Circle currently offers 20 test USDC per address/network every two hours. The
public faucet uses a browser form; the CLI does not claim to have submitted a
USDC request. It also does not mint a substitute token with a misleading USDC label.

## Fund SOL

```bash
governor-wallet airdrop
```

This requests 1 devnet SOL through the RPC's `requestAirdrop` method. To request
0.1 SOL instead, use integer lamports:

```bash
governor-wallet airdrop --lamports 100000000
```

The CLI submits once, then checks the returned signature. It reports `submitted`
separately from `confirmed`/`finalized` and never retries the funding request
automatically. [Solana requestAirdrop reference](https://solana.com/docs/rpc/http/requestairdrop).

Public faucets can be rate-limited or dry. During setup, this RPC returned error
429 for both requested amounts. If that happens, use the same public address at
[Solana's web faucet](https://faucet.solana.com/) and select Devnet, then check status.
Do not treat a rejected faucet request as funded.

SOL is useful for development transactions and account setup. In x402's sponsored
Solana flow, the facilitator supplies the fee-payer signature and pays transaction
fees; USDC is the buyer's payment asset. A buyer's SOL balance is not the USDC
spending budget. [Exact SVM specification](https://github.com/x402-foundation/x402/blob/main/specs/schemes/exact/scheme_exact_svm.md).

## Check readiness

```bash
governor-wallet status
```

This verifies Devnet, reads SOL, sums the official USDC mint's token accounts using
integer atomic amounts, and checks current facilitator support. It discovers the
fee payer dynamically rather than pinning an address that can change. Output
includes a Solana Explorer link with `cluster=devnet`.

`status` succeeding means the checks ran successfully; inspect the balances to
determine whether the wallet is funded. The public wallet address may be shared
for faucet funding. Private key bytes are never part of this output.

## Real agent payments

Wallet setup alone does not change the default mock mode. Start `governor-vendor`
and choose the explicit Devnet purchase mode on the dashboard's Services page.
The [local vendor guide](../src/governor/vendor/README.md) covers setup, the separate
seller page, CLI configuration, and receipt recovery.

`DevnetPaymentAdapter` signs only exact USDC transfers to the pinned local vendor,
after Governor reserves the payment within both caps. The facilitator sponsors
fees; both sides independently verify confirmed transaction evidence. Private
keys and signed payloads are never exposed to Gemini or the browser. Ambiguous
signed holds stay reserved until confirmed settlement; automatic failed-payment
hold release is not implemented. External Bazaar purchases remain disconnected.
