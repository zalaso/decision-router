# Sicurezza e limiti del primo slice

**Routing e autorizzazione restano separati.** Il progetto suggerisce un agente;
non esegue tool, non tocca filesystem su richiesta HTTP e non avvia agenti.
Le risposte contengono sempre `granted_capabilities: []`. `authorize()` usa solo
grant fidati forniti nel costruttore della policy, mai campi della richiesta
o del modello; le operazioni irreversibili richiedono un controllo esterno
ulteriore e vengono sempre negate da questo esempio.

La policy esamina injection, intento malevolo e rischio separatamente dal
routing. Le soglie possono forzare `human_review` anche quando l'agente ha
confidence elevata. Evidenze mancanti, malformate o incerte si chiudono in
revisione; nessuna indisponibilità del modello equivale a "sicuro".
In auto le evidenze positive di rischio già osservate restano vincolanti anche
dopo il fallback. Il secondario shadow non può influenzare la policy effettiva.
Questi classificatori non rilevano tutti gli attacchi: le autorizzazioni
devono rimanere efficaci anche se la classificazione è errata al 100%.

## Dati e credenziali

- Chiave TypeSafe solo da `TYPESAFE_API_KEY`; SecretStr impedisce la stampa
  involontaria nella configurazione. YAML con credenziali viene rifiutato.
- Endpoint cloud fissato a `https://api.typesafe.ai/v1/systemone`, TLS verificato,
  redirect disattivati, proxy ambiente disabilitati per evitare inoltri inattesi.
- Endpoint Rizzo Flow limitato a HTTP su IP numerico di loopback con porta
  esplicita. Redirect e proxy ambiente sono disabilitati. L'eventuale
  `RIZZO_API_KEY` viene letta soltanto dall'ambiente e non è la chiave TypeSafe.
  La connessione HTTP locale non cifra i dati: non inoltrarla fuori macchina.
- Modalità `auto` e `shadow` possono inviare prompt e contesto al cloud: scegliere
  `local` per dati che non possono uscire dal computer. Non esiste una redazione
  automatica dei contenuti prima dell'inferenza.
- Logger dedicato con allowlist di numeri, enum e indici: niente prompt,
  contesto, rubriche, ID arbitrari, chiavi, URL, body di errore o stack trace.
  I nomi dei candidati vengono sostituiti da indici ordinati nel solo audit log;
  la risposta API conserva i nomi richiesti dal chiamante.
- Errori HTTP di validazione non riflettono l'input. Gli errori dei provider
  sono enum sanitizzati. Non attivare log HTTP/Torch di debug su dati sensibili.

## Endpoint e carico

Avvio predefinito su `127.0.0.1`. Token Bearer opzionale via `ROUTER_API_TOKEN`
con confronto a tempo costante. Il body è limitato a 256 KiB anche con chunked
transfer; lunghezze, numero di domande e candidati sono limitati dal dominio.
Il locale rifiuta più di 64 coppie NLI o oltre 512 token/coppia senza troncare.
Il worker NLI/OpenJev vive in un processo separato, con una sola inferenza alla volta:
su timeout/cancellazione viene terminato. Modelli scaricati solo su scelta
esplicita, revisioni fissate, safetensors, nessun codice remoto. La middleware
limita richieste e concorrenza **per processo server**, inclusi i tentativi
di autenticazione falliti. `ROUTER_REQUIRE_HTTPS=true` rifiuta le richieste
HTTP POST `/v1/` e richiede un token configurato, ma non termina TLS: usarlo
solo dietro un
reverse proxy fidato che imposti correttamente lo schema ASGI.

Mancano quote per identità e coordinate fra più processi, terminazione TLS,
gestione identità, cifratura dei log e policy di retention. Prima di esporre il
servizio fuori loopback servono questi controlli nell'infrastruttura fidata,
oltre alla configurazione del proxy e a una verifica operativa dell'ambiente.
Una richiesta lenta a livello HTTP richiede timeout del proxy/server esterno.

## Calibrazione

Confidence non è correttezza. Le soglie incluse sono esempi. NLI può essere
molto confidente su un insieme di candidati tutti inadatti; neutral viene
escluso nel Boolean. La temperatura è un parametro, non un modello calibrato.
`docs/CALIBRATION.md` descrive export, split e report offline.
Separare training/calibration/test, misurare rischio residuo sui casi accettati,
valutare lingue, distribuzioni fuori dominio e prompt injection avversaria.
Il fake non va impiegato per classificare sicurezza in un sistema reale.
Il wrapper OpenJev legge solo il primo token per etichetta; l'adapter rifiuta
collisioni fra etichette, ma ciò non valida la qualità delle probabilità o la
resistenza a prompt injection. Vedere `OPENJEV.md`.
Rizzo Flow usa un modello e una statistica di confidence diversi da Jev:
anche con formato HTTP compatibile, soglie e qualità richiedono una
validazione specifica. L'endpoint compatibile non include l'astensione nativa
di Rizzo Flow; la policy del router deve restare attiva. Vedere
`RIZZO_FLOW.md`.
