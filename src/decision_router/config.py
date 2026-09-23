from __future__ import annotations

import json
import os
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import Field, SecretStr, field_validator, model_validator

from decision_router.domain import Model, Probability

PRIMARY_MODEL = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
PRIMARY_REVISION = "8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c"
Temperature = Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)]


def validate_rizzo_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        address = ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError:
        raise ValueError("Rizzo Flow URL must use a numeric loopback address") from None
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or port is None
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Rizzo Flow URL must be an HTTP loopback origin with a port")
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{host}:{port}"


class Settings(Model):
    mode: Literal["cloud", "local", "auto", "shadow"] = "local"
    local_backend: Literal["fake", "nli", "openjev", "rizzo_flow"] = "fake"
    shadow_primary: Literal["local", "cloud"] = "local"
    timeout_s: float = Field(default=5.0, gt=0, le=300)
    local_startup_timeout_s: float = Field(default=120.0, gt=0, le=600)
    local_accept_confidence: Probability = 0.80
    review_below_confidence: Probability = 0.55
    safety_min_confidence: Probability = 0.80
    risk_threshold: Probability = 0.30
    injection_threshold: Probability = 0.30
    malicious_threshold: Probability = 0.30
    cloud_model: str = "jev-latest"
    local_model: str = PRIMARY_MODEL
    local_revision: str = Field(default=PRIMARY_REVISION, pattern=r"^[a-f0-9]{40}$")
    local_files_only: bool = True
    local_max_pairs: int = Field(default=64, ge=2, le=256)
    rizzo_base_url: str = "http://127.0.0.1:8017"
    rizzo_model: str = Field(default="rizzo-latest", min_length=1, max_length=256)
    rizzo_api_key: SecretStr = SecretStr("")
    max_requests_per_minute: int = Field(default=60, ge=1, le=100000)
    max_concurrent_requests: int = Field(default=4, ge=1, le=1000)
    require_https: bool = False
    dashboard: bool = True
    temperature: Temperature = 1.0
    temperature_by_question: dict[str, Temperature] = Field(default_factory=dict)
    typesafe_api_key: SecretStr = SecretStr("")
    api_token: SecretStr = SecretStr("")

    @field_validator("rizzo_base_url")
    @classmethod
    def loopback_rizzo_url(cls, value: str) -> str:
        return validate_rizzo_origin(value)

    @model_validator(mode="after")
    def thresholds(self) -> Self:
        if self.review_below_confidence > self.local_accept_confidence:
            raise ValueError("review threshold must not exceed acceptance threshold")
        if self.require_https and not self.api_token.get_secret_value():
            raise ValueError("HTTPS exposure requires an API token")
        if self.local_backend == "openjev" and self.temperature_by_question:
            raise ValueError("OpenJev supports one temperature for the whole request")
        return self


def load_settings(path: Path | None = None) -> Settings:
    data: dict[str, object] = {}
    if path is not None:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is not None and not isinstance(loaded, dict):
            raise ValueError("Configuration must be a YAML mapping")
        data.update(loaded or {})
    # Keys are deliberately accepted only from the process environment.
    if "typesafe_api_key" in data or "rizzo_api_key" in data or "api_token" in data:
        raise ValueError("Credentials belong in environment variables, not YAML")
    for field in Settings.model_fields:
        if field == "rizzo_api_key":
            continue
        value = os.environ.get(f"ROUTER_{field.upper()}")
        if value is not None:
            if field == "temperature_by_question":
                value = json.loads(value)
            data[field] = value
    data["typesafe_api_key"] = os.environ.get("TYPESAFE_API_KEY", "")
    data["rizzo_api_key"] = os.environ.get("RIZZO_API_KEY", "")
    return Settings.model_validate(data)
