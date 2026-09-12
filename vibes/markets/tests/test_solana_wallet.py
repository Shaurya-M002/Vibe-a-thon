import json
import stat

import httpx
import pytest
from solders.keypair import Keypair

from governor.solana_wallet import (
    DEVNET_GENESIS,
    DEVNET_NETWORK,
    DEVNET_USDC,
    DevnetRPC,
    WalletError,
    create_wallet,
    facilitator_support,
    load_wallet,
)


def test_wallet_creation_is_private_and_never_overwrites(tmp_path):
    path = tmp_path / "wallet.json"
    address, created = create_wallet(path)
    assert created
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert str(load_wallet(path).pubkey()) == address
    original = path.read_bytes()
    assert create_wallet(path) == (address, False)
    assert path.read_bytes() == original


def test_wallet_rejects_public_file_and_corrupt_key(tmp_path):
    path = tmp_path / "wallet.json"
    create_wallet(path)
    path.chmod(0o644)
    with pytest.raises(WalletError, match="private"):
        load_wallet(path)
    path.chmod(0o600)
    path.write_text("[true]")
    with pytest.raises(WalletError, match="64-byte"):
        create_wallet(path)
    assert path.read_text() == "[true]"


def test_wrong_network_refused_before_airdrop():
    methods = []

    def handler(request):
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(200, json={"result": "mainnet-genesis"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WalletError, match="not Solana Devnet"):
            DevnetRPC(client).airdrop(str(Keypair().pubkey()), 1_000_000_000)
    assert methods == ["getGenesisHash"]


def test_balances_use_integer_amounts_and_verified_mint():
    address = str(Keypair().pubkey())

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getGenesisHash":
            result = DEVNET_GENESIS
        elif body["method"] == "getBalance":
            result = {"value": 100000001}
        else:
            assert body["params"][1] == {"mint": DEVNET_USDC}
            result = {
                "value": [
                    {
                        "account": {
                            "data": {
                                "parsed": {
                                    "info": {
                                        "mint": DEVNET_USDC,
                                        "owner": address,
                                        "tokenAmount": {"amount": "20000001", "decimals": 6},
                                    }
                                }
                            }
                        }
                    }
                ]
            }
        return httpx.Response(200, json={"result": result})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = DevnetRPC(client).balances(address)
    assert result["sol"] == "0.100000001"
    assert result["usdc"] == "20.000001"
    assert result["usdc_atomic"] == "20000001"


def test_rate_limit_does_not_retry_airdrop():
    methods = []

    def handler(request):
        method = json.loads(request.content)["method"]
        methods.append(method)
        if method == "getGenesisHash":
            return httpx.Response(200, json={"result": DEVNET_GENESIS})
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WalletError, match="rate limited"):
            DevnetRPC(client).airdrop(str(Keypair().pubkey()), 100000000)
    assert methods.count("requestAirdrop") == 1


def test_confirmation_failure_preserves_submitted_signature():
    def handler(request):
        method = json.loads(request.content)["method"]
        if method == "getGenesisHash":
            return httpx.Response(200, json={"result": DEVNET_GENESIS})
        if method == "requestAirdrop":
            return httpx.Response(200, json={"result": "submitted-signature"})
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = DevnetRPC(client).airdrop(str(Keypair().pubkey()), 100000000)
    assert result["status"] == "submitted"
    assert result["signature"] == "submitted-signature"


def test_facilitator_requires_exact_devnet_and_v2():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "kinds": [
                    {
                        "x402Version": 2,
                        "scheme": "exact",
                        "network": DEVNET_NETWORK,
                        "extra": {"feePayer": "public-fee-payer"},
                    },
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert facilitator_support(client)["solana_devnet_exact"] is True
