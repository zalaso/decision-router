# Decision Router

[Italiano](README.md) · [English](README.en.md)

[![Tests](https://github.com/zalaso/decision-router/actions/workflows/test.yml/badge.svg)](https://github.com/zalaso/decision-router/actions/workflows/test.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-3b5bdb)
![License](https://img.shields.io/badge/license-MIT-3b5bdb)

**Decides which AI agent should handle a request, and whether it is safe to.**

You have several agents (one writes code, one does research, one drives a
browser…). For each request, Decision Router suggests the best agent and, in
parallel, checks for prompt injection, malicious intent and risk level. When
something is unclear it does not guess: it asks for **human review**.

The router **only suggests**: it never runs agents, touches files or grants
permissions.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/dashboard-dark.png">
  <img alt="Decision Router dashboard: suggested agent, per-agent probabilities and safety checks" src="docs/img/dashboard.png">
</picture>

## Contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [The dashboard](#the-dashboard)
- [Using it from code: HTTP, CLI, Python](#using-it-from-code)
- [Using a real model](#using-a-real-model)
- [Configuration](#configuration)
- [Security and limitations](#security-and-limitations)
- [Development](#development)

## Quick start

You need **Python 3.12 or newer** ([download](https://www.python.org/downloads/)).
The first run uses **demo mode**: no model to download, no cloud account, no
data leaving your computer.

### Option A: double-click (recommended to try it)

1. On GitHub, click **Code → Download ZIP** and extract the folder
   (or `git clone https://github.com/zalaso/decision-router.git`).
2. Start it:
   - **Windows**: double-click `start.bat`
   - **macOS / Linux**: run `./start.sh` in a terminal

On first run the script creates a `.venv` environment and installs the
dependencies (about a minute), then opens the dashboard at
<http://127.0.0.1:8000/dashboard/>. Later runs start instantly. To stop it,
press `Ctrl+C` in the terminal window.

### Option B: install as a command, straight from GitHub

With [pipx](https://pipx.pypa.io/) or [uv](https://docs.astral.sh/uv/), without
downloading the code by hand:

```bash
pipx install git+https://github.com/zalaso/decision-router.git
```

```bash
decision-router serve --open
```

With uv: `uv tool install git+https://github.com/zalaso/decision-router.git`.
To update: `pipx upgrade decision-router` (or `uv tool upgrade decision-router`).

### Option C: for development

```powershell
git clone https://github.com/zalaso/decision-router.git
cd decision-router
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\decision-router serve --open
```

On Linux/macOS, use `python3.12 -m venv .venv` and `.venv/bin/` instead of
`.venv\Scripts\`. `uv.lock` is included: `uv sync --locked --extra dev`
installs the pinned versions.

## How it works

```mermaid
flowchart LR
    R["Request<br/>+ context<br/>+ candidate agents"] --> Q["4 typed questions<br/>which agent? · injection?<br/>malicious? · how risky?"]
    Q --> P{"Provider"}
    P -->|demo| F[fake]
    P -->|local| L["NLI · OpenJev · Rizzo Flow"]
    P -->|cloud| C[Jev TypeSafe]
    F & L & C --> D["Probability<br/>distributions"]
    D --> POL["Deterministic policy<br/>(fixed rules, not AI)"]
    POL -->|clear and safe| A["Suggested agent"]
    POL -->|doubt or risk| H["human_review"]
```

1. **Request.** A text arrives, with optional context and the list of agents
   to choose from (each with a natural-language description).
2. **Typed questions.** The router turns the request into four questions: a
   *choice* (which agent?), two *yes/no* (is it prompt injection? is it
   malicious?) and a *score* (low, medium or high risk).
3. **Provider.** A classifier answers with a probability distribution for
   each question. The provider is interchangeable: demo, local model or cloud.
4. **Deterministic policy.** Fixed rules, not AI, decide the outcome. If
   confidence is low, a safety check crosses its threshold or an answer is
   missing, the outcome is `human_review`. An error or timeout is never
   treated as "safe".

Responses always contain `granted_capabilities: []`: a prediction never
becomes a permission. Whoever integrates the router decides what to run.

### Modes

| Mode | What it does |
|---|---|
| `local` (default) | Uses the local backend only. No data leaves the computer. |
| `cloud` | Uses only TypeSafe's Jev cloud API (requires `TYPESAFE_API_KEY`). |
| `auto` | Tries locally. Confidence between 0.55 and 0.80, an error or a timeout: falls back to the cloud. Below 0.55: human review. |
| `shadow` | Queries local and cloud in parallel: uses the primary, records whether the secondary agrees. For comparing models. |

### Local backends

| Backend | Description | Weights |
|---|---|---|
| `fake` (default) | Deterministic keyword demo. **Not a classifier**: synthetic probabilities. | none |
| `nli` | Multilingual mDeBERTa NLI, CPU, offline. Small English variant in `config/local-small.yaml`. | ~558 MB / ~26 MB |
| `openjev` | Optional GPT-AGI/OpenJev adapter with Qwen2.5-0.5B-Instruct. | ~1 GB |
| `rizzo_flow` | Local [Rizzo Flow](https://github.com/Rizzo-AI-Academy/rizzo-flow) service, Jev-compatible HTTP format. | managed by Rizzo |

## The dashboard

Open it with `decision-router serve --open` (or the start scripts) at
<http://127.0.0.1:8000/dashboard/>. It is served by the API itself, with no
build step, no CDN and no extra dependencies: it works offline too.

- **Route**: type a request (or try the examples, including a prompt
  injection and a malicious request) and see the suggested agent, the
  probability for each agent and the three safety checks against their
  thresholds. Candidate agents can be edited right on the page.
- **Custom decision**: build your own questions (choice, yes/no, score)
  about a text and get the probability distributions, without the router policy.
- **How it works**: an explanation of the flow, modes and backends.
- **Configuration**: the mode, backend and thresholds the server was started
  with. Keys are never shown, only whether they are present. Configuration
  cannot be changed from the browser, on purpose.

Every result has **Copy JSON**, **Copy as curl** and **Copy as PowerShell**
buttons, so you can move straight from trying to integrating. History stays
in the browser tab only. Italian and English UI, automatic light and dark theme.

A link such as `http://127.0.0.1:8000/dashboard/?prompt=Write%20Python%20code`
opens the dashboard and routes that request immediately.

To expose only the API without the UI: `ROUTER_DASHBOARD=false`.

## Using it from code

### HTTP

`decision-router serve` starts the API on `127.0.0.1:8000`. Interactive
OpenAPI documentation is at <http://127.0.0.1:8000/docs>.

| Endpoint | Use |
|---|---|
| `POST /v1/route` | Route a request: suggested agent + safety checks + policy. |
| `POST /v1/decisions` | Generic decision with your own questions (contract in `examples/decision.json`). |
| `GET /v1/status` | Active configuration, without secrets. |
| `GET /health` | Service health. |

```bash
curl -s http://127.0.0.1:8000/v1/route -H "Content-Type: application/json" -d '{"prompt":"Write Python code"}'
```

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/route -Method Post -ContentType application/json -Body '{"prompt":"Write Python code"}'
```

Response (abridged):

```json
{
  "policy": {
    "agent": "coding_agent",
    "review_required": false,
    "reasons": [],
    "granted_capabilities": []
  },
  "evaluation": {
    "status": "decided",
    "decision": {"provider": "fake", "answers": {"route": {"selected": "coding_agent", "confidence": 0.9}}}
  }
}
```

Customize the agents with the `candidates` field
(`[{"id": "...", "description": "..."}]`); keep `human_review` among the
candidates as the safe way out.

### Command line

```bash
decision-router route "Write Python code"
decision-router decide examples/decision.json
decision-router serve --port 9000 --config config/example.yaml
```

Exit codes: `0` decided, `2` human review, `1` startup or input error.
Handy in scripts: `decision-router route "..." || echo "a person must decide"`.

### Python library

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

## Using a real model

### Local NLI model (offline, free)

```powershell
.venv\Scripts\python -m pip install -e ".[local]"
$env:HF_HOME = "$PWD\.models"
# Explicitly download public weights, without remote code:
.venv\Scripts\python scripts/download_model.py --config config/local.yaml
# From here on, fully offline:
$env:HF_HUB_OFFLINE = "1"
.venv\Scripts\decision-router --config config/local.yaml serve --open
```

The primary model is multilingual mDeBERTa NLI (about 558 MB, CPU float32).
The small English alternative is `config/local-small.yaml` (about 26 MB):
much faster, less accurate. Revisions are pinned, `trust_remote_code=False`,
safetensors only. The model runs in a child process that is terminated on
timeout. Locally, candidate and level **descriptions** are what matter: the
NLI classifier does not read `instructions`. Comparison and limitations in
[LOCAL_MODELS](docs/LOCAL_MODELS.md).

### Local OpenJev (optional)

Adapter for **GPT-AGI/OpenJev** with the public Qwen2.5-0.5B-Instruct
checkpoint, selected with `config/openjev.yaml`. It is not the default and not
a replica of TypeSafe Jev. Pinned installation and limitations in
[OPENJEV](docs/OPENJEV.md) (currently in Italian).

### Local Rizzo Flow (optional)

Start [Rizzo Flow](https://github.com/Rizzo-AI-Academy/rizzo-flow) in a
separate terminal (`uv sync --locked`, `uv run rizzo download`,
`uv run rizzo serve`), then:

```powershell
.venv\Scripts\decision-router --config config/rizzo-flow.yaml serve --open
```

The address must be a numeric HTTP loopback origin (`ROUTER_RIZZO_BASE_URL`,
default `http://127.0.0.1:8017`); an optional `RIZZO_API_KEY` is read from the
environment only. Details in [RIZZO_FLOW](docs/RIZZO_FLOW.md) (currently in Italian).

### Jev cloud (TypeSafe)

```powershell
$env:TYPESAFE_API_KEY = "..."   # environment only, never in YAML files
$env:ROUTER_MODE = "auto"
$env:ROUTER_LOCAL_BACKEND = "nli"
.venv\Scripts\decision-router serve --open
```

`auto` and `shadow` may send prompts and context to the cloud: use `local` for
data that must not leave the computer. Cloud usage may incur charges.
`python scripts/check_shadow.py` runs a limited live comparison (3 synthetic
examples, maximum 10); without a key it prints `not_run`.

## Configuration

Precedence: defaults < YAML file (`--config` or `ROUTER_CONFIG`) <
`ROUTER_*` environment variables. `.env.example` lists every variable but is
not loaded automatically.

| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_MODE` | `local` | `local`, `cloud`, `auto`, `shadow` |
| `ROUTER_LOCAL_BACKEND` | `fake` | `fake`, `nli`, `openjev`, `rizzo_flow` |
| `ROUTER_TIMEOUT_S` | `5` | Timeout per single attempt |
| `ROUTER_LOCAL_ACCEPT_CONFIDENCE` | `0.80` | Above: the local result is accepted |
| `ROUTER_REVIEW_BELOW_CONFIDENCE` | `0.55` | Below: human review |
| `ROUTER_SAFETY_MIN_CONFIDENCE` | `0.80` | Minimum confidence of the safety checks |
| `ROUTER_INJECTION_THRESHOLD` / `_MALICIOUS_` / `_RISK_` | `0.30` | Thresholds of the three checks |
| `ROUTER_DASHBOARD` | `true` | Serve the dashboard at `/dashboard/` |
| `ROUTER_API_TOKEN` | empty | If set, requires `Authorization: Bearer <token>` |
| `TYPESAFE_API_KEY` | empty | Key for the Jev cloud |

The thresholds are **uncalibrated examples**: Jev confidence and the maximum
NLI probability mean different things. Before real use, calibrate them per
model, task and number of candidates: procedure in [CALIBRATION](docs/CALIBRATION.md).

## Security and limitations

- **Routing and authorization stay separate.** The router runs nothing and
  grants no permissions, even if the prompt asks for them.
- **Fail-closed.** Missing, malformed or uncertain evidence leads to human
  review; an unavailable model never counts as "safe".
- **The `fake` backend classifies nothing**: it only exists to try the UI and API.
- **Classifiers do not catch every attack.** Authorization in the system that
  uses the router must hold even when classification is wrong.
- **Loopback only by default.** `decision-router serve` refuses to listen on a
  non-local address unless `ROUTER_API_TOKEN` is set. Network exposure also
  needs a trusted TLS proxy and quotas shared across processes
  (`ROUTER_REQUIRE_HTTPS=true` behind the proxy).
- **Sanitized logs**: no prompts, context, keys or URLs in the audit log.

In the included CPU measurement, mDeBERTa identifies the expected candidate in
15 of 16 cases but takes about 4.5 seconds per request and, under the current
thresholds, sends **all** cases to review; the small model takes about 195 ms
and identifies 12 of 16. The prototype works; autonomous routing still needs
calibration. Details in [ARCHITECTURE](docs/ARCHITECTURE.md),
[SECURITY](docs/SECURITY.md) and [VERIFICATION](docs/VERIFICATION.md).

## Development

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy
.venv\Scripts\python -m ruff check .
.venv\Scripts\python benchmarks/run.py --config config/example.yaml
```

With the models downloaded:

```powershell
.venv\Scripts\python scripts/download_model.py --config config/local-small.yaml
.venv\Scripts\python benchmarks/run.py --config config/local.yaml --compare-config config/local-small.yaml
$env:RUN_LOCAL_MODEL_TESTS = "1"
.venv\Scripts\python -m pytest tests/test_model_integration.py -q
```

The benchmark reports accuracy, agreement between providers, latency, Brier
score and ECE; the included sample is a synthetic smoke test, not a scientific
validation. `benchmarks/run.py --export-labeled` exports labeled distributions
and `benchmarks/calibrate.py` measures temperature and metrics on a separate
test set.

The dashboard lives in `src/decision_router/static/` (dependency-free HTML,
CSS and JavaScript) and is served by `src/decision_router/dashboard.py`.

## License

The original code is MIT-licensed; models and dependencies retain their own licenses.
