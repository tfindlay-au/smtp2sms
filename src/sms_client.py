"""RUT956 SMS client — modern RutOS JSON API (verified on RutOS 7.23.7).

Login yields a bearer token (~5 min TTL); we cache it and re-login once on 401.
No delivery retries — fire-and-forget per the SPDD.
"""
import asyncio

import httpx


class SmsSendError(Exception):
    pass


class RutSmsClient:
    def __init__(self, host: str, username: str, password: str,
                 modem: str, timeout: float = 10.0):
        self._base = f"http://{host}"
        self._username = username
        self._password = password
        self._modem = modem
        self._timeout = timeout
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _login(self) -> str:
        resp = await self._client.post(
            f"{self._base}/api/login",
            json={"username": self._username, "password": self._password},
        )
        data = resp.json()
        if not data.get("success"):
            raise SmsSendError(f"RUT login failed: {data.get('errors')}")
        return data["data"]["token"]

    async def _post_send(self, token: str, number: str, message: str) -> httpx.Response:
        return await self._client.post(
            f"{self._base}/api/messages/actions/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"data": {"number": number, "message": message,
                           "modem": self._modem}},
        )

    async def send(self, number: str, message: str) -> None:
        """Send one SMS inside the overall time budget. Raises SmsSendError."""
        try:
            async with asyncio.timeout(self._timeout):
                async with self._lock:
                    if self._token is None:
                        self._token = await self._login()
                    resp = await self._post_send(self._token, number, message)
                    if resp.status_code == 401:
                        self._token = await self._login()
                        resp = await self._post_send(self._token, number, message)
                data = resp.json()
                if not data.get("success"):
                    raise SmsSendError(f"RUT send failed: {data.get('errors')}")
        except SmsSendError:
            raise
        except (httpx.HTTPError, asyncio.TimeoutError, KeyError, ValueError) as e:
            raise SmsSendError(f"RUT API error: {e!r}") from e
