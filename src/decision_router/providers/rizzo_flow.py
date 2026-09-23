"""Loopback HTTP adapter for Rizzo Flow's Jev-compatible endpoint."""

import httpx
from pydantic import SecretStr

from decision_router.config import validate_rizzo_origin
from decision_router.domain import Choice, DecisionRequest, ErrorCode, ProviderError, ProviderResult
from decision_router.providers.jev import _SystemOneHttpProvider


class RizzoFlowProvider(_SystemOneHttpProvider):
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str = "rizzo-latest",
        timeout_s: float = 5.0,
    ) -> None:
        origin = validate_rizzo_origin(base_url)
        super().__init__(
            client,
            api_key,
            model,
            timeout_s,
            endpoint=f"{origin}/v1/systemone",
            provider_name="rizzo_flow",
            require_api_key=False,
            provider_warnings=[
                "rizzo_flow_confidence_not_calibrated_for_routing",
                "rizzo_flow_jev_compatible_interface_not_jev_weights",
            ],
        )

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        if any(
            isinstance(question, Choice) and len(question.candidates) > 26
            for question in request.questions
        ):
            raise ProviderError(ErrorCode.UNSUPPORTED)
        return await super().decide(request)
