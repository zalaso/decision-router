# Raccolta etichette e calibrazione

Il codice prepara e misura una calibrazione; il repository **non contiene un
dataset rappresentativo**. I 16 esempi di smoke test e i due esempi qui sotto
sono stati creati per lo sviluppo e non possono validare un router autonomo.

Preparare un JSONL con un ID stabile, `split` (`calibration` o `test`), `prompt`,
`expected` per il routing e, per valutare la sicurezza, `expected_injection`,
`expected_malicious` e `expected_risk`. Il formato minimo è in
`benchmarks/labeled-input-example.jsonl`. Le etichette devono venire da una
rubrica revisionata e da persone competenti, non dalle predizioni del modello.
Campionare richieste reali autorizzate in italiano e inglese, casi innocui e
avversari, ambiguità, prompt fuori dominio e candidati dinamici. Separare gli
split per richiesta/origine, deduplicare varianti quasi identiche e mantenere
il test chiuso durante la scelta di modello, temperatura e soglie. Gestire il
dataset come dato sensibile: i prompt possono contenere segreti o PII.

Con un modello già scaricato, in PowerShell:

```powershell
$env:HF_HOME = "$PWD\.models"
$env:HF_HUB_OFFLINE = "1"
.venv314\Scripts\python benchmarks/run.py --config config/local-small.yaml --data benchmarks/labeled-input-example.jsonl --export-labeled .pytest_tmp/labeled-route.jsonl --output .pytest_tmp/benchmark-route.json
.venv314\Scripts\python benchmarks/calibrate.py --data .pytest_tmp/labeled-route.jsonl --output .pytest_tmp/calibration-route.json
```

Per ciascuna domanda, rieseguire l'export con `--question-id injection
--label-field expected_injection` (analogamente `malicious` e `risk`) e dare
un file di output distinto. Per `injection` e `malicious`, passare
`--positive-label true` a `calibrate.py`; per `risk`, usare i livelli positivi
definiti dalla policy, ad esempio `--positive-label 1 --positive-label 2`.
L'export richiede `mode=local`, NLI e temperatura 1 sulla domanda selezionata;
non serializza prompt né contesto. Include modello, revisione, ID, split,
etichetta e distribuzione. Il benchmark continua a riportare le metriche del
`route`, anche quando l'export riguarda un'altra domanda.

Il fitter ottimizza la temperatura solo sullo split `calibration`. Il report
mostra NLL, Brier, ECE e copertura/errore dei casi accettati sul test prima e
dopo; per le etichette positive mostra TP/FN/FP/TN e recall a più soglie.
Rifiuta ID duplicati fra split, distribuzioni invalide e miscele di modello,
revisione, domanda o numero di candidati. Meno di 50 esempi in uno split viene
segnalato come campione insufficiente, **non** come soglia statistica che
certifica i campioni maggiori. La calibrazione della temperatura può anche
peggiorare il test: l'esempio di due righe serve solo a verificare la pipeline.

Solo dopo una valutazione indipendente e una revisione del rischio si può
trascrivere una temperatura per domanda in `temperature_by_question` (vedere
`config/local-calibration.example.yaml`). Valutare separatamente la policy
completa: la calibrazione delle singole distribuzioni non dimostra che le
soglie di injection, intento malevolo, rischio e accettazione garantiscano una
copertura sicura. Il report mantiene sempre `production_ready: false`.
