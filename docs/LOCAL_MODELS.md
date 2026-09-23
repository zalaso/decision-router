# Backend locali: verifica e decisione

Ricerca effettuata il **21 settembre 2026**. Scelta per il primo vertical slice: **NLI zero-shot con Transformers**, modello primario `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`; alternativa leggera `MoritzLaurer/xtremedistil-l6-h256-zeroshot-v1.1-all-33`. Si tratta di decision model locali **Jev-like**, non di repliche dei pesi, dell'architettura o della calibrazione di Jev.

## Metodo e limiti della verifica

Sono stati interrogati gli endpoint pubblici GitHub `repos`, `commits`, `git/trees`, `releases`, `tags` e Hugging Face `api/models?blobs=true`; sono stati scaricati e letti sorgenti, licenze, configurazioni e model card. Non ci si è limitati ai README. Dimensioni e disponibilità dei file derivano dai metadati del registry, non dal download integrale di tutti i pesi. Nessun checkpoint degli altri progetti è stato eseguito durante questa ricerca; risultati prestazionali pubblicati dagli autori non equivalgono a misure su questo computer. L'esecuzione dell'adapter di questo repository e i relativi test sono documentati separatamente nel README.

Le revisioni sotto sono quelle osservate, non promesse di stabilità upstream. Nessuna delle cinque repository Jev-like analizzate esponeva una GitHub Release; solo `GPT-AGI/OpenJev` aveva il tag `v0.1.0`. NanoJev pubblica invece il proprio checkpoint con il tag Hugging Face `unified-games-v1`. Fonti verificabili: [API release SemIf](https://api.github.com/repos/TheoLeeCJ/SemIf/releases), [NanoJev](https://api.github.com/repos/TianyuCodings/NanoJev/releases), [LitJev](https://api.github.com/repos/zhengxuyu/litjev/releases), [GPT-AGI](https://api.github.com/repos/GPT-AGI/OpenJev/tags), [razorback16](https://api.github.com/repos/razorback16/openjev/releases).

## Confronto

| Candidato | Natura e licenza | Candidati/probabilità/calibrazione | Hardware, maturità e decisione |
|---|---|---|---|
| **SemIf**, prima chiamato OpenJev, TheoLeeCJ | Wrapper di modelli pubblici, codice MIT; nessun nuovo checkpoint decisionale necessario. Il manifest punta anche a Qwen3.5-4B Apache-2.0. | Legge logits di token lettera e applica softmax sulle opzioni dinamiche. Verifica che i token siano distinti e che il confine del prompt non ne cambi la tokenizzazione. Temperatura post-hoc per workload, distinta dai logits nativi. | Sorgenti, test, fixture e risultati riproducibili sono presenti. Supporti Transformers/MLX/llama.cpp con requisiti diversi. I pesi BF16 Qwen3.5-4B osservati sono circa 9.32 GB, oltre a memoria di esecuzione. Buona alternativa futura per ragionamento istruito; maggiore costo del backbone per questo slice. |
| **OpenJev, GPT-AGI** | Wrapper HF più mock, codice MIT; Qwen2.5-0.5B-Instruct Apache-2.0 come default, pesi pubblici circa 988 MB. | Nel codice `HFBackend` viene confrontato **solo il primo token di ogni label**. Label con lo stesso primo token possono collidere. Candidati runtime; non è un nuovo modello addestrato. Calibrazione nel piano futuro. | CPU/CUDA/MPS; interfaccia piccola ma prototipo, con limitazione concreta nella lettura delle label. Non scelto come default; ora integrato come backend opzionale. |
| **OpenJev, razorback16** | Server compatibile con il wire schema Jev su vLLM/DiffusionGemma; codice e checkpoint NVIDIA dichiarano Apache-2.0. | Probabilità dai logprob dei token etichetta, confidence come concentrazione entropica. Non è prova di calibrazione probabilistica. | Docker usa CUDA 13 e un fork vLLM fissato a commit. Pesi NVFP4 osservati circa 18.82 GB; compatibilità kernel/hardware da verificare. Integrazione più onerosa per un primo slice Windows/CPU. |
| **NanoJev** | **Modello realmente addestrato**: Qwen3-0.6B con teste decisionali, source MIT. Model card mantiene la licenza upstream Qwen ma non espone un campo `license` per l'intero checkpoint. | Testa condivisa, set attention e softmax; supporto dinamico Choice, Boolean e livelli ordinati. Checkpoint e dati sono pubblici. Nessuna evidenza verificata che le probabilità siano calibrate per routing di agenti. | Checkpoint `best.safetensors` circa 2.385 GB. Codice include training e servizio; il percorso documentato usa CUDA/BF16. La release verificata è specializzata su Maze, Snake e ViZDoom: generalizzazione al routing non dimostrata. Non scelto come router iniziale. |
| **LitJev** | Wrapper su Qwen; codice originale Apache-2.0, parti adattate MIT con NOTICE. Non richiede training per il percorso base; esistono anche strumenti opzionali per teste/calibrazione. | Logits ristretti ai candidati, softmax con temperatura; fitter NLL presente nel codice. Choice/Noul/Score esposti via HTTP. Compatibilità schema non implica equivalenza numerica con Jev. | Research preview; default e modello più testato dichiarato Qwen3.8-27B su H100 80 GB. I pesi BF16 corrispondono a circa 55.56 GB. Dipendenze includono Torch 2.11 e Transformers >=5.17. Non scelto per semplicità e hardware. |
| **NLI mDeBERTa**, primario | **Encoder addestrato** su MNLI/XNLI; MIT nella model card; pesi safetensors ungated. | Coppie testo/ipotesi costruite da candidati dinamici. Logits NLI nativi; distribuzione tra candidati derivata. Nessuna calibrazione specifica al routing inclusa. | 278.8M parametri, 557.65 MB su disco FP16; CPU FP32 circa 1.12 GB di soli pesi, più runtime. Transformers standard, nessun server/GPU obbligatorio. Multilingue; migliore corrispondenza al contesto italiano, da misurare. |
| **NLI XtremeDistil**, alternativo | Encoder addestrato per zero-shot, MIT nella model card; safetensors ungated. | Stesso adapter, candidati dinamici; entailment/not-entailment, probabilità da calibrare. | 12.75M parametri, 25.51 MB su disco FP16 e circa 51 MB di soli pesi FP32. Ottimo smoke test CPU e baseline inglese; non una garanzia di qualità italiana. |

Le cifre RAM sono stime aritmetiche dei soli parametri: tokenizer, librerie, attivazioni e batch aumentano il fabbisogno. La latenza cresce con lunghezza e numero dei candidati; non riportiamo valori millisecondo non misurati qui.

## Evidenze di implementazione e revisioni

- **SemIf** `1f2dea3e25379f9dfc98cb83c324f00ab5deda37`: [direct.py](https://github.com/TheoLeeCJ/SemIf/blob/1f2dea3e25379f9dfc98cb83c324f00ab5deda37/src/semif_phase1/direct.py), [manifest modelli](https://github.com/TheoLeeCJ/SemIf/blob/1f2dea3e25379f9dfc98cb83c324f00ab5deda37/manifests/models.json), [calibrazione](https://github.com/TheoLeeCJ/SemIf/blob/1f2dea3e25379f9dfc98cb83c324f00ab5deda37/docs/CALIBRATION.md), [licenza](https://github.com/TheoLeeCJ/SemIf/blob/1f2dea3e25379f9dfc98cb83c324f00ab5deda37/LICENSE). Il modello primario di quel percorso è [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a).
- **GPT-AGI/OpenJev** `0e7bb990211df139da13dc67aa2a7aceca311af7`: [HF backend](https://github.com/GPT-AGI/OpenJev/blob/0e7bb990211df139da13dc67aa2a7aceca311af7/src/openjev/backends/hf.py), [licenza](https://github.com/GPT-AGI/OpenJev/blob/0e7bb990211df139da13dc67aa2a7aceca311af7/LICENSE), [Qwen2.5 checkpoint](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/tree/7ae557604adf67be50417f59c2c2f167def9a775).
- **razorback16/openjev** `e04794ab36e4f7e6040c2547baecdb2737ce2e79`: [engine.py](https://github.com/razorback16/openjev/blob/e04794ab36e4f7e6040c2547baecdb2737ce2e79/openjev/engine.py), [Dockerfile con pin vLLM](https://github.com/razorback16/openjev/blob/e04794ab36e4f7e6040c2547baecdb2737ce2e79/docker/Dockerfile), [licenza](https://github.com/razorback16/openjev/blob/e04794ab36e4f7e6040c2547baecdb2737ce2e79/LICENSE), [checkpoint NVIDIA](https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4/tree/ec4ff3df205028f4e81c954c2227f9312b3ec2ea).
- **NanoJev** `76fdfc9ecdca45a9bcef17991a07d3041a87685a`: [training e architettura](https://github.com/TianyuCodings/NanoJev/blob/76fdfc9ecdca45a9bcef17991a07d3041a87685a/scripts/train_toy_decisions.py), [server](https://github.com/TianyuCodings/NanoJev/blob/76fdfc9ecdca45a9bcef17991a07d3041a87685a/scripts/serve_decisions.py), [licenza codice](https://github.com/TianyuCodings/NanoJev/blob/76fdfc9ecdca45a9bcef17991a07d3041a87685a/LICENSE). [Model card e pesi](https://huggingface.co/C-Tianyu/NanoJev/tree/047b927b30882a1138fc504821b82ac145a4b81a) documentano la release giochi e il formato proprietario `DecisionPredictor`, non `AutoModelForSequenceClassification`.
- **LitJev** `e7fb109a7466da9709028eb9c4e9f16eaeb4e2a3`: [scoring](https://github.com/zhengxuyu/litjev/blob/e7fb109a7466da9709028eb9c4e9f16eaeb4e2a3/src/litjev/scoring.py), [decision engine](https://github.com/zhengxuyu/litjev/blob/e7fb109a7466da9709028eb9c4e9f16eaeb4e2a3/src/litjev/decision.py), [fitter temperatura](https://github.com/zhengxuyu/litjev/blob/e7fb109a7466da9709028eb9c4e9f16eaeb4e2a3/src/litjev/calibration.py), [dipendenze](https://github.com/zhengxuyu/litjev/blob/e7fb109a7466da9709028eb9c4e9f16eaeb4e2a3/pyproject.toml), [licenza](https://github.com/zhengxuyu/litjev/blob/e7fb109a7466da9709028eb9c4e9f16eaeb4e2a3/LICENSE), [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B/tree/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0).

Il nome **OpenJev è ambiguo**: SemIf è stato rinominato, mentre GPT-AGI e razorback16 sono implementazioni indipendenti. Non vengono sommati come release o capacità di un unico progetto. La ricerca non è un censimento esaustivo di tutti i repository omonimi.

## Decisione e semantica NLI

Il backend NLI privilegia compatibilità CPU, pesi realmente disponibili, modelli già addestrati per classificazione aperta e integrazione Python ridotta. Le descrizioni dei candidati diventano ipotesi in linguaggio naturale; i loro identificatori restano chiavi opache. Cambiare candidati non richiede retraining.

L'integrazione usa ipotesi dichiarative (`This request involves: ...` per Choice,
`The level is: ...` per Score). **In questo backend le descrizioni devono essere
autosufficienti**: le istruzioni generiche di Choice/Score non vengono eseguite,
e questo è dichiarato dal warning `choice_score_use_descriptions_not_instructions`.
Jev Cloud invece riceve ed elabora anche le istruzioni. Per Boolean l'ipotesi usa
la proposizione `instructions` e la rubrica positiva; la rubrica negativa non
entra nei logits, limite anch'esso segnalato nei warning.

Il modello è un classificatore NLI, non un instruction follower. Il test reale
su “Write Python code” ha individuato un errore nella concatenazione nuda fra
istruzione e descrizione. Un primo tentativo con la domanda appesa all'ipotesi
ha passato quel test ma ha ottenuto solo 6/16 nel benchmark; il report
`benchmarks/results-local-initial.json` conserva questa evidenza. L'adapter
finale usa il percorso zero-shot basato sulle descrizioni e rubriche nominali.
Rubriche composte, negazioni e formulazione delle opzioni richiedono validazione
specifica. Il campione dimostrativo ha informato l'implementazione: non è un
test set indipendente e non dimostra generalizzazione.

Per Choice e Score, con logit di entailment `e_i`, la distribuzione è `softmax(e_i / T)` sui candidati. Per Boolean, il confronto NLI usa entailment contro contradiction/not-entailment per costruire `P(true)` e `1-P(true)`. Score restituisce l'attesa dell'indice ordinato; non dimostra una comprensione metrica uniforme della scala. Queste trasformazioni seguono il principio del [codice ufficiale zero-shot Transformers 4.57.6](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/pipelines/zero_shot_classification.py).

La distribuzione è **condizionata al set di candidati**: se tutte le opzioni sono sbagliate può comunque concentrarsi su una. Nel Boolean a tre classi il neutral viene escluso dal confronto binario: non interpretarlo come certezza epistemica. `confidence = max(probabilities)` è una convenzione locale trasparente, non la formula di Jev né una stima validata della correttezza. Temperatura `1` significa assenza di calibrazione. Soglie e temperatura devono essere adattate su validation set separato dal test, riportando NLL, Brier, ECE, copertura e rischio dei casi accettati.

## Integrazione opzionale di un progetto Jev-like

Oltre ai due NLI, il router ora ha un adapter per la **libreria reale
GPT-AGI/OpenJev** alla revisione `0e7bb990211df139da13dc67aa2a7aceca311af7`.
Usa il suo `HFBackend` nel processo locale e `Qwen/Qwen2.5-0.5B-Instruct` come
checkpoint opzionale, non un server HTTP o pesi Jev. L'adapter rende gli ID
dei candidati etichette A–Z, rifiuta collisioni dei primi token e convalida
distribuzioni e Score dopo l'arrotondamento upstream. Questo non risolve la
limitazione del primo token come metodo probabilistico e non è una prova di
calibrazione. Le istruzioni per riprodurlo sono in `OPENJEV.md`.

Entrambi i checkpoint hanno una finestra di **512 token** per coppia, special token compresi. Un adapter destinato a decisioni di sicurezza deve rifiutare input troppo lunghi, evitando troncamenti silenziosi. I nomi degli output vanno letti da `label2id`: mDeBERTa usa `entailment=0, neutral=1, contradiction=2`; XtremeDistil `entailment=0, not_entailment=1`. Usare `AutoTokenizer`, `AutoModelForSequenceClassification`, `trust_remote_code=False`, safetensors e revisioni fissate; su CPU preferire FP32.

## Checkpoint riproducibili e lingue

| Ruolo | Model ID | Revisione verificata |
|---|---|---|
| Primario multilingue | `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` | `8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c` |
| Alternativo CPU piccolo, inglese | `MoritzLaurer/xtremedistil-l6-h256-zeroshot-v1.1-all-33` | `c07f66d9cbf781191bee66edfe8ad7856f045781` |

Fonti: [model card mDeBERTa](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli/blob/8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c/README.md), [config mDeBERTa](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli/blob/8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c/config.json), [model card XtremeDistil](https://huggingface.co/MoritzLaurer/xtremedistil-l6-h256-zeroshot-v1.1-all-33/blob/c07f66d9cbf781191bee66edfe8ad7856f045781/README.md), [config XtremeDistil](https://huggingface.co/MoritzLaurer/xtremedistil-l6-h256-zeroshot-v1.1-all-33/blob/c07f66d9cbf781191bee66edfe8ad7856f045781/config.json).

mDeBERTa deriva da pretraining multilingue e NLI su 15 lingue. **L'italiano non compare tra le 15 lingue XNLI valutate nella model card**: il trasferimento all'italiano è una capacità attesa, non un risultato verificato su questo routing. XtremeDistil è una baseline inglese; non deve sostituire silenziosamente il primario su input italiani. Nessuno dei due checkpoint è uno specialista certificato di prompt injection o autorizzazioni.

Un'ulteriore alternativa esaminata è [deberta-v3-base-zeroshot-v2.0-c](https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0-c/tree/bddf8c5411c34ac3565e16e04384fd68b2618dda), MIT e con safetensors pubblici: interessante per workload inglesi e miscela di training dichiarata commercialmente utilizzabile; non prioritario rispetto al multilingue per questo progetto. Le licenze del codice del router non sostituiscono quelle di checkpoint, dataset o componenti upstream.

## Cosa resta da dimostrare

Non sono dimostrati equivalenza con Jev, calibrazione fuori distribuzione, accuratezza su routing italiano, robustezza avversaria o latenza di produzione. Il benchmark minimo del repository permette di iniziare a misurarli ma un dataset dimostrativo non basta a fissare soglie operative. Registrare revisioni, CPU/GPU, precisione, candidati, lunghezza, cold start separato dal warm path e timeout. Il runtime attuale usa un processo figlio terminabile su timeout/cancellazione e limita la concorrenza locale a un'inferenza; lo scorer diretto resta un'utility sincrona per test e libreria.
