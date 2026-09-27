import secrets
from datetime import UTC, datetime

import httpx

_states: dict[str, float] = {}


async def discovery(issuer: str) -> dict[str, object]:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(url)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Discovery document was not an object.")
    return body


async def exchange_code(token_url: str, data: dict[str, str]) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        response = await client.post(token_url, data=data)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Token response was not an object.")
    return body


def new_state() -> str:
    state = secrets.token_urlsafe(18)
    _states[state] = datetime.now(UTC).timestamp() + 600
    return state


def take_state(state: str) -> bool:
    expires = _states.pop(state, None)
    return expires is not None and expires >= datetime.now(UTC).timestamp()
