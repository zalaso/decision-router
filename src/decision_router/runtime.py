"""Composition root: the only place that constructs external dependencies."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from decision_router.audit import JsonAudit
from decision_router.config import Settings
from decision_router.domain import DecisionRequest, ErrorCode, ProviderError, ProviderResult
from decision_router.engine import DecisionEngine
from decision_router.providers.base import DecisionProvider
from decision_router.providers.fake import FakeProvider
from decision_router.providers.jev import JevCloudProvider
from decision_router.providers.ollama import OllamaProvider
from decision_router.providers.process import ProcessNliProvider
from decision_router.providers.rizzo_flow import RizzoFlowProvider


class _UnavailableLocal:
    async def decide(self, request: DecisionRequest) -> ProviderResult:
        raise ProviderError(ErrorCode.UNAVAILABLE)


@asynccontextmanager
async def runtime(settings: Settings) -> AsyncIterator[DecisionEngine]:
    local: DecisionProvider | None = None
    nli: ProcessNliProvider | None = None
    if settings.mode != "cloud":
        if settings.local_backend == "fake":
            local = FakeProvider()
        elif settings.local_backend == "nli" or settings.local_backend == "openjev":
            try:
                nli = ProcessNliProvider(
                    model=settings.local_model,
                    revision=settings.local_revision,
                    local_files_only=settings.local_files_only,
                    timeout_s=settings.timeout_s,
                    startup_timeout_s=settings.local_startup_timeout_s,
                    temperature=settings.temperature,
                    temperature_by_question=settings.temperature_by_question,
                    max_pairs=settings.local_max_pairs,
                    _backend=settings.local_backend,
                )
                await nli.start()
                local = nli
            except Exception:
                # Missing weights/dependencies remain observable as unavailable, never fake.
                # Auto can still reach cloud; local/shadow fail closed.
                if nli is not None:
                    nli.close()
                    nli = None
                local = _UnavailableLocal()
    warm_up: asyncio.Task[None] | None = None
    try:
        async with httpx.AsyncClient(timeout=settings.timeout_s, trust_env=False) as client:
            if settings.mode != "cloud" and settings.local_backend == "ollama":
                ollama = OllamaProvider(
                    client, base_url=settings.ollama_base_url, model=settings.ollama_model
                )
                # Loading a model from disk can take a minute on CPU: start it now.
                warm_up = asyncio.create_task(ollama.warm_up())
                local = ollama
            if settings.mode != "cloud" and settings.local_backend == "rizzo_flow":
                local = RizzoFlowProvider(
                    client,
                    base_url=settings.rizzo_base_url,
                    api_key=settings.rizzo_api_key,
                    model=settings.rizzo_model,
                    timeout_s=settings.timeout_s,
                )
            cloud = (
                JevCloudProvider(
                    client, settings.typesafe_api_key, settings.cloud_model, settings.timeout_s
                )
                if (settings.mode != "local" and settings.typesafe_api_key.get_secret_value())
                else None
            )
            yield DecisionEngine(
                settings=settings,
                local=local,
                cloud=cloud,
                audit=JsonAudit(logging.getLogger("decision_router.audit")),
            )
    finally:
        if warm_up is not None:
            warm_up.cancel()
        if nli:
            nli.close()
