import threading

import pytest

from decision_router.domain import ErrorCode, ProviderError
from decision_router.providers.local import LocalNliProvider


async def test_timeout_has_no_unbounded_worker_queue(request_data):
    release = threading.Event()

    class Slow:
        def score(self, premise, hypotheses):
            release.wait(timeout=2)
            return [(1, 0)] * len(hypotheses)

    provider = LocalNliProvider(Slow(), model="test", revision="test", timeout_s=0.01)
    try:
        with pytest.raises(ProviderError) as exc:
            await provider.decide(request_data)
        assert exc.value.code == ErrorCode.TIMEOUT
        with pytest.raises(ProviderError) as exc:
            await provider.decide(request_data)
        assert exc.value.code == ErrorCode.UNAVAILABLE
    finally:
        release.set()
        provider.close()


async def test_unsupported_candidate_count(request_data):
    class Never:
        def score(self, premise, hypotheses):
            pytest.fail("Must reject before inference")

    provider = LocalNliProvider(Never(), model="test", revision="test", max_pairs=2)
    try:
        with pytest.raises(ProviderError, match="unsupported_input"):
            await provider.decide(request_data)
    finally:
        provider.close()


@pytest.mark.parametrize("bad", [[], [(float("nan"), 0)] * 6])
async def test_invalid_logits_rejected(bad, request_data):
    class Invalid:
        def score(self, premise, hypotheses):
            return bad

    provider = LocalNliProvider(Invalid(), model="test", revision="test")
    try:
        with pytest.raises(ProviderError, match="invalid_response"):
            await provider.decide(request_data)
    finally:
        provider.close()
