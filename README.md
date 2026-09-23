# Decision Router

[Italiano](README.md) · [English](README.en.md)

[![Tests](https://github.com/zalaso/decision-router/actions/workflows/test.yml/badge.svg)](https://github.com/zalaso/decision-router/actions/workflows/test.yml)

Primo vertical slice Python >=3.12: decisioni tipizzate, provider intercambiabili,
router dimostrativo e policy deterministiche. Nessun agente downstream viene eseguito.

## Avvio rapido (senza cloud o pesi)

Scaricare il [codice sorgente da GitHub](https://github.com/zalaso/decision-router)
con **Code → Download ZIP** oppure clonare la repository:

```powershell
git clone https://github.com/zalaso/decision-router.git
cd decision-router
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\decision-router --config config/example.yaml route "Write Python code"
.venv\Scripts\decision-router decide examples/decision.json
.venv\Scripts\python -m uvicorn decision_router.api:app_factory --factory --host 127.0.0.1
```

Con lo ZIP, estrarlo e aprire la cartella prima del comando `py -3.12`.
Su Linux/macOS usare `python3.12 -m venv .venv` e `.venv/bin/`.
Per contribuire o eseguire i test installare `-e ".[dev]"`.
Il default è un **fake**
deterministico: non è un classificatore ML, le probabilità sono sintetiche.
CLI: exit 0 decisione, 2 revisione umana, 1 errore di avvio/input.

È incluso `uv.lock`: con uv, `uv sync --locked --extra dev` installa le
dipendenze fissate (`--extra local` aggiunge Torch/Transformers). Le verifiche
effettivamente svolte su Windows sono in [VERIFICATION](docs/VERIFICATION.md).

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/route -Method Post -ContentType application/json -Body '{"prompt":"Write Python code"}'
```

`POST /v1/decisions` accetta il contratto di `examples/decision.json`;
`POST /v1/route` aggiunge la classificazione separata di injection, intento malevolo
e rischio. `/docs` espone OpenAPI. I candidati si possono aggiungere nel campo
`candidates` di `/v1/route`; mantenere `human_review` per il fallback sicuro.

## Modello locale reale

```powershell
.venv\Scripts\python -m pip install -e ".[local]"
$env:HF_HOME = "$PWD\.models"
# Download esplicito dei pesi pubblici, senza caricare codice remoto:
.venv\Scripts\python scripts/download_model.py --config config/local.yaml
# Esecuzioni successive completamente offline, con cache già popolata:
$env:ROUTER_LOCAL_FILES_ONLY = "true"
$env:HF_HUB_OFFLINE = "1"
.venv\Scripts\decision-router --config config/local.yaml decide examples/decision.json
```

Primario: mDeBERTa NLI multilingue, circa 558 MB di pesi, CPU float32.
Alternativo piccolo inglese: `config/local-small.yaml`, circa 26 MB di pesi.
Revisioni bloccate e `trust_remote_code=False`; solo safetensors.
Le distribuzioni sono trasformazioni dei logits NLI, **non equivalgono a
probabilità calibrate di correttezza**. Confronto e limiti in
[LOCAL_MODELS](docs/LOCAL_MODELS.md).
Il runtime carica i pesi in un processo figlio e lo termina su timeout o
cancellazione. Il primo avvio include il caricamento del modello; il budget di
startup è distinto dal timeout della singola decisione.
Nel locale le descrizioni di Choice/Score devono contenere l'intero criterio:
il classificatore NLI non esegue le istruzioni generiche di queste domande.
La risposta lo segnala nei warning; Jev Cloud usa anche `instructions`.

### OpenJev locale opzionale

È disponibile anche un adapter per **GPT-AGI/OpenJev** con il checkpoint
pubblico Qwen2.5-0.5B-Instruct. È un wrapper Jev-like realmente distinto
dal backend NLI, selezionabile con `config/openjev.yaml`, ma non è il default.
Installazione alla revisione fissata, download dei pesi e limiti del confronto
dei primi token sono spiegati in [OPENJEV](docs/OPENJEV.md). L'installazione
base funziona senza questa dipendenza opzionale.

### Rizzo Flow locale opzionale

[Rizzo Flow](https://github.com/Rizzo-AI-Academy/rizzo-flow) espone un servizio
locale con il formato HTTP Jev `/v1/systemone`. Il router lo usa come backend
`rizzo_flow` tramite `config/rizzo-flow.yaml`, senza installare i suoi pesi o
inviare la chiave TypeSafe. Avviare Rizzo Flow in un terminale separato,
seguendo il suo quickstart (`uv sync --locked`, `uv run rizzo download`,
`uv run rizzo serve`). Poi, dalla cartella Decision Router:

```powershell
.venv\Scripts\decision-router --config config/rizzo-flow.yaml route "Write Python code"
```

Per una porta diversa impostare `ROUTER_RIZZO_BASE_URL` a un origin HTTP
numerico di loopback, per esempio `http://127.0.0.1:8018`. Se il server Rizzo
richiede Bearer auth, esportare `RIZZO_API_KEY` anche nella shell del router.
Un server non raggiungibile o una risposta non valida portano a revisione
umana in modalità `local`; le distribuzioni non sono calibrate per il routing.
Dettagli e limiti in [RIZZO_FLOW](docs/RIZZO_FLOW.md).

## Cloud, auto e shadow

Esportare `TYPESAFE_API_KEY` nella shell per le API ufficiali TypeSafe.
Non sono supportati gateway non ufficiali o redirect delle credenziali.

```powershell
$env:ROUTER_MODE = "auto"
$env:ROUTER_LOCAL_BACKEND = "nli"
.venv\Scripts\decision-router route "Find current research on compilers"
```

`cloud` usa solo Jev; `local` solo il backend configurato. In `auto`, errore,
timeout o input non supportato passa al cloud; confidence locale tra 0.55 e
0.80 passa al cloud, sotto 0.55 richiede revisione umana. Se il cloud non è
disponibile, la decisione si astiene. In `shadow`, `ROUTER_SHADOW_PRIMARY`
sceglie il provider effettivo; l'altro contribuisce solo al confronto.
La risposta attende entrambi entro timeout (nessun task abbandonato di rete).

Le soglie sono esempi configurabili: la confidence Jev e il massimo delle
probabilità NLI hanno semantiche diverse. Prima dell'uso reale, calibrare
separatamente modello/task/numero di candidati. La procedura con split e
report offline è in [CALIBRATION](docs/CALIBRATION.md). Con una chiave
`TYPESAFE_API_KEY` già configurata, `python scripts/check_shadow.py` esegue
un confronto live limitato a 3 esempi sintetici (massimo 10); l'uso del cloud
può comportare addebiti. Senza chiave stampa `not_run` e non chiama Jev.

Configurazione: default < YAML (`--config` o `ROUTER_CONFIG`) < variabili
`ROUTER_*`. `.env.example` è solo un esempio, non viene caricato automaticamente.
Chiavi e token soltanto nell'ambiente. Impostare `ROUTER_API_TOKEN` abilita Bearer
auth per i due endpoint. Il servizio applica limiti di richieste/minuto e
concorrenza per processo; `ROUTER_REQUIRE_HTTPS=true` rifiuta HTTP sui POST
`/v1/` e richiede
un token. Avviare su loopback: per esposizione esterna servono un proxy TLS
fidato, identità e quote condivise fra processi.

## Libreria

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

## Verifica e benchmark

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy
.venv\Scripts\python -m ruff check .
.venv\Scripts\python benchmarks/run.py --config config/example.yaml
# Dopo aver scaricato entrambi i modelli:
.venv\Scripts\python scripts/download_model.py --config config/local-small.yaml
.venv\Scripts\python benchmarks/run.py --config config/local.yaml --compare-config config/local-small.yaml
$env:RUN_LOCAL_MODEL_TESTS = "1"
.venv\Scripts\python -m pytest tests/test_model_integration.py -q
```

Il benchmark riporta accuratezza, agreement tra provider, latenza, Brier ed ECE;
il campione incluso è uno smoke test sintetico, non una validazione scientifica.
`benchmarks/run.py --export-labeled` esporta distribuzioni NLI con etichette
e split; `benchmarks/calibrate.py` misura temperatura e metriche sul test
separato. I dati dimostrativi non sono adatti a impostare temperature operative.
Nella misura CPU finale mDeBERTa individua il candidato atteso in 15/16 casi,
ma richiede circa 4,48 secondi per richiesta e **tutti i casi vengono inviati
a revisione** con le soglie attuali. Il backend piccolo impiega circa 195 ms
e individua 12/16 candidati. Il prototipo è funzionante; il routing autonomo
richiede ancora calibrazione e validazione delle domande di rischio.
Leggere [ARCHITECTURE](docs/ARCHITECTURE.md), [SECURITY](docs/SECURITY.md)
e [VERIFICATION](docs/VERIFICATION.md) per verifiche effettive e limitazioni.

Licenza MIT per il codice originale; modelli e dipendenze mantengono le proprie licenze.
