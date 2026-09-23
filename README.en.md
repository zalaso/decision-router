# Decision Router

[Italiano](README.md) · [English](README.en.md)

[![Tests](https://github.com/zalaso/decision-router/actions/workflows/test.yml/badge.svg)](https://github.com/zalaso/decision-router/actions/workflows/test.yml)

First working vertical slice for Python >=3.12: typed decisions, interchangeable
providers, a demonstration router, and deterministic policies. No downstream
agent is executed.

## Quick start (no cloud or model weights)

Download the [source code from GitHub](https://github.com/zalaso/decision-router)
with **Code → Download ZIP**, or clone the repository:

```powershell
git clone https://github.com/zalaso/decision-router.git
cd decision-router
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\decision-router --config config/example.yaml route "Write Python code"
.venv\Scripts\decision-router decide examples/decision.json
.venv\Scripts\python -m uvicorn decision_router.api:app_factory --factory --host 127.0.0.1
```

If you downloaded the ZIP, extract it and open the project directory before
running `py -3.12`. On Linux/macOS, use `python3.12 -m venv .venv` and
`.venv/bin/`. To contribute or run tests, install `-e ".[dev]"`.
The default is a deterministic **fake** provider: it is not an ML classifier,
and its probabilities are synthetic. CLI exit codes: 0 for a decision,
2 for human review, and 1 for a startup or input error.

`uv.lock` is included. With uv, `uv sync --locked --extra dev` installs pinned
dependencies (`--extra local` adds Torch/Transformers). The checks actually
performed on Windows are recorded in [VERIFICATION](docs/VERIFICATION.md).

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/route -Method Post -ContentType application/json -Body '{"prompt":"Write Python code"}'
```

`POST /v1/decisions` accepts the contract shown in `examples/decision.json`.
`POST /v1/route` adds separate classification of prompt injection, malicious
intent, and risk. `/docs` exposes OpenAPI. Add choices through the
`candidates` field of `/v1/route`; keep `human_review` as a safe fallback.

## Real local model

```powershell
.venv\Scripts\python -m pip install -e ".[local]"
$env:HF_HOME = "$PWD\.models"
# Explicitly download public weights without loading remote code:
.venv\Scripts\python scripts/download_model.py --config config/local.yaml
# Run fully offline after the cache has been populated:
$env:ROUTER_LOCAL_FILES_ONLY = "true"
$env:HF_HUB_OFFLINE = "1"
.venv\Scripts\decision-router --config config/local.yaml decide examples/decision.json
```

The primary model is multilingual mDeBERTa NLI, with roughly 558 MB of weights
and float32 CPU inference. The smaller English alternative is
`config/local-small.yaml`, with roughly 26 MB of weights. Model revisions are
pinned, `trust_remote_code=False`, and only safetensors are used. These
distributions are transformations of NLI logits; **they are not calibrated
probabilities of correctness**. See [LOCAL_MODELS](docs/LOCAL_MODELS.md) for
comparisons and limitations.

The runtime loads weights in a child process and terminates it on timeout or
cancellation. The first run includes model loading; the startup budget is
separate from the timeout for an individual decision. In local mode, Choice
and Score descriptions must contain the full decision criterion: the NLI
classifier does not follow the generic `instructions` on those questions.
The response flags this in its warnings; Jev Cloud also uses `instructions`.

### Optional local OpenJev

An adapter for **GPT-AGI/OpenJev** with the public Qwen2.5-0.5B-Instruct
checkpoint is also available. It is a Jev-like wrapper distinct from the NLI
backend, selectable with `config/openjev.yaml`, and is not the default.
It wraps an open-source causal model; the checkpoint was not trained by Jev
and this is not a replica of TypeSafe. To try it after cloning:

```powershell
.venv\Scripts\python -m pip install -e ".[local]"
.venv\Scripts\python -m pip install "git+https://github.com/GPT-AGI/OpenJev.git@0e7bb990211df139da13dc67aa2a7aceca311af7"
$env:HF_HOME = "$PWD\.models"
.venv\Scripts\python scripts/download_model.py --config config/openjev.yaml
$env:HF_HUB_OFFLINE = "1"
.venv\Scripts\decision-router --config config/openjev.yaml route "Write Python code"
```

Weights and OpenJev are absent from the base installation. Without either,
the local provider is unavailable and the router abstains. OpenJev compares
only the first token of each candidate label; this adapter supports up to
26 Choice candidates and checks for label collisions. Its confidence is not
a calibrated probability of correctness. See [OPENJEV](docs/OPENJEV.md)
(currently in Italian) for the full constraints and implementation details.

### Optional local Rizzo Flow

[Rizzo Flow](https://github.com/Rizzo-AI-Academy/rizzo-flow) runs a local
service with the Jev-compatible `/v1/systemone` HTTP format. Select it as
the `rizzo_flow` backend with `config/rizzo-flow.yaml`. The router does not
install its weights or send your TypeSafe key to it. Start Rizzo Flow in a
separate terminal using its quickstart (`uv sync --locked`,
`uv run rizzo download`, `uv run rizzo serve`). Then, from the Decision
Router directory:

```powershell
.venv\Scripts\decision-router --config config/rizzo-flow.yaml route "Write Python code"
```

For a different port, set `ROUTER_RIZZO_BASE_URL` to a numeric HTTP
loopback origin, such as `http://127.0.0.1:8018`. If your Rizzo server
requires Bearer authentication, export `RIZZO_API_KEY` in the router's shell
too. An unreachable server or invalid response leads to human review in
`local` mode; distributions are not calibrated for routing. See
[RIZZO_FLOW](docs/RIZZO_FLOW.md) for details (currently in Italian).

## Cloud, auto, and shadow

Set `TYPESAFE_API_KEY` in your shell to use the official TypeSafe APIs.
Unofficial gateways and credential redirects are not supported.

```powershell
$env:ROUTER_MODE = "auto"
$env:ROUTER_LOCAL_BACKEND = "nli"
.venv\Scripts\decision-router route "Find current research on compilers"
```

`cloud` uses only Jev; `local` uses only the configured backend. In `auto`,
an error, timeout, or unsupported input falls back to the cloud; local
confidence between 0.55 and 0.80 falls back to the cloud, while confidence
below 0.55 requires human review. If the cloud is unavailable, the router
abstains. In `shadow`, `ROUTER_SHADOW_PRIMARY` selects the provider whose
decision is used; the other contributes only to the comparison. The response
waits for both within the timeout (no abandoned network task).

These thresholds are configurable examples: Jev confidence and the maximum
NLI probability have different meanings. Before real-world use, calibrate
separately for each model, task, and number of candidates. The procedure with
data splits and an offline report is in [CALIBRATION](docs/CALIBRATION.md).
With `TYPESAFE_API_KEY` already configured, `python scripts/check_shadow.py`
runs a live comparison limited to three synthetic examples (maximum ten);
cloud usage may incur charges. Without a key it prints `not_run` and does
not call Jev.

Configuration precedence: defaults < YAML (`--config` or `ROUTER_CONFIG`)
< `ROUTER_*` environment variables. `.env.example` is only an example and
is not loaded automatically. Keep keys and tokens in the environment.
Setting `ROUTER_API_TOKEN` enables Bearer authentication for both endpoints.
The service applies per-process request-per-minute and concurrency limits.
`ROUTER_REQUIRE_HTTPS=true` rejects HTTP on `POST /v1/` and requires a token.
Bind to loopback; external exposure needs a trusted TLS proxy, identity,
and quotas shared across processes.

## Library

```python
import asyncio
import logging
from decision_router import Choice, Candidate, DecisionRequest, DecisionEngine
from decision_router.audit import JsonAudit
from decision_router.config import Settings
from decision_router.providers.fake import FakeProvider

engine = DecisionEngine(settings=Settings(), local=FakeProvider(), cloud=None,
                        audit=JsonAudit(logging.getLogger("decision_router.audit")))
async def main():
    result = await engine.decide(DecisionRequest(prompt="write code", questions=[
        Choice(id="route", instructions="Choose agent", candidates=[
            Candidate(id="coder", description="write code"),
            Candidate(id="human", description="manual review"),
        ])
    ]))
    print(result.model_dump_json(indent=2))

asyncio.run(main())
```

## Verification and benchmarks

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy
.venv\Scripts\python -m ruff check .
.venv\Scripts\python benchmarks/run.py --config config/example.yaml
# After downloading both models:
.venv\Scripts\python scripts/download_model.py --config config/local-small.yaml
.venv\Scripts\python benchmarks/run.py --config config/local.yaml --compare-config config/local-small.yaml
$env:RUN_LOCAL_MODEL_TESTS = "1"
.venv\Scripts\python -m pytest tests/test_model_integration.py -q
```

The benchmark reports accuracy, agreement between providers, latency, Brier
score, and ECE. Its included sample is a synthetic smoke test, not a scientific
validation. `benchmarks/run.py --export-labeled` exports labeled NLI
distributions with data splits; `benchmarks/calibrate.py` measures temperature
and metrics on a separate test set. The demonstration data is unsuitable for
choosing operational temperatures.

In the final CPU measurement, mDeBERTa identifies the expected candidate in
15 of 16 cases, but takes about 4.48 seconds per request, and **all cases are
sent to human review** under the current thresholds. The smaller backend
takes about 195 ms and identifies 12 of 16 candidates. The prototype works;
autonomous routing still requires calibration and validation of risk questions.
Read [ARCHITECTURE](docs/ARCHITECTURE.md), [SECURITY](docs/SECURITY.md), and
[VERIFICATION](docs/VERIFICATION.md) for actual checks and limitations.

The original code is MIT-licensed; models and dependencies retain their own licenses.
