"""Typed decisions through one async interface: DecisionEngine.decide(request)."""

from decision_router.domain import (
    Boolean,
    Candidate,
    Choice,
    DecisionRequest,
    DecisionResult,
    Score,
)
from decision_router.engine import DecisionEngine
from decision_router.providers.base import DecisionProvider

__version__ = "0.1.0"

__all__ = [
    "Boolean",
    "Candidate",
    "Choice",
    "DecisionEngine",
    "DecisionProvider",
    "DecisionRequest",
    "DecisionResult",
    "Score",
]
