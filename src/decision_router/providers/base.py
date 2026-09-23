from typing import Protocol

from decision_router.domain import DecisionRequest, ProviderResult


class DecisionProvider(Protocol):
    async def decide(self, request: DecisionRequest) -> ProviderResult:
        """Return a complete valid distribution, or raise a safe ProviderError."""
        ...
