"""Isolated local inference. A timed-out model process can be terminated."""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from multiprocessing.connection import Connection
from typing import Literal, Protocol, cast

from decision_router.domain import (
    DecisionRequest,
    ErrorCode,
    ProviderError,
    ProviderResult,
    validate_result,
)

_MAX_MESSAGE = 1_048_576


class _ConnectionLike(Protocol):
    def send_bytes(self, buf: bytes) -> None: ...

    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class _SyncProvider(Protocol):
    def _decide_sync(self, request: DecisionRequest) -> ProviderResult: ...

    def close(self) -> None: ...


def _serve(
    connection: Connection,
    backend: Literal["nli", "openjev", "fake", "stall"],
    model: str,
    revision: str,
    local_files_only: bool,
    temperature: float,
    temperature_by_question: dict[str, float],
    max_pairs: int,
) -> None:
    provider: _SyncProvider | None = None
    try:
        if backend == "nli":
            from decision_router.providers.local import LocalNliProvider
            from decision_router.providers.transformers_scorer import TransformersScorer

            scorer = TransformersScorer(model, revision, local_files_only=local_files_only)
            provider = LocalNliProvider(
                scorer,
                model=model,
                revision=revision,
                temperature=temperature,
                temperature_by_question=temperature_by_question,
                max_pairs=max_pairs,
            )
        elif backend == "openjev":
            from decision_router.providers.openjev import OpenJevProvider

            provider = OpenJevProvider(
                model=model,
                revision=revision,
                local_files_only=local_files_only,
                temperature=temperature,
                max_pairs=max_pairs,
            )
            provider.load()
        connection.send_bytes(b"READY")
        while True:
            try:
                raw = connection.recv_bytes(_MAX_MESSAGE)
            except EOFError:
                return
            try:
                request = DecisionRequest.model_validate_json(raw)
                if backend == "stall":
                    time.sleep(3600)
                    return
                if backend == "fake":
                    from decision_router.providers.fake import FakeProvider

                    result = asyncio.run(FakeProvider().decide(request))
                else:
                    assert provider is not None
                    result = provider._decide_sync(request)
                connection.send_bytes(result.model_dump_json().encode("utf-8"))
            except ProviderError as exc:
                connection.send_bytes(b"ERROR:" + exc.code.value.encode("ascii"))
            except Exception:
                # Never serialize exception text, tracebacks, user input or credentials.
                connection.send_bytes(b"ERROR:unavailable")
    except Exception:
        try:
            connection.send_bytes(b"ERROR:unavailable")
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if provider is not None:
            provider.close()
        connection.close()


class ProcessNliProvider:
    def __init__(
        self,
        *,
        model: str,
        revision: str,
        local_files_only: bool,
        temperature: float,
        temperature_by_question: dict[str, float] | None = None,
        max_pairs: int,
        timeout_s: float,
        startup_timeout_s: float = 120,
        _backend: Literal["nli", "openjev", "fake", "stall"] = "nli",
    ) -> None:
        self._model = model
        self._revision = revision
        self._local_files_only = local_files_only
        self._temperature = temperature
        self._temperature_by_question = temperature_by_question or {}
        self._max_pairs = max_pairs
        self._timeout_s = timeout_s
        self._startup_timeout_s = startup_timeout_s
        self._backend = _backend
        self._process: multiprocessing.Process | None = None
        self._connection: _ConnectionLike | None = None
        self._busy = False

    async def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("Worker already started")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_serve,
            args=(
                child,
                self._backend,
                self._model,
                self._revision,
                self._local_files_only,
                self._temperature,
                self._temperature_by_question,
                self._max_pairs,
            ),
            daemon=True,
        )
        self._connection = parent
        # multiprocessing stubs expose platform-specific concrete subclasses.
        self._process = cast(multiprocessing.Process, process)
        try:
            process.start()
            child.close()
            ready = await asyncio.wait_for(
                asyncio.to_thread(parent.recv_bytes, 64), timeout=self._startup_timeout_s
            )
            if ready != b"READY":
                raise ProviderError(ErrorCode.UNAVAILABLE)
        except BaseException:
            child.close()
            self.close()
            raise

    def close(self) -> None:
        process, connection = self._process, self._connection
        self._process = None
        self._connection = None
        if process is not None:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1)
                    if process.is_alive():
                        process.kill()
                process.join(timeout=1)
        if connection is not None:
            connection.close()

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        process, connection = self._process, self._connection
        if process is None or connection is None or not process.is_alive():
            raise ProviderError(ErrorCode.UNAVAILABLE)
        if self._busy:
            raise ProviderError(ErrorCode.UNAVAILABLE)
        payload = request.model_dump_json().encode("utf-8")
        if len(payload) > _MAX_MESSAGE:
            raise ProviderError(ErrorCode.UNSUPPORTED)
        self._busy = True
        try:
            async with asyncio.timeout(self._timeout_s):
                await asyncio.to_thread(connection.send_bytes, payload)
                raw = await asyncio.to_thread(connection.recv_bytes, _MAX_MESSAGE)
            if raw.startswith(b"ERROR:"):
                try:
                    code = ErrorCode(raw[6:].decode("ascii"))
                except (UnicodeError, ValueError):
                    code = ErrorCode.INVALID_RESPONSE
                raise ProviderError(code)
            try:
                result = ProviderResult.model_validate_json(raw)
                return validate_result(request, result)
            except (ValueError, TypeError):
                raise ProviderError(ErrorCode.INVALID_RESPONSE) from None
        except TimeoutError:
            self.close()
            raise ProviderError(ErrorCode.TIMEOUT) from None
        except asyncio.CancelledError:
            self.close()
            raise
        except (BrokenPipeError, EOFError, OSError):
            self.close()
            raise ProviderError(ErrorCode.UNAVAILABLE) from None
        finally:
            self._busy = False
