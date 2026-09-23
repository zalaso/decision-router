import asyncio
from time import perf_counter

import pytest

from decision_router.domain import ErrorCode, ProviderError, validate_result
from decision_router.providers.process import ProcessNliProvider


def provider(backend="fake", timeout=2):
    return ProcessNliProvider(
        model="fixture",
        revision="fixture",
        local_files_only=True,
        temperature=1,
        max_pairs=64,
        timeout_s=timeout,
        startup_timeout_s=10,
        _backend=backend,
    )


async def test_process_roundtrip_and_close(request_data):
    worker = provider()
    await worker.start()
    try:
        result = await worker.decide(request_data)
        validate_result(request_data, result)
        assert result.answers["route"].selected == "coder"
        assert worker._process is not None and worker._process.is_alive()
    finally:
        worker.close()
    assert worker._process is None


async def test_timeout_kills_worker_instead_of_leaving_kernel_running(request_data):
    worker = provider("stall", timeout=0.05)
    await worker.start()
    started = perf_counter()
    with pytest.raises(ProviderError) as exc:
        await worker.decide(request_data)
    assert exc.value.code == ErrorCode.TIMEOUT
    assert perf_counter() - started < 3
    assert worker._process is None
    with pytest.raises(ProviderError, match="unavailable"):
        await worker.decide(request_data)


async def test_cancellation_kills_worker(request_data):
    worker = provider("stall", timeout=10)
    await worker.start()
    task = asyncio.create_task(worker.decide(request_data))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker._process is None
