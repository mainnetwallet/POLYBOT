"""CLOB REST client with L2 HMAC-SHA256 authentication.

Provides a direct, SDK-independent path to the CLOB API for:
- L2 credential creation/derivation (via L1 wallet signature)
- Balance / allowance cache refresh
- Order placement and cancellation (verification path)

This keeps SDK-specific code isolated: the adapter may use the SDK or this
REST client interchangeably. L2 signing follows the official docs:

    message = timestamp + METHOD + path + body
    signature = urlsafeBase64(HMAC-SHA256(base64decode(secret), message))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Optional

import httpx

from polybot.constants import CLOB_API_URL


class CLOBRestClient:
    """Minimal authenticated CLOB REST client."""

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,          # base64-encoded
        passphrase: str,
        address: str,         # signer address
        base_url: str = CLOB_API_URL,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.address = address
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    # -- signing --------------------------------------------------------

    def _secret_bytes(self) -> bytes:
        return base64.b64decode(self.secret)

    def l2_signature(self, method: str, path: str, body: Optional[str] = None) -> tuple[str, int]:
        """Return (signature, timestamp) for a CLOB request."""
        timestamp = int(time.time())
        message = f"{timestamp}{method.upper()}{path}"
        if body:
            message += body
        digest = hmac.new(
            self._secret_bytes(), message.encode("utf-8"), hashlib.sha256
        ).digest()
        sig = base64.urlsafe_b64encode(digest).decode("ascii")
        return sig, timestamp

    def _headers(self, method: str, path: str, body: Optional[str] = None) -> dict:
        sig, ts = self.l2_signature(method, path, body)
        return {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": str(ts),
            "POLY_API_KEY": self.api_key,
            "POLY_PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    # -- requests -------------------------------------------------------

    async def _request(
        self, method: str, path: str, payload: Optional[dict] = None
    ) -> httpx.Response:
        body = json.dumps(payload, separators=(",", ":")) if payload is not None else None
        headers = self._headers(method, path, body)
        url = f"{self.base_url}{path}"
        if method.upper() == "GET":
            return await self._client.get(url, headers=headers, params=payload)
        return await self._client.request(method, url, headers=headers, content=body)

    # -- endpoints ------------------------------------------------------

    async def balance_allowance_update(
        self,
        *,
        asset_type: str,
        signature_type: int,
        token_id: Optional[str] = None,
    ) -> httpx.Response:
        """Refresh the CLOB allowance cache for collateral or an outcome token."""
        params: dict = {"asset_type": asset_type, "signature_type": str(signature_type)}
        if token_id is not None:
            params["token_id"] = token_id
        return await self._request("GET", "/balance-allowance/update", params)

    async def get_open_orders(self) -> httpx.Response:
        return await self._request("GET", "/data/orders", None)

    async def get_trades(self, market_id: Optional[str] = None) -> httpx.Response:
        params: dict = {}
        if market_id:
            params["market"] = market_id
        return await self._request("GET", "/data/trades", params)

    async def get_balance(self) -> httpx.Response:
        return await self._request("GET", "/balance", None)

    async def place_order_rest(self, order: dict, order_type: str, owner: str) -> httpx.Response:
        body = {
            "deferExec": False,
            "order": order,
            "orderType": order_type,
            "owner": owner,
        }
        return await self._request("POST", "/order", body)

    async def cancel_order_rest(self, order_id: str, owner: str) -> httpx.Response:
        body = {"order_id": order_id, "orderType": "GTC", "owner": owner}
        return await self._request("DELETE", "/order", body)

    async def health(self) -> bool:
        try:
            resp = await self._request("GET", "/markets", {"closed": "false", "limit": "1"})
            return resp.status_code < 500
        except Exception:
            return False


class CLOBAuth:
    """L1 signature + L2 credential lifecycle."""

    def __init__(self, rest: CLOBRestClient, signer_private_key: str) -> None:
        self.rest = rest
        self.signer_private_key = signer_private_key

    async def create_or_derive_credentials(
        self, address: str, nonce: int = 0
    ) -> dict:
        """Sign ClobAuth typed data and create/derive L2 API credentials.

        Requires an Ethereum wallet signer (eth_account). Returns {apiKey, secret, passphrase}.
        """
        from eth_account import Account
        from eth_account.messages import encode_typed_data

        acct = Account.from_key(self.signer_private_key)
        if acct.address.lower() != address.lower():
            raise ValueError("signer private key does not match address")

        timestamp = int(time.time())
        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                "ClobAuth": [
                    {"name": "address", "type": "address"},
                    {"name": "timestamp", "type": "string"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "message", "type": "string"},
                ],
            },
            "primaryType": "ClobAuth",
            "domain": {"name": "ClobAuthDomain", "version": "1", "chainId": 137},
            "message": {
                "address": address,
                "timestamp": str(timestamp),
                "nonce": str(nonce),
                "message": "This message attests that I control the given wallet",
            },
        }

        signed = acct.sign_typed_data(typed_data)
        signature = signed.signature

        # Try derive first, then create.
        for endpoint in ("/auth/derive-api-key", "/auth/api-key"):
            resp = await self._client_post_auth(
                endpoint,
                {
                    "POLY_ADDRESS": address,
                    "POLY_SIGNATURE": signature,
                    "POLY_TIMESTAMP": str(timestamp),
                    "POLY_NONCE": str(nonce),
                },
            )
            if resp.status_code in (200, 201):
                return resp.json()
        raise RuntimeError("Failed to create or derive CLOB API credentials")

    async def _client_post_auth(self, path: str, headers: dict) -> httpx.Response:
        return await self.rest._client.post(
            f"{self.rest.base_url}{path}", headers=headers
        )
