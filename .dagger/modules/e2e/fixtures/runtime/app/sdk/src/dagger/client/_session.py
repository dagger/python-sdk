import logging
import os
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx
from opentelemetry import propagate
from typing_extensions import Self

from dagger import ClientConnectionError, TransportError, telemetry
from dagger._exceptions import _query_error_from_response
from dagger._managers import ResourceManager
from dagger.client._config import ConnectConfig

logger = logging.getLogger(__name__)

# Safe to retry: every API call is cached on its inputs.
MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 2.0


@dataclass(slots=True, kw_only=True)
class ConnectParams:
    """Options for making a session connection. For internal use only."""

    port: int
    session_token: str
    url: httpx.URL = field(init=False)

    def __post_init__(self):
        self.port = int(self.port)
        if self.port < 1:
            msg = f"Invalid port value: {self.port}"
            raise ValueError(msg)
        self.url = httpx.URL(f"http://127.0.0.1:{self.port}/query")

    @classmethod
    def from_env(cls) -> "ConnectParams | None":
        if not (port := os.getenv("DAGGER_SESSION_PORT")):
            return None
        if not (token := os.getenv("DAGGER_SESSION_TOKEN")):
            msg = "DAGGER_SESSION_TOKEN must be set when using DAGGER_SESSION_PORT"
            raise ClientConnectionError(msg)
        try:
            return cls(port=int(port), session_token=token)
        except ValueError as e:
            # only port is validated
            msg = f"Invalid DAGGER_SESSION_PORT: {port}"
            raise ClientConnectionError(msg) from e


class TelemetryTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        propagate.inject(request.headers)
        return await super().handle_async_request(request)


class ClientSession(ResourceManager):
    """HTTP session to the engine's GraphQL API.

    Queries go as text: the generated client knows its schema, so nothing is
    fetched or validated on connect.
    """

    def __init__(self, conn: ConnectParams, cfg: ConnectConfig | None = None):
        super().__init__()

        if cfg is None:
            cfg = ConnectConfig()

        self.conn = conn
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None

    def _make_client(self) -> httpx.AsyncClient:
        retry = self.cfg.retry
        return httpx.AsyncClient(
            auth=(self.conn.session_token, ""),
            timeout=self.cfg.timeout,
            transport=TelemetryTransport(
                retries=MAX_ATTEMPTS if retry and retry.connect else 0,
                # Plain HTTP on loopback: the default TLS context costs ~70ms
                # per process start and is never used.
                verify=False,
            ),
        )

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def start(self) -> httpx.AsyncClient:
        if self._client:
            return self._client

        async with self.get_stack() as stack:
            logger.debug("Establishing client session to GraphQL server")
            self._client = await stack.enter_async_context(self._make_client())
            return self._client

    def has_session(self):
        return self._client is not None

    async def execute(self, query: str) -> Any:
        """Send a query and return its data."""
        client = await self.start()
        retry = self.cfg.retry
        attempts = MAX_ATTEMPTS if retry and retry.execute else 1

        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(self.conn.url, json={"query": query})
            except httpx.TransportError as e:  # noqa: PERF203 — this is the retry loop
                if attempt == attempts:
                    raise TransportError(_transport_message(e)) from e
                delay = min(0.1 * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
                logger.debug("Request failed (%s), retrying in %.1fs", e, delay)
                await anyio.sleep(delay)
            except RuntimeError as e:
                # httpx raises this when the client has already been closed.
                msg = (
                    "Connection to engine has been closed. Make sure you're "
                    "calling the API within a `dagger.connection()` context."
                )
                raise TransportError(msg) from e
            else:
                return _read_response(response, query)

        msg = "Failed to execute request"
        raise TransportError(msg)

    async def close(self) -> None:
        logger.debug("Closing client session to GraphQL server")
        await super().close()
        self._client = None


def _transport_message(e: httpx.TransportError) -> str:
    if isinstance(e, httpx.TimeoutException):
        return (
            "Request timed out. Try setting a higher timeout value for this connection."
        )
    if msg := str(e):
        return f"Failed to execute request: {msg}"
    return "Failed to execute request"


def _read_response(response: httpx.Response, query: str) -> Any:
    try:
        body = response.json()
    except ValueError as e:
        msg = _unexpected(response)
        raise TransportError(msg) from e

    if not isinstance(body, dict):
        raise TransportError(_unexpected(response))

    if errors := body.get("errors"):
        if err := _query_error_from_response(errors, query):
            raise err
        msg = f"Unexpected error response from engine: {errors!r}"
        raise TransportError(msg)

    if response.status_code != httpx.codes.OK:
        raise TransportError(_unexpected(response))

    return body.get("data")


def _unexpected(response: httpx.Response) -> str:
    return (
        f"Unexpected response from engine: {response.status_code} {response.text[:200]}"
    )


class BaseConnection:
    session: ClientSession

    async def connect(self) -> Self:
        await self.session.start()
        return self

    async def close(self) -> None:
        await self.session.close()

    async def aclose(self) -> None:
        await self.close()

    def __await__(self):
        return self.connect().__await__()

    async def __aenter__(self) -> Self:
        telemetry.initialize()
        return await self.connect()

    async def __aexit__(self, *_) -> None:
        await self.close()


class SingleConnection(BaseConnection):
    """Establish a GraphQL client connection to the Dagger API server."""

    def __init__(self, conn: ConnectParams, cfg: ConnectConfig | None = None):
        self.session = ClientSession(conn, cfg)


class SharedConnection(BaseConnection):
    """Establish a GraphQL client connection to the Dagger API server.

    Uses a lazy and shared connection.
    """

    _instance: Self | None = None
    _session: ClientSession | None = None
    _params: ConnectParams | None = None
    _cfg: ConnectConfig

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._cfg = ConnectConfig()
        return cls._instance

    def __init__(self) -> None:
        # This is a singleton class, so we don't want to initialize.
        ...

    def with_params(self, params: ConnectParams) -> Self:
        """Set the connection params."""
        if self._session:
            logger.warning(
                "Cannot set connection params after connection already started"
            )
        else:
            self._params = params
        return self

    def with_config(self, cfg: ConnectConfig) -> Self:
        """Set the connection config."""
        if self._session:
            logger.warning(
                "Cannot set connection config after connection already started"
            )
        else:
            self._cfg = cfg
        return self

    @property
    def session(self) -> ClientSession:
        if not self._session:
            logger.debug("Configuring shared connection to GraphQL server")

            # Delay checking the environment until we actually need it.
            if not self._params:
                self._params = ConnectParams.from_env()

            if not self._params:
                msg = "No active engine session to connect to"
                raise ClientConnectionError(msg)

            self._session = ClientSession(self._params, self._cfg)
        return self._session

    def is_connected(self) -> bool:
        return self._session is not None and self._session.has_session()

    async def close(self) -> None:
        if self._session:
            await super().close()
            self._session = None
