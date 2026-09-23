"""Opt-in offline tests against real pinned model weights, not a mocked scorer."""

import os

import pytest

from decision_router.config import Settings
from decision_router.domain import DecisionRequest, ProviderError, validate_result
from decision_router.providers.local import LocalNliProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LOCAL_MODEL_TESTS") != "1", reason="Opt-in real-model offline integration"
)


@pytest.fixture(scope="module")
def model_provider():
    from decision_router.providers.transformers_scorer import TransformersScorer

    settings = Settings()
    scorer = TransformersScorer(
        settings.local_model, settings.local_revision, local_files_only=True
    )
    provider = LocalNliProvider(
        scorer, model=settings.local_model, revision=settings.local_revision, timeout_s=30
    )
    yield provider
    provider.close()


async def test_real_model_contract(model_provider, request_data):
    result = await model_provider.decide(request_data)
    validate_result(request_data, result)
    assert result.provider == "local_nli"
    assert result.answers["route"].selected == "coder"


async def test_real_model_rejects_long_input_without_truncation(model_provider, request_data):
    request = DecisionRequest(prompt="word " * 3000, questions=request_data.questions)
    with pytest.raises(ProviderError, match="unsupported_input"):
        await model_provider.decide(request)
