# Backend locale GPT-AGI/OpenJev

`local_backend: openjev` usa la [libreria GPT-AGI/OpenJev](https://github.com/GPT-AGI/OpenJev)
alla revisione `0e7bb990211df139da13dc67aa2a7aceca311af7`, con il suo
backend Hugging Face. **È un wrapper su un modello causale open source, non
un checkpoint addestrato da Jev e non una replica di TypeSafe.** Il router
continua a esporre il proprio contratto e le proprie policy; OpenJev rimane
dietro l'adapter e gira nel processo figlio terminabile.

Per provarlo dopo aver clonato la repository, da PowerShell:

```powershell
.venv\Scripts\python -m pip install -e ".[local]"
.venv\Scripts\python -m pip install "git+https://github.com/GPT-AGI/OpenJev.git@0e7bb990211df139da13dc67aa2a7aceca311af7"
$env:HF_HOME = "$PWD\.models"
.venv\Scripts\python scripts/download_model.py --config config/openjev.yaml
$env:HF_HUB_OFFLINE = "1"
.venv\Scripts\decision-router --config config/openjev.yaml route "Write Python code"
```

Il modello predefinito per questa modalità è
`Qwen/Qwen2.5-0.5B-Instruct` alla revisione
`7ae557604adf67be50417f59c2c2f167def9a775`. Pesi e libreria OpenJev
non sono inclusi nella repository o nell'installazione base. Senza libreria o
pesi, il provider locale risulta indisponibile e il router si astiene; non
passa silenziosamente al fake.

OpenJev confronta **solo il primo token** di ogni etichetta. L'adapter assegna
etichette corte `A`–`Z` ai candidati dinamici, mette ID e descrizioni nelle
istruzioni e rifiuta la richiesta se due etichette hanno lo stesso primo token
nel tokenizer del modello. Per questa modalità Choice è limitato a 26
candidati; Score usa i livelli `0`–`9`, Boolean usa `yes/no`. OpenJev arrotonda
le distribuzioni a quattro decimali: l'adapter verifica lo scarto e le
rinormalizza, controllando che etichetta scelta, legenda e score siano coerenti.
La temperatura è una sola per l'intera richiesta, come nell'API upstream.
Il payload interno è limitato a 12.000 caratteri e a `local_max_pairs`
(64 coppie per default); richieste oltre tali limiti si astengono.

La `confidence` Choice/Score di OpenJev è una misura di concentrazione basata
sull'entropia, mentre Boolean usa il massimo fra P(true) e P(false). Non è una
probabilità calibrata di correttezza e non è confrontabile automaticamente con
la confidence Jev Cloud o NLI. Le soglie di esempio possono quindi imporre
`human_review` anche se il wrapper restituisce un candidato. La qualità sul
routing e sulle domande di sicurezza richiede ancora un dataset rappresentativo.

L'integrazione è fissata alla revisione upstream indicata. Il backend OpenJev
non ha un server HTTP nella release verificata: il router importa direttamente
la libreria locale. L'interfaccia REST `/v1/systemone` compare nella sua
roadmap, quindi non viene descritta qui come già disponibile.
