"""One decision interface hides timeouts, validation, fallback and shadow comparison."""

import asyncio
from time import perf_counter
from typing import Literal

from decision_router.audit import AuditSink
from decision_router.config import Settings
from decision_router.domain import (
    Attempt,
    DecisionRequest,
    DecisionResult,
    ErrorCode,
    ProviderError,
    ProviderResult,
    ShadowComparison,
    validate_result,
)
from decision_router.providers.base import DecisionProvider


class DecisionEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        local: DecisionProvider | None,
        cloud: DecisionProvider | None,
        audit: AuditSink,
    ) -> None:
        self._settings = settings
        self._providers = {"local": local, "cloud": cloud}
        self._audit = audit

    async def _attempt(self, slot: Literal["local", "cloud"], request: DecisionRequest) -> Attempt:
        start = perf_counter()
        result: ProviderResult | None = None
        error: ErrorCode | None = None
        try:
            provider = self._providers[slot]
            if provider is None:
                raise ProviderError(ErrorCode.UNAVAILABLE)
            async with asyncio.timeout(self._settings.timeout_s):
                raw = await provider.decide(request)
                # Revalidate even an injected provider that bypassed Pydantic construction.
                result = validate_result(request, ProviderResult.model_validate(raw.model_dump()))
        except TimeoutError:
            error = ErrorCode.TIMEOUT
        except ProviderError as exc:
            error = exc.code
        except (ValueError, TypeError, KeyError):
            error = ErrorCode.INVALID_RESPONSE
        except Exception:
            # Fail closed; never include arbitrary provider exception text in logs or responses.
            error = ErrorCode.UNAVAILABLE
        return Attempt(
            slot=slot, result=result, error=error, latency_ms=(perf_counter() - start) * 1000
        )

    @staticmethod
    def _confidence(result: ProviderResult) -> float:
        return min(answer.confidence for answer in result.answers.values())

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        start = perf_counter()
        settings = self._settings
        fallback: str | None = None
        shadow: ShadowComparison | None = None
        if settings.mode == "shadow":
            secondary: Literal["local", "cloud"] = (
                "cloud" if settings.shadow_primary == "local" else "local"
            )
            primary, comparison = await asyncio.gather(
                self._attempt(settings.shadow_primary, request), self._attempt(secondary, request)
            )
            attempts = [primary, comparison]
            shadow = ShadowComparison(
                primary=settings.shadow_primary,
                agreement={
                    q.id: primary.result.answers[q.id].selected
                    == comparison.result.answers[q.id].selected
                    for q in request.questions
                }
                if primary.result and comparison.result
                else None,
            )
            chosen = primary.result
            if primary.error:
                fallback = primary.error.value
        else:
            slot: Literal["local", "cloud"] = "cloud" if settings.mode == "cloud" else "local"
            first = await self._attempt(slot, request)
            attempts = [first]
            chosen = first.result
            if settings.mode == "auto" and (
                chosen is None or self._confidence(chosen) < settings.local_accept_confidence
            ):
                fallback = first.error.value if first.error else "low_confidence"
                # Very uncertain classifications abstain without a cloud call.
                if chosen is None or self._confidence(chosen) >= settings.review_below_confidence:
                    second = await self._attempt("cloud", request)
                    attempts.append(second)
                    chosen = second.result
                    if second.error:
                        fallback += ":cloud_" + second.error.value
            elif first.error:
                fallback = first.error.value
        status: Literal["decided", "human_review"] = "decided"
        if chosen is None or self._confidence(chosen) < settings.review_below_confidence:
            status = "human_review"
            fallback = fallback or "very_low_confidence"
        # Local-only/shadow-local must not silently accept the middle confidence band.
        elif (
            (settings.mode == "local")
            or (settings.mode == "shadow" and settings.shadow_primary == "local")
        ) and (self._confidence(chosen) < settings.local_accept_confidence):
            status = "human_review"
            fallback = "low_confidence"
        result = DecisionResult(
            status=status,
            decision=chosen,
            attempts=attempts,
            fallback_reason=fallback,
            shadow=shadow,
            latency_ms=(perf_counter() - start) * 1000,
        )
        self._audit.record(result)
        return result
