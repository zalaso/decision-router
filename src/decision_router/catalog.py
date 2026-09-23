"""Model catalog for recommendations: packaged YAML, optional override, local Ollama detection."""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Literal, Self

import httpx
import yaml
from pydantic import Field, ValidationError, model_validator

from decision_router.config import Settings
from decision_router.domain import Identifier, Model

Task = Literal["code", "reasoning", "writing", "research", "chat", "vision"]
TASKS: tuple[Task, ...] = ("code", "reasoning", "writing", "research", "chat", "vision")


class Group(Model):
    id: Identifier
    label: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=80)


class ModelProfile(Model):
    id: Identifier
    name: str = Field(min_length=1, max_length=120)
    group: Identifier
    api_model: str = Field(min_length=1, max_length=200)
    local: bool = False
    price: tuple[float, float] | None = None  # USD per 1M tokens, input/output
    context_k: int | None = Field(default=None, ge=1, le=100000)
    size_gb: float | None = Field(default=None, ge=0)
    cost: int = Field(ge=0, le=5)
    speed: int = Field(ge=1, le=5)
    skills: dict[Task, int]
    notes: str = Field(default="", max_length=500)
    # True for installed Ollama models missing from the catalog: generic profile.
    estimated: bool = False
    installed: bool | None = None

    @model_validator(mode="after")
    def complete(self) -> Self:
        if set(self.skills) != set(TASKS) or any(not 0 <= v <= 10 for v in self.skills.values()):
            raise ValueError("skills must rate every task from 0 to 10")
        if self.price is not None and min(self.price) < 0:
            raise ValueError("prices must not be negative")
        return self


class Catalog(Model):
    updated: str = ""
    groups: list[Group] = Field(min_length=1, max_length=50)
    models: list[ModelProfile] = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len({m.id for m in self.models}) != len(self.models):
            raise ValueError("Model IDs must be unique")
        if not {m.group for m in self.models} <= {g.id for g in self.groups}:
            raise ValueError("Every model must belong to a declared group")
        return self


class OllamaStatus(Model):
    enabled: bool
    reachable: bool
    installed: list[str] = Field(default_factory=list)


class CatalogView(Model):
    """What the dashboard shows: catalog plus local installation state."""

    updated: str
    groups: list[Group]
    models: list[ModelProfile]
    ollama: OllamaStatus


def load_catalog(path: str | None) -> Catalog:
    source = (
        Path(path).read_text(encoding="utf-8")
        if path
        else (files("decision_router") / "catalog.yaml").read_text(encoding="utf-8")
    )
    data = yaml.safe_load(source)
    if not isinstance(data, dict):
        raise ValueError("Model catalog must be a YAML mapping")
    return Catalog.model_validate(data)


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower()[:56] or "model"


def estimated_profile(
    name: str, details: dict[str, object], capabilities: list[str]
) -> ModelProfile:
    """Conservative profile for an installed model the catalog does not describe."""
    size = str(details.get("parameter_size", "")).upper().rstrip("B")
    try:
        billions = float(size)
    except ValueError:
        billions = 7.0
    base = 2 if billions < 5 else 3 if billions < 15 else 5 if billions < 40 else 6
    context = details.get("context_length")
    return ModelProfile(
        id="ollama-" + _slug(name),
        name=name,
        group="local",
        api_model=name,
        local=True,
        context_k=max(1, int(context) // 1000) if isinstance(context, int) else None,
        cost=0,
        speed=5 if billions < 5 else 4 if billions < 15 else 2,
        skills={
            "code": base,
            "reasoning": base,
            "writing": base,
            "research": max(1, base - 2),
            "chat": min(10, base + 1),
            "vision": base if "vision" in capabilities else 0,
        },
        estimated=True,
        installed=True,
    )


class CatalogService:
    """Owns the catalog and the last Ollama scan; recommendations use the same merged view."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport
        self.catalog = load_catalog(settings.model_catalog)
        self._ollama = OllamaStatus(enabled=settings.ollama_detect, reachable=False)
        self._extra: list[ModelProfile] = []
        self._scanned = False

    async def refresh(self) -> None:
        self._scanned = True
        if not self._settings.ollama_detect:
            return
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=1.5, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.get(f"{self._settings.ollama_base_url}/api/tags")
            response.raise_for_status()
            entries = response.json().get("models", [])
            if not isinstance(entries, list):
                raise ValueError("unexpected Ollama response")
        except (httpx.HTTPError, ValueError, AttributeError):
            self._ollama = OllamaStatus(enabled=True, reachable=False)
            self._extra = []
            return
        known = {m.api_model for m in self.catalog.models if m.local}
        installed: list[str] = []
        extra: dict[str, ModelProfile] = {}
        for entry in entries[:200]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            name = entry["name"][:200]
            installed.append(name)
            if self._matches(name, known) is None:
                details = entry.get("details")
                capabilities = entry.get("capabilities")
                try:
                    profile = estimated_profile(
                        name,
                        details if isinstance(details, dict) else {},
                        [c for c in capabilities if isinstance(c, str)]
                        if isinstance(capabilities, list)
                        else [],
                    )
                except (ValidationError, ValueError, TypeError):
                    continue
                extra.setdefault(profile.id, profile)
        self._ollama = OllamaStatus(enabled=True, reachable=True, installed=installed)
        self._extra = list(extra.values())

    @staticmethod
    def _matches(name: str, known: set[str]) -> str | None:
        # Ollama reports "qwen3.8:27b"; a bare "qwen3.8" means the ":latest" tag.
        candidates = {name, name.removesuffix(":latest")}
        return next((k for k in known if k in candidates), None)

    async def view(self, *, rescan: bool = False) -> CatalogView:
        if rescan or not self._scanned:
            await self.refresh()
        installed = set(self._ollama.installed) | {
            n.removesuffix(":latest") for n in self._ollama.installed
        }
        models = [
            m.model_copy(update={"installed": m.api_model in installed})
            if m.local and self._ollama.reachable
            else m
            for m in self.catalog.models
        ]
        return CatalogView(
            updated=self.catalog.updated,
            groups=self.catalog.groups,
            models=models + self._extra,
            ollama=self._ollama,
        )
