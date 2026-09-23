# Rizzo Flow come backend locale

[Rizzo Flow](https://github.com/Rizzo-AI-Academy/rizzo-flow) è un progetto
indipendente con modelli aperti e un endpoint HTTP compatibile con il formato
pubblico Jev `POST /v1/systemone`. Non usa i pesi proprietari Jev. Decision
Router invia le proprie domande Choice, Boolean e Score al servizio locale e
valida che la risposta contenga esattamente domande, candidati e distribuzioni
attesi. La policy di routing resta nel Decision Router.

## Avvio

Rizzo Flow è un servizio separato: installarlo e scaricare i pesi seguendo
[il quickstart upstream](https://github.com/Rizzo-AI-Academy/rizzo-flow#quickstart).
I comandi sotto fissano la revisione upstream
`d34665b7a28c62b79f37939f2fd83f5fe659fbf9` usata per verificare il
contratto descritto qui.
Il download predefinito include Spark-X2.5-4B Q8_0 (~4,4 GB) e il runtime
llama.cpp; queste dipendenze non sono incluse nella repository o nella wheel
di Decision Router. In una prima shell PowerShell:

```powershell
git clone https://github.com/Rizzo-AI-Academy/rizzo-flow.git
cd rizzo-flow
git checkout d34665b7a28c62b79f37939f2fd83f5fe659fbf9
uv sync --locked
uv run rizzo download
uv run rizzo serve
```

Quando il servizio risponde su `http://127.0.0.1:8017`, in una seconda shell
nella cartella di Decision Router:

```powershell
.venv\Scripts\python -m pip install -e .
.venv\Scripts\decision-router --config config/rizzo-flow.yaml route "Write Python code"
```

È disponibile anche `decide examples/decision.json` o l'API HTTP di Decision
Router. Se Rizzo Flow ascolta su un'altra porta, impostare
`ROUTER_RIZZO_BASE_URL=http://127.0.0.1:PORTA`. Il parametro accetta soltanto
un origin HTTP con indirizzo IP numerico di loopback e porta esplicita
(`127.0.0.1` o `[::1]`); non accetta host remoti, credenziali nell'URL,
path o query. L'adapter non segue redirect e ignora i proxy dell'ambiente.

Rizzo Flow non richiede un token per default. Se si imposta `RIZZO_API_KEY`
per il suo server, impostare lo stesso valore nell'ambiente del processo
Decision Router. Il token non va nel YAML. La chiave `TYPESAFE_API_KEY`
rimane separata ed è inviata solo all'endpoint ufficiale TypeSafe se una
modalità usa il cloud.

## Semantica e limiti

- La risposta indica `provider: "rizzo_flow"` e riporta l'ID del modello
  ricevuto dal server, non `jev_cloud`. Choice conserva la confidence Rizzo;
  per Boolean il router calcola `max(P(true), P(false))`.
- Le Choice hanno al massimo 26 candidati nel formato compatibile Jev.
  Rizzo Flow rifiuta input fuori limite; il router respinge 27+ candidati
  prima della chiamata HTTP. Score resta limitato a 10 livelli dal dominio.
- L'endpoint compatibile Jev di Rizzo Flow non espone l'astensione nativa
  disponibile nella sua API `/v1/decisions`. Il router applica le proprie
  soglie di revisione e la policy di rischio, che richiedono comunque
  calibrazione su dati pertinenti.
- Un server assente, un timeout o una risposta malformata non attivano il
  provider fake. In `local` il risultato è `human_review`. In `auto` valgono
  le normali regole di fallback, che possono inviare prompt e contesto a
  TypeSafe Cloud se configurato.
- Rizzo Flow gira in un processo esterno: il timeout del router termina la
  richiesta HTTP, non il processo Rizzo. Il server va gestito e arrestato
  separatamente.

I test in questa repository usano un trasporto HTTP mockato per verificare
wire contract, configurazione, segreti, errori e fail-closed. La qualità del
modello e le prestazioni su hardware reale richiedono una misura separata.
