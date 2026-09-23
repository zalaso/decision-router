# Verifiche del vertical slice

Ambiente: Windows 11, Python **3.14.4**, PyTorch **2.14.0+cpu**,
Transformers **4.57.6**. CUDA non disponibile. Il progetto mantiene Python
>=3.12 come requisito: il Python 3.12 scaricato è stato bloccato dalla policy
Windows di controllo applicazioni (errore 4551), quindi non viene dichiarata
un'esecuzione locale su 3.12. La matrice GitHub Actions 3.12/3.14 è stata
eseguita con successo sul commit `9837073`: [run](https://github.com/zalaso/decision-router/actions/runs/35830339061).
Dipendenze risolte in `uv.lock`.

## Eseguito realmente

- **75 test senza pesi passati**; i due test che richiedono il modello vengono
  saltati per default. Copertura di Choice/Boolean/Score, adapter cloud mockato,
  NLI con scorer iniettato, fake, candidati dinamici, quattro modalità,
  soglie ai confini, timeout, errori malformati, shadow, policy fail-closed,
  protezione credenziali/log e configurazione. I test nuovi verificano il
  worker in processo separato, la sua terminazione su timeout/cancellazione,
  limiti HTTP/TLS gate e il report di calibrazione su split disgiunti.
- **2 test aggiuntivi con mDeBERTa reale passati**, in modalità offline: tutte
  e tre le primitive, scelta del candidato coding su un input semplice e
  rifiuto di input oltre la finestra senza troncamento.
- Test end-to-end HTTP in processo, lifecycle FastAPI e OpenAPI; CLI eseguita
  in subprocess e riprovata direttamente con risultato `coding_agent` e nessun
  permesso concesso. Non è stato lasciato un server in background.
- `mypy` in modalità strict: **nessun problema nei 18 file sorgente**.
- `ruff check`, `ruff format --check`, compilazione sorgenti e `pip check`
  completati senza errori dopo le correzioni.
- Pesi pubblici dei due modelli scaricati con revisioni fissate e realmente
  caricati/inferiti su CPU. Il confronto locale non usa servizi di inferenza cloud.
- Ricerca su codice, release, licenze e pesi: vedere `LOCAL_MODELS.md`.
  I 13 hash e i 33 link del dossier sono stati verificati tramite API/fonti primarie.
- CLI reale con modello piccolo attraverso il processo figlio: esito corretto
  e astensione della policy. Export JSONL delle distribuzioni per 2 esempi e
  fitter su split 1/1 riusciti. Il report segnala campione insufficiente e
  `production_ready: false`; il valore stimato non è stato applicato.
- `scripts/check_shadow.py` eseguito senza `TYPESAFE_API_KEY`: ha restituito
  `not_run` senza inviare richieste al cloud.
- Wheel e sdist `0.1.0` costruiti in locale. In CI, su Python 3.12 e 3.14,
  passano test, mypy, Ruff, build, installazione della wheel e smoke test del
  comando `decision-router` installato.

Comandi riproducibili nell'ambiente già creato:

```powershell
.venv314\Scripts\python -m pytest -q
.venv314\Scripts\python -m mypy
.venv314\Scripts\python -m ruff check src tests benchmarks scripts
$env:HF_HOME = "$PWD\.models"
$env:HF_HUB_OFFLINE = "1"
$env:RUN_LOCAL_MODEL_TESTS = "1"
.venv314\Scripts\python -m pytest tests/test_model_integration.py -q
.venv314\Scripts\python benchmarks/run.py --config config/local.yaml --compare-config config/local-small.yaml --output benchmarks/results-local.json
```

## Benchmark e interpretazione

I report completi sono `benchmarks/results-fake.json`,
`benchmarks/results-local.json` e `benchmarks/results-local-initial.json`.
Contengono 16 esempi sintetici, inglesi e italiani, con accuratezza del candidato
grezzo, agreement, latenza, Brier multiclass, NLL ed ECE su 10 bin. `coverage`
si riferisce all'accettazione dell'orchestratore; `policy_coverage` include
anche i controlli di sicurezza. L'accuratezza del candidato resta misurabile
quando la decisione operativa è `human_review`.

Il confronto fake/fake verifica il meccanismo di agreement, non la qualità ML.
Il confronto locale è mDeBERTa/XtremeDistil, non locale/Jev. L'agreement per
Score nello shadow è quello del livello più probabile.

Il dataset è stato consultato durante lo sviluppo: **non è un test set
indipendente, né una validazione della sicurezza**. Nessuna temperatura o
soglia è stata adattata per aumentare artificialmente la copertura.
L'ECE usa la probabilità della classe selezionata, non la diversa statistica
`confidence` che può provenire da Jev. I report non dimostrano calibrazione.

Il report iniziale conserva il fallimento della formulazione che aggiungeva
le istruzioni alla fine dell'ipotesi NLI: 6/16 corretti, agreement 43,75%,
latenza media circa 5,24 s e tutti i casi in revisione. È stato usato per
correggere l'adapter, senza nascondere il limite emerso.

### Misura del primo ciclo con l'adapter corrente

Questa tabella precede l'isolamento dell'inferenza in un processo figlio. Le
metriche di qualità del candidato restano riferite alla stessa formulazione
NLI; startup e latenza vanno rimisurati sul carico e sulla macchina di rilascio.

| Metrica | Risultato |
|---|---:|
| mDeBERTa: candidato corretto prima della policy | 15/16, 93,75% |
| XtremeDistil: candidato corretto prima della policy | 12/16, 75% |
| Agreement mDeBERTa/XtremeDistil | 11/16, 68,75% |
| Latenza media mDeBERTa, quattro domande per richiesta | 4.484 ms |
| Latenza mediana / p95 mDeBERTa | 4.365 / 5.642 ms |
| Latenza media mDeBERTa esclusa prima richiesta | 4.407 ms |
| Startup primario | 29,47 s |
| Latenza media XtremeDistil | 194,79 ms |
| Brier multiclass / NLL mDeBERTa | 0,2372 / 0,5558 |
| ECE 10 bin mDeBERTa | 0,3258 |
| Copertura accettata orchestratore / policy | 0% / 0% |

**Tutti i 16 casi terminano operativamente in `human_review`**, benché il
candidato grezzo sia spesso corretto. L'orchestratore considera la confidence
minima fra routing e domande di rischio; le soglie conservative e le evidenze
di sicurezza non calibrate impediscono l'automazione. Il 93,75% non va quindi
presentato come accuratezza di un router autonomo pronto all'uso. Non sono
state abbassate soglie per eludere questo risultato.

Il primario rimane una baseline multilingue per qualità dei candidati; il
modello alternativo dimostra la differenza di latenza, ma sbaglia quattro
esempi e non è una sostituzione multilingue equivalente. Il percorso fake è
utile per verificare la meccanica end-to-end, non per aggirare questi limiti.

## Non verificato e rischi aperti

- Nessuna chiamata reale Jev Cloud: assenza di chiave configurata; contratto
  verificato contro documentazione ufficiale e trasporto mockato.
- Nessuna certificazione di robustezza a prompt injection, accuratezza in
  produzione, calibrazione italiana o validità delle soglie di esempio.
- Il NLI non esegue istruzioni generiche Choice/Score: servono descrizioni
  autosufficienti. Boolean esclude neutral e usa la sola rubrica positiva.
- Il processo NLI viene terminato su timeout/cancellazione e il provider
  resta indisponibile dopo il guasto; non c'è riavvio automatico. Il processo
  non costituisce sandbox per pesi o dipendenze non fidati.
- Cold start e latenza CPU rendono il primario inadatto a promettere tempi
  sotto il secondo su questa macchina. Il modello piccolo è un'alternativa
  esplicita inglese, da validare sul carico previsto.
- Warning upstream osservati: deprecazioni Starlette/TestClient e avviso Torch
  su JIT con Python 3.14. Non hanno impedito le verifiche, ma rafforzano la
  necessità di eseguire la matrice Python 3.12 prima del rilascio.
- Nessun audit completo delle vulnerabilità delle dipendenze o test di carico;
  il controllo `pip check` verifica compatibilità dei requisiti, non sicurezza.
- I limiti HTTP sono in memoria e per processo; TLS e identità condivisa
  devono essere forniti dall'infrastruttura prima di esposizione esterna.

## Lavoro che richiede dati o ambiente aggiuntivi

1. Raccogliere un dataset rappresentativo italiano/inglese, etichettato e con
   split separati; usare la pipeline in `CALIBRATION.md` e misurare il rischio
   residuo della policy completa prima di applicare temperature o soglie.
2. Con una chiave TypeSafe configurata, eseguire il controllo live e il shadow
   locale/Jev, misurando qualità, costi e latenza senza cambiare la policy.
3. Completare proxy TLS, identità/quote condivise e test di carico prima
   dell'esposizione esterna. La copia Python 3.12 scaricata su questa macchina
   rimane bloccata da Windows; la verifica 3.12 ora è coperta dalla CI.
