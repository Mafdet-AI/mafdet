<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/mafdet-logo-dark.svg">
  <img src="assets/mafdet-logo.svg" alt="" width="112">
</picture>

<h1>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/mafdet-wordmark-dark.svg">
  <img src="assets/mafdet-wordmark.svg" alt="MAFDET" height="30">
</picture>
</h1>

### You type. It already knows.

MAFDET legge il tuo messaggio mentre lo scrivi, e **comincia a ragionarci prima che tu lo invii**.<br>
Premi Invio, e gran parte del lavoro è già fatta.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-EEEAE2?labelColor=0E0D0C)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/mafdet?label=pypi&color=EEEAE2&labelColor=0E0D0C)](https://pypi.org/project/mafdet/)
[![Runtime: local](https://img.shields.io/badge/runtime-local-D4892A?labelColor=0E0D0C)](#requisiti)
[![Status: prototype](https://img.shields.io/badge/status-prototype-6A655D?labelColor=0E0D0C)](#roadmap)

[English](README.md)

</div>

---

## Perché

Le interfacce di chat fanno aspettare il modello finché non premi Invio, e solo allora si mette al lavoro. Mentre scrivi, la GPU resta ferma. Quando invii, il modello deve prima *leggere* tutto il messaggio (prefill). Poi un modello che ragiona, come Qwen3 in modalità ragionamento, *pensa* per centinaia di token prima della prima parola della risposta. Su una GPU di fascia media quel ragionamento è l'attesa più lunga di tutte: dieci secondi e più su un problema, minuti con un modello più grande della VRAM.

MAFDET usa per entrambe le cose il tempo in cui scrivi. **Legge** il messaggio mentre lo scrivi, così all'Invio il prefill è già fatto. E **ragiona in anticipo**: il modello comincia a pensare al tuo messaggio mentre lo stai ancora scrivendo, e conserva quel ragionamento quando cambi il testo. Su Qwen3-4B, con 24 problemi digitati in tempo reale, l'attesa media dopo l'Invio passa da **12,2 s a 3,2 s**, con le stesse 24 risposte corrette su 24; su Qwen3-14B da **108 s a 47 s**. Su un MacBook Pro M2 Pro da **18,1 s a 5,6 s** (4B, 24/24 in entrambi i casi) e da **32,7 s a 12,1 s** (14B).

MAFDET è pensato per l'hardware che la maggior parte delle persone possiede davvero. La macchina di riferimento è una **RTX 2060 con 6 GB di VRAM**; funziona anche sui **Mac con Apple Silicon** (Metal, misurato su un M2 Pro con 16 GB), e tutto funziona anche **solo su CPU**. Le schede **AMD Radeon** girano con Vulkan, finora misurate su una scheda ([primi numeri](#su-una-amd-radeon), ne servono altre: [cercasi aiuto](#cercasi-misure-su-una-amd-radeon)).

> Il nome viene da Mafdet, la dea felina dell'antico Egitto detta "la corritrice", che colpiva serpenti e scorpioni prima che potessero attaccare.

## Come funziona

### Lettura in anticipo

Un LLM elabora il testo in due fasi:

| Fase | Cosa succede | Velocità |
|---|---|---|
| **Prefill** | legge il prompt; tutti i token vengono elaborati in parallelo | veloce |
| **Decode** | genera la risposta, un token alla volta | lenta |

Mentre legge, il modello salva risultati intermedi per ogni token nella **KV cache**. Per un dato prefisso quei risultati non cambiano mai, quindi si possono calcolare in anticipo.

MAFDET tiene la KV cache allineata al testo **mentre scrivi**:

1. A ogni pausa, tokenizza il testo attuale.
2. Trattiene gli ultimi token, perché una parola che stai ancora scrivendo può cambiare il modo in cui il testo viene diviso in token.
3. Trova il **prefisso comune più lungo** tra i token nuovi e quelli già in cache, taglia la cache in quel punto e fa il prefill solo di ciò che è nuovo.
4. Quando premi invio, restano da elaborare solo pochi token.

**La correttezza non dipende da come scrivi.** Backspace, modifiche a metà testo, blocchi incollati, punteggiatura o spazi mancanti: il controllo sul prefisso comune riporta sempre la cache esattamente a ciò che produrrebbe un calcolo da zero. Le euristiche come l'holdback e il debounce influiscono solo su quanto lavoro si spreca, mai sul risultato.

### Ragionamento in anticipo

La lettura in anticipo toglie il prefill dall'attesa; il ragionamento però resta. Così il modello **ragiona anche mentre scrivi**: a blocchi brevi, sul messaggio così com'è in quel momento. Quando cambi il testo, il ragionamento non viene buttato. Resta nel prompt come appunti dopo la nuova versione del messaggio, il che costa solo un prefill, e il modello continua da lì. All'Invio finisce di ragionare da dove era arrivato e risponde. Su Qwen3-4B circa due terzi del ragionamento sono fatti prima dell'Invio. Come sono impostati gli appunti, e cosa ha cambiato, è in [Ragionamento in anticipo, nel dettaglio](#ragionamento-in-anticipo-nel-dettaglio) più sotto.

La lettura in anticipo è esatta. Il ragionamento in anticipo no, perché il modello ragiona su versioni del messaggio non ancora definitive: per questo è stato misurato su risposte verificabili.

## Avvio rapido

**Su Windows, con un doppio clic.** Scarica MAFDET (su GitHub, *Code → Download ZIP*, ed estrai lo zip; oppure `git clone https://github.com/Mafdet-AI/mafdet.git`) e fai doppio clic su **`start_mafdet.bat`**. La prima volta crea un ambiente Python in `.venv` e ci installa MAFDET: ci vuole circa un minuto, e serve [Python](https://www.python.org/downloads/) 3.10 o successivo. Dopo, un doppio clic apre MAFDET in meno di un secondo, e dopo un aggiornamento installa da solo le librerie nuove. Il terminale mostra man mano cosa sta scaricando: ogni libreria, e poi llama.cpp e i modelli che scegli nell'app, con dimensione, barra di avanzamento, velocità e tempo rimanente.

**Su macOS, con un doppio clic.** Lo stesso, con **`start_mafdet.command`** (serve Python 3.10 o successivo: da python.org, oppure `brew install python@3.12`). Se il Finder dice che non può verificare il file, clic destro, *Apri*, poi di nuovo *Apri*: serve solo la prima volta.

**Da [PyPI](https://pypi.org/project/mafdet/)** (Python 3.10 o successivo). Su macOS con il Python di Homebrew, e su Ubuntu e Debian recenti, `pip install` fuori da un ambiente virtuale si ferma con *externally-managed-environment*: installa con [pipx](https://pipx.pypa.io), che dà a MAFDET un ambiente tutto suo e mette `mafdet` nel PATH.

```bash
brew install pipx             # Ubuntu/Debian: sudo apt install pipx · Windows: py -m pip install --user pipx
pipx ensurepath               # poi apri un nuovo terminale
pipx install mafdet
mafdet
```

Su Windows con il Python di python.org, `pip install mafdet` funziona così com'è. Per aggiornare: `pipx upgrade mafdet` (oppure `pip install -U mafdet`).

**Dai sorgenti, da terminale** (serve anche per le misure in `research/`):

```bash
git clone https://github.com/Mafdet-AI/mafdet.git
cd mafdet
python -m venv .venv          # Windows: py -3.12 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e .
mafdet
```

In entrambi i casi niente PyTorch e niente download a mano: MAFDET chiede se aprire la **finestra** o l'**app nel terminale**, e al primo avvio una procedura guidata fa il resto. Guarda il tuo computer, installa da sola la build giusta di llama.cpp (CUDA per una scheda NVIDIA, Vulkan per una AMD Radeon, Metal su un Mac con Apple Silicon, CPU altrimenti) e scarica il modello che rende di più sul tuo hardware.

<p align="center">
  <img src="assets/screenshots/gui-chat.png" alt="La finestra di MAFDET: il modello ha ragionato mentre il messaggio veniva scritto (455 token di ragionamento su 571) e ha risposto 1,6 s dopo l'invio" width="49%">
  <img src="assets/screenshots/gui-models.png" alt="Il catalogo dei modelli: per ogni modello, dove gira su questo computer, la velocità e l'attesa dopo l'invio con e senza MAFDET" width="49%">
</p>

## L'app

La stessa app esiste in due forme, sullo stesso motore:

| | Comando | Cos'è |
|---|---|---|
| <img src="assets/icons/watch.svg" width="22" alt=""> | `mafdet gui` | **La finestra**: chat, modelli, hardware e impostazioni, in italiano, inglese, francese o tedesco |
| <img src="assets/icons/strike.svg" width="22" alt=""> | `mafdet tui` | **L'app nel terminale**: le stesse viste nel terminale, pensate per la tastiera (da F1 a F4), su uno schermo a fosfori ambra con una sequenza di avvio (Impostazioni, *Cinematic effects*, spegne le animazioni) |
| <img src="assets/icons/race.svg" width="22" alt=""> | `mafdet models` | il catalogo dei modelli per questo computer (`--family`, `--download`, anche `--download repository:file.gguf` da Hugging Face; `--delete` toglie dal disco un modello scaricato) |
| <img src="assets/icons/awaken.svg" width="22" alt=""> | `mafdet doctor` | controlla GPU, memoria, llama.cpp e modelli; `--install` installa llama.cpp (`--build 12`, `13`, `vulkan` o `cpu` per scegliere la build) |

`mafdet proxy` porta la lettura in anticipo in Open WebUI, e `mafdet agent` dà a un modello locale degli strumenti con cui lavorare (tutti e due più sotto). `mafdet` da solo apre il launcher, che può ricordare la scelta (`mafdet --ask` lo mostra di nuovo).

**Chat.** Mentre scrivi, una riga di stato sotto il messaggio mostra cosa sta facendo il modello: quanto ha già letto e quanti token di ragionamento ha scritto in anticipo (il campo si illumina di ambra mentre ragiona). All'invio resta da calcolare solo la coda. Il ragionamento è mostrato chiuso sopra la risposta, e ogni risposta finisce con i suoi numeri: prima parola dopo l'invio, token letti mentre scrivevi, ragionamento fatto prima dell'invio, velocità. Quando il modello scrive la risposta in anticipo durante una pausa, compare sotto il messaggio man mano che viene scritta, in trasparenza, prima che tu invii (Impostazioni, *Mostra la risposta mentre viene scritta in anticipo*): all'invio passa subito nella conversazione, e ogni modifica al messaggio la fa cadere, perché una risposta vale solo per il testo esatto per cui è stata scritta. Nel ragionamento un segno mostra dove è caduto l'invio: tutto quello che sta sopra è stato pensato mentre scrivevi. *Budget di ragionamento dopo l'Invio* (Impostazioni, `--think-budget`) limita il ragionamento dopo l'invio: oltre, il ragionamento viene chiuso e il modello risponde con quello che ha, invece di pensare finché il limite di token lo taglia senza risposta.

**Modelli.** Cinque famiglie: **Qwen3**, **Llama 3**, **Gemma 3**, **Mistral** e **Phi-4**, da 0,6 a 32 miliardi di parametri, ognuna in più quantizzazioni. Con ogni modello MAFDET legge il messaggio mentre scrivi e, a una pausa, scrive la risposta in anticipo; Qwen3 in più **ragiona in anticipo**, l'unica famiglia di cui è stata misurata la cornice di ragionamento. Ogni modello del catalogo dice cosa fa sul *tuo* computer, prima di scaricarlo: se sta nella VRAM o gira in parte sulla CPU, quanto veloce scrive e legge e, per Qwen3, dopo quanto arriva la risposta dopo l'invio con e senza MAFDET (per le altre famiglie, quanto ci vuole a scrivere una risposta tipica). La stima viene dal tuo hardware (banda di GPU e RAM, VRAM, ripartizione dei layer di llama.cpp tra GPU e CPU) e sta entro il 12% delle velocità misurate sul PC di riferimento (Qwen3-4B 89,6 stimati / 87,1 misurati tok/s, 8B 17,0 / 16,2, 14B 5,0 / 4,9), su una RTX 4070 SUPER (4B 134 / 130, 14B 39 / 41, tutto sulla GPU) e su un M2 Pro (Qwen3-4B 53 / 57 tok/s). Dove Qwen3-14B non sta tutto nella VRAM, al posto del Q4_K_M viene proposto il suo IQ4_XS: stesse risposte, con più modello sulla GPU (misure in `research/LOG.md`). Una volta scaricato un modello, **Misura** lo prova in mezzo minuto e i numeri veri sostituiscono la stima. Il modello che rende di più è segnato come *Il migliore per te*, e ogni famiglia ha il suo migliore. **Elimina** toglie dal disco un modello scaricato, dopo una domanda sulla sua scheda (nell'app nel terminale con Canc, da riga di comando con `mafdet models --delete <id>`); la vista dice quanto occupano i modelli e quanto spazio è libero sul disco, e un download che non ci starebbe viene rifiutato prima di partire. L'attesa dopo l'invio è calcolata su quanto ci metti a scrivere un messaggio: 34 s all'inizio, poi la mediana dei tuoi messaggi dopo che ne hai inviati alcuni. Per aggiungere un modello basta una riga in `tools/build_catalog.py`: legge da Hugging Face le dimensioni dei file e la forma del modello.

**I tuoi modelli.** Funziona anche qualsiasi GGUF fuori dal catalogo:

- **Cerca su Hugging Face** (vista Modelli) trova i repository GGUF, prima i più scaricati, e ne elenca i file con la stessa stima per il tuo computer; una quantizzazione è consigliata (Q4_K_M quando c'è). Il download riprende da dove si era fermato, con tutte le parti di un modello diviso, in `~/.mafdet/models/hf/<owner>/<repo>/`. I modelli ad accesso riservato (Llama, Gemma) richiedono la licenza accettata su huggingface.co e `hf auth login` sul computer.
- **Un file scaricato a mano**: mettilo in `~/.mafdet/models` (anche in una sottocartella), oppure sceglilo ovunque sul disco con **Aggiungi un file GGUF** (resta dov'è).

Compaiono sotto *I tuoi modelli*, con i numeri letti dall'header del file (layer, teste KV, parametri, esperti attivi) e **Misura** come per il catalogo. Non serve scaricare altro: tokenizer e chat template sono quelli del GGUF, letti tramite llama-server (identici al tokenizer di Hugging Face su Qwen3-4B: id, token speciali, testo decodificato, template). Un Qwen3 con la modalità thinking ragiona in anticipo mentre scrivi; ogni altro modello legge mentre scrivi e scrive la risposta in anticipo alle pause. L'architettura deve essere supportata dal llama.cpp installato.

**Hardware e impostazioni.** La vista Hardware mostra GPU, CPU, memoria e il runtime llama.cpp, con un pulsante per installarlo o reinstallarlo. Le impostazioni della chat restano da un avvio all'altro (in `~/.mafdet`, oppure `$MAFDET_HOME`).

<p align="center">
  <img src="assets/screenshots/tui-models.png" alt="L'app nel terminale: il catalogo dei modelli come tabella" width="49%">
  <img src="assets/screenshots/launcher.png" alt="Il launcher: finestra o terminale" width="49%">
</p>

## In Open WebUI: `mafdet proxy`

La stessa lettura in anticipo, e il ragionamento in anticipo con `--think-ahead`, nella chat che usi già. `mafdet proxy` avvia llama-server come serve alla lettura in anticipo e, davanti, un proxy compatibile OpenAI su `http://127.0.0.1:8077/v1`; un piccolo script fa mandare a Open WebUI quello che scrivi.

```bash
mafdet proxy --model qwen3-4b                  # lettura in anticipo: il prompt a cui il modello risponde è lo stesso
mafdet proxy --model qwen3-4b --think-ahead    # e il modello ragiona mentre scrivi (Qwen3, non esatto)
```

Open WebUI 0.13 su una RTX 2060, Qwen3-4B, 8 conversazioni di 2-3 messaggi per lato, scritte (`research/proxy_openwebui.py`):

| Invio → | media | mediana | peggiore |
|---|---|---|---|
| prima parola, ragionamento spento: llama-server con le opzioni di default | 5,4 s | 3,3 s | 20,7 s |
| prima parola, ragionamento spento: attraverso `mafdet proxy` | **0,16 s** | **0,14 s** | 0,4 s |
| prima parola della risposta, ragionamento acceso: `mafdet proxy` | 7,6 s | 6,3 s | 21,0 s |
| prima parola della risposta, ragionamento acceso: `mafdet proxy --think-ahead` | **4,3 s** | **4,1 s** | 10,4 s |

Senza, il tuo messaggio aspetta in coda dietro il titolo, i tag e i suggerimenti che Open WebUI chiede al modello, e i suoi 30 strumenti integrati (~5,8k token) vengono riletti; il proxy dà a quelle richieste uno slot tutto loro e legge il messaggio mentre scrivi. Come configurarlo, passo per passo: [Open WebUI + MAFDET in 5 minuti](mafdet/proxy/clients/README.md), oppure tutto in Docker: [`docker/`](docker/README.md) (in inglese). `http://127.0.0.1:8077/proxy` dice se funziona. Anche altre interfacce possono mandare le bozze (`mafdet/proxy/README.md`); cosa viene dopo, e come aiutare: [PROXY.md](PROXY.md) (in inglese).

## Un agente con gli strumenti: `mafdet agent` (sperimentale)

Un modello locale che lavora sul tuo computer attraverso degli strumenti, in un ciclo, finché il compito è finito: legge e scrive file, lancia comandi e usa gli strumenti dei server [MCP](https://modelcontextprotocol.io). Nasce prima di tutto per il **reverse engineering e lo studio della sicurezza**, dove il binario non deve uscire dal computer e il modello lo scegli tu: un decompilatore o un debugger che parla MCP dà al modello i suoi strumenti, e qualsiasi GGUF con un chat template che prevede le chiamate a strumenti li può usare (cerca su Hugging Face nell'app, oppure `--model repository:file`).

```bash
mafdet agent                                         # una conversazione, in questa cartella, con il modello dell'app
mafdet agent --model qwen3-14b --workdir C:\samples\crackme
mafdet agent --mcp rea --profile native-static       # con gli strumenti di un server MCP
mafdet agent -p "cosa accetta check.py?"             # un compito, poi esce
mafdet agent --resume last
mafdet agent --model qwen3-14b --mcp rea --profile native-static --web --save   # e ricorda queste opzioni
mafdet agent --walk C:\samples\crackme.exe --mcp rea --max-functions 50         # dà un nome alle sue funzioni, dalle foglie
mafdet agent --walk C:\samples\crackme.exe --mcp pyghidra --refine              # poi rilegge la cima, con chi chiama
mafdet agent --mcp rea --book C:\samples\crackme.exe                            # con quello che ha trovato il walk
```

È pensato per una scheda da 12-16 GB (il riferimento è una RTX 5070 Ti, dove Qwen3-14B sta tutto sulla GPU); su 6 GB solo un modello 4B è abbastanza veloce per un ciclo. Il contesto è il più grande tra 32k, 24k e 16k token che lascia il modello tutto sulla GPU, con la cache KV che avrà (a 8 bit quando a 16 non starebbe), e lascia ancora libero 1 GB della memoria video per il desktop e le altre app; all'avvio dice cosa ha scelto e perché, e `--ctx` sceglie al posto suo. Su una RTX 4070 SUPER da 12 GB, Qwen3-14B IQ4_XS ha 16k: a 24k il modello ci starebbe, con 0,4 GB liberi, ma appena un'altra app ha preso memoria video Windows ne ha spostato una parte fuori, e scriveva a 5-27 token al secondo invece di 40-46.

`mafdet agent` apre una conversazione che riempie il terminale, con il modello al lavoro dentro. Scrivi in basso; sopra, ogni chiamata a uno strumento con l'inizio del risultato, le modifiche come diff, la risposta in Markdown. Mentre il modello ragiona i suoi pensieri non si vedono: un gatto sbatte gli occhi accanto a quello che farebbe un gatto (`=^.^= Making biscuits…`, con i secondi e i token fin lì), e il ragionamento resta chiuso in una riga. Una chiamata che richiede la tua conferma chiede sul posto, mostrando cosa lancerebbe o cambierebbe. Quando il modello si dà una lista di cose da fare, la lista compare sopra la casella del messaggio, con i passi fatti e quello a cui è arrivato.

| Tasto | |
|---|---|
| Invio | invia; mentre il modello lavora il messaggio aspetta e parte quando ha finito |
| Maiusc+Invio (o Ctrl+J) | una nuova riga |
| Esc | ferma il modello (anche un comando in corso), o risponde no a una domanda |
| `y` / `a` / `n` | a una domanda: sì, sempre per questo strumento, no |
| Maiusc+Tab | cosa parte senza chiedere: ask → edits → all |
| Ctrl+O | ragionamento e output degli strumenti per intero, o ridotti |
| Su / Giù | i tuoi messaggi precedenti |
| `/help` `/tools` `/notes` `/compact` `/new` `/approve` `/quit` | `/notes` mostra cosa si è annotato il modello, `/compact` riassume la conversazione per fare spazio |

Un `MAFDET.md` (o `AGENTS.md`) nella cartella di lavoro viene letto all'avvio come istruzioni per quella cartella: cos'è il campione, cosa è stato trovato, come si compila. `--plain` dà la stessa conversazione come righe semplici, e `-p` o una pipe un solo compito senza interfaccia. `--resume` (l'id di una sessione, o `last`) riprende una sessione di `~/.mafdet/sessions`: la conversazione a schermo intero mostra di nuovo i suoi ultimi 60 messaggi, le note vengono rilette, e una sessione che era stata compattata riparte dal suo riassunto.

`--save` conserva il modello, la modalità di conferma, i server MCP, il profilo, gli strumenti e il web (`--web`) in `~/.mafdet/agent.json`: da lì in poi `mafdet agent` da solo parte con quelli. Quello che dice la riga di comando vale più del file (`--no-web` per un avvio senza il web).

Cosa fa per un modello con un contesto piccolo e nessuno che lo controlli:

- **Pochi strumenti, schemi corti.** Otto suoi (`read_file`, che mostra un binario come dump esadecimale, `write_file`, `edit_file`, `search`, `shell`, `job`, `note`, `todo`): 891 token di schemi sul vocabolario di Qwen3, e la prima richiesta intera, prompt di sistema compreso, 1.305. I server MCP si scrivono in `~/.mafdet/mcp.json`, nella forma che usano gli altri client. Un server di reverse engineering può elencare centinaia di strumenti (uno provato qui: 207, 3,4 MB di schemi): il modello parte con pochi (`--profile native-static`: 16 per l'analisi statica di un binario nativo; `--tools`, o `tools` nel file) e gli altri li trova e li accende con `find_tools`. Le descrizioni sono tagliate alla fine di una frase (quella di uno strumento a 160 caratteri, quella di un parametro obbligatorio a 80, di uno facoltativo a 48), i controlli che il server fa comunque (limiti, pattern) vengono tolti, e un parametro facoltativo descritto con le stesse parole in uno strumento precedente non viene descritto di nuovo; uno strumento che supera ancora i 450 caratteri (circa 100 token) viene accorciato un livello alla volta, i parametri facoltativi ridotti a nome e tipo, le descrizioni più corte. Ogni parametro tiene nome e tipo, e quelli obbligatori restano tali. I 16 strumenti del profilo e `find_tools` prendono 1.696 token (con gli schemi solo ripuliti ne prendevano 3,8k); con le istruzioni del server, la prima richiesta cresce di 1.812.
- **Una shell che resta aperta.** `shell` lavora in una sola PowerShell (sh fuori da Windows) che resta aperta per tutta la sessione, così un `cd`, una variabile o un ambiente attivato ci sono ancora per il comando dopo; un risultato dice il codice di uscita quando non è 0, e la cartella quando è cambiata. Un comando che supera il suo timeout (60 s, fino a 600 se il modello lo chiede) o che fermi con Esc viene chiuso insieme a quello che ha avviato, e il successivo parte in una shell nuova, nella cartella di lavoro, e lo dice. Quello che deve restare acceso (un server, un debugger) gira come job in background (`shell` con `background`): il suo output va in un file, e `job` elenca i job, legge la fine dell'output di uno, o lo ferma. I job finiscono con l'agente.
- **Output tagliati su misura.** L'output di uno strumento è ridotto a testa e coda (`--tool-chars`, 4.000) e conservato intero in `~/.mafdet/sessions/<id>/outputs`, dove il modello può leggere la parte che gli serve. Quando il contesto si riempie, gli output più vecchi vengono tolti in una volta sola; quando non basta, la conversazione viene compattata: il modello ne scrive un riassunto in quattro parti (il compito, cosa è accertato, cosa è stato fatto, cosa manca; senza strumenti e senza ragionamento), e al suo posto restano il riassunto, il tuo ultimo messaggio, le note, la lista delle cose da fare e gli ultimi due passi del modello, con gli output lunghi sostituiti da una riga che dice quale file li contiene (il più recente tiene il suo inizio). Se il risultato non sarebbe più piccolo, non cambia niente e l'errore dice cosa non ci sta. `/compact` lo fa quando lo chiedi tu. Il riassunto è del modello e può perdere qualcosa: quello che non va perso va in una nota.
- **Note e una lista delle cose da fare.** Con `note` il modello si annota una scoperta (un indirizzo, un nome, un valore, una decisione): le note non vengono mai tolte, e sono conservate in `~/.mafdet/sessions/<id>/notes.md`, dove puoi leggerle anche tu. Con `todo` fissa i passi di un compito e li segna come fatti man mano; un modello piccolo perde meno il filo di un compito lungo se ha la lista davanti. La lista è conservata in `todo.md` accanto alle note, e `--resume` la ritrova com'era.
- **Il web, solo se lo chiedi.** `--web` dà al modello `web_search` e `web_fetch`, che allora partono senza chiedere, come una lettura. È spento di default: una ricerca o un indirizzo sono qualcosa che esce dal computer. La ricerca legge la pagina HTML di DuckDuckGo (nessuna chiave, ma non è un'API: può cambiare), o un tuo SearXNG, indicato come `"search_url": "http://.../search?q={query}&format=json"` in `~/.mafdet/agent.json`; una pagina torna come testo, tagliata e conservata su disco come ogni output.
- **Prima chiede.** Leggere dentro la cartella di lavoro parte da solo; scrivere, lanciare comandi e gli strumenti MCP non marcati di sola lettura chiedono (`--approve edits` lo lascia scrivere nella cartella, `--approve all` anche lanciare comandi: per una VM o una cartella da buttare). Una chiamata che esce dalla cartella chiede sempre. In `mcp.json`, `"allow": ["list_procedures", "procedure_pseudo_code", "xrefs"]` elenca gli strumenti di un server di cui ti fidi, che partono senza chiedere (i nomi esatti sono più sicuri dei pattern: in un server `list_*` prende anche i suoi strumenti per Android e Frida). Al modello viene detto che quello che contengono i risultati degli strumenti e i file sono dati da analizzare, mai istruzioni da seguire. Analizzare un binario è leggere; eseguirlo no: quello va fatto in una VM.
- **I suoi errori gli vengono detti**: argomenti che non sono JSON, uno strumento che non esiste, uno strumento ancora spento (con come accenderlo), una chiamata che hai rifiutato, uno strumento fallito.

**Dare un nome alle funzioni di un binario: `--walk`.** Con il server MCP di un decompilatore, `mafdet agent --walk <binario> --mcp <server>` dà un nome e un riassunto a ogni funzione di un binario, senza nessuno che segua, dalle foglie del grafo delle chiamate in su. Il walk lo guida il codice, non il modello: elenca le funzioni, le ordina mettendo prima quelle chiamate, e dà al modello una funzione alla volta, in una breve conversazione tutta sua: lo pseudo-codice, con le funzioni che chiama già col loro nuovo nome, l'elenco di queste con una riga su cosa fa ciascuna, e un solo strumento con cui rispondere (un nome, un riassunto di una riga, un commento, quanto è sicuro). Così un contesto piccolo non contiene mai più di una funzione (il walk usa 16k, se `--ctx` non dice altro), e l'inizio del prompt è sempre lo stesso, tenuto nella cache di llama-server. Una funzione che ha già un nome vero (un import, un simbolo) resta com'è; una risposta che non è un nome utilizzabile viene detta al modello una volta, poi la funzione è segnata come fallita e il walk va avanti. Quello che trova va nel libro del binario, di MAFDET: `~/.mafdet/binaries/<le prime 16 cifre del suo sha256>/book.json`, e `book.md` da leggere, dal punto d'ingresso in giù; lo stesso comando di nuovo riprende da dove si era fermato, anche dopo un crash (`--max-functions`, `--time-limit` in minuti, `--retry-failed`). Con `--refine`, quando ogni funzione ha un nome, un secondo passaggio rilegge, dalla cima dell'albero in giù, quelle che ne chiamano altre, ora con chi le chiama e le righe in cui le chiama, e tiene o cambia ogni nome (il primo passaggio non vede mai chi chiama una funzione, e un'etichetta sbagliata in basso nell'albero sale verso l'alto); poi scrive un paragrafo sull'intero programma in cima a `book.md` (fatto, non ancora misurato su un binario vero). Nomi e commenti vengono scritti anche nel decompilatore dove il suo server lo permette e la modalità di conferma lo consente (`--approve`, o `allow` in `mcp.json`): un walk non chiede a nessuno. In Ghidra arrivano attraverso [pyghidra-mcp](https://github.com/clearbluejar/pyghidra-mcp), che tiene su disco un suo progetto Ghidra (il walk lo salva ogni 25 funzioni e alla fine); rea su Windows legge un binario ma non rinomina né commenta. `--book <binario>` dà a una sessione normale uno strumento `book`, di sola lettura, con quello che ha trovato il walk. I server con cui sa parlare sono una tabella per server (rea, pyghidra-mcp), scelta dagli strumenti che il server elenca.

A che punto è: il ciclo e il client MCP funzionano. Su una RTX 4070 SUPER con Qwen3-14B le prime sessioni vere hanno fatto circa 60 chiamate a strumenti, nessuna malformata: leggere un file e spiegarlo ha preso 2 passi (24 s), correggere un bug con `--approve edits` 5 passi (81 s), e una conversazione compattata in un contesto di 4k e di 6k è andata avanti e ha finito bene. Il primo walk vero, sul where.exe di Windows e sulla stessa scheda: 86 funzioni su 86 con un nome in circa 25 minuti (17 s a funzione), nessuna fallita, attraverso rea e di nuovo attraverso pyghidra-mcp, che ha scritto tutti gli 86 nomi e commenti nel progetto Ghidra. Letti a mano su 23 di loro, 10 nomi erano giusti, 8 vaghi e 5 sbagliati: le foglie hanno avuto nomi buoni, la cima dell'albero delle chiamate no (main è diventato `validate_config_and_initialize`). Un solo binario piccolo e il giudizio di un solo lettore, non una misura. L'interfaccia a schermo intero, il modello che lancia comandi, i job in background e il web sono provati solo con un modello finto (il web su un sito di prova locale), non ancora con uno vero. Poi: un secondo passaggio sulla cima dell'albero quando le funzioni che chiama hanno già un nome, e prima i simboli dove il binario li ha; lettura e ragionamento in anticipo mentre gira uno strumento e sulla funzione sotto il cursore dell'analista; un benchmark su crackme con risposta nota, e su binari con i simboli, tolti, analizzati dal walk e confrontati con i loro nomi veri. Il piano è in `research/LOG.md`.

## Requisiti

- **Windows**, con una **GPU NVIDIA** e driver aggiornati (obiettivo di test: RTX 2060, 6 GB), una **AMD Radeon** con Vulkan (finora misurata su una scheda, una RX 5700 XT: [cercasi aiuto](#cercasi-misure-su-una-amd-radeon)), oppure solo una CPU. Per **Linux** ci sono già le build di llama.cpp e il codice, ma non è ancora stato provato
- **macOS** su Apple Silicon (M1 o successivi): i modelli girano sulla GPU con Metal, nella memoria unificata (provato: M2 Pro, 16 GB, macOS 26.2; Qwen3-4B legge 593 token/s e ne scrive 57). I Mac Intel girano sulla CPU
- **Python 3.10 o successivo** (provato con la 3.12)
- Spazio su disco: circa 0,7 GB per llama.cpp con CUDA (100 MB con Vulkan, 60 MB per la CPU), più i modelli, da 0,6 GB (Qwen3-0.6B) a 2,3 GB per il 4B consigliato sulle schede da 6-8 GB
- **16 GB di RAM** consigliati per l'esecuzione solo su CPU e per i modelli più grandi della VRAM

L'app non ha bisogno di PyTorch: i modelli li esegue llama.cpp, e transformers fornisce solo i tokenizer. PyTorch serve solo per le misure sul backend PyTorch (sotto): installalo prima dall'index CUDA, poi `pip install -e ".[research]"`.

## Ragionamento in anticipo, nel dettaglio

Nell'app è attivo di default per Qwen3 (Impostazioni, *Ragiona mentre scrivo*); da riga di comando è `mafdet strike`, con le opzioni qui sotto. Il modello **comincia a pensare al tuo messaggio mentre lo stai ancora scrivendo**. Usa Qwen3 in modalità ragionamento, quindi prima di rispondere il modello ragiona in un blocco nascosto (mostrato chiuso sopra la risposta). Quel ragionamento di solito è l'attesa più lunga, e `strike` ne sposta il più possibile prima dell'invio:

1. **Mentre scrivi**, il modello ragiona a blocchi brevi di 32 token, sul messaggio così com'è in quel momento, a parole intere (dopo uno spazio o un segno di punteggiatura, oppure a una pausa).
2. **Quando il testo cambia**, il ragionamento non viene buttato. Resta nel prompt come appunti, dopo la nuova versione del messaggio. Spostarlo lì costa solo un prefill: la fase veloce e parallela, circa 60 volte più rapida che riscrivere gli stessi token sul 14B con la RTX 2060. Una nuova riga degli appunti nomina le parole appena scritte, così il modello continua a raccogliere materiale su di esse invece di ricominciare.
3. **All'invio**, un'ultima frase dice al modello che il messaggio ora è completo. Finisce di ragionare da dove era arrivato e risponde. Di rado (Qwen3-4B: una volta sui 24 problemi sul Mac, una volta in passato sulla RTX 2060) il modello scrive la risposta tra i suoi pensieri e chiude il turno senza uscire dal ragionamento: l'app allora chiude il ragionamento come fa il template di Qwen3, e il modello scrive la risposta, qualche secondo dopo invece di una risposta vuota.

Gli appunti sono un elenco di **materiale per la risposta**, aperto da una cornice: *"L'utente sta ancora scrivendo. Non mi chiederò cosa chiederà, né osserverò che il testo è incompleto. Mentre aspetto raccolgo materiale per la risposta: per ogni termine chiave, nome o numero già scritto, una riga con cos'è e i fatti che so, oppure un risultato parziale."* seguita da una riga di esempio presa da un altro messaggio. Ogni volta che scrivi altro, una nuova riga nomina le parole nuove: `- New words "finestra di contesto": `. Così da *"Quando finisce la finestra di contesto degli LLM?"* gli appunti contengono già cos'è una finestra di contesto e cosa significa LLM, e da un problema i risultati parziali (`3 × 12 = 36`) prima ancora che la domanda sia stata scritta.

La cornice conta. Senza, il modello spendeva gli appunti a osservare che il messaggio era incompleto e, dopo ogni modifica, ricopiava il suo ultimo paragrafo. Su sei messaggi digitati in tempo reale, la quota di frasi degli appunti che parlano dell'utente o del messaggio (invece che del contenuto) è scesa dall'86% con una cornice generica (*"ricava quello che puoi"*) al 41-54% con questo elenco, e al 17-26% con la riga di esempio e un logit bias che, durante il ragionamento in anticipo e solo lì, rende meno probabili parole come *user*, *incomplete*, *typing* (gli ID dei token vengono dal tokenizer del modello caricato, quindi funziona con qualsiasi modello). In modalità ragionamento si usa anche la presence penalty suggerita dagli autori di Qwen3, che tiene il 4B lontano dai loop in cui ripete una frase fino al limite di token.

**Se fai una pausa e invii il messaggio senza cambiarlo**, `strike` va oltre e scrive anche la risposta. Questa parte è esatta: la risposta vale solo per il testo esatto per cui è stata scritta, e qualsiasi modifica la scarta. Con `--no-think` (senza ragionamento) `strike` fa solo questo, e con la decodifica greedy la risposta è identica token per token a quella avviata dopo l'invio (verificato).

Il ragionamento in anticipo **non è esatto**: il modello ragiona su versioni intermedie del messaggio, quindi la risposta può essere diversa da quella che darebbe partendo da zero. Per questo è stato misurato su risposte verificabili (sotto).

| Opzione | Cosa fa |
|---|---|
| `--no-think` | niente ragionamento: risponde subito, e in anticipo scrive solo la risposta esatta |
| `--think-ahead N` | ragionamento più lungo conservato tra una modifica e l'altra (predefinito 1024 token) |
| `--think-budget N` | ragionamento concesso dopo l'invio (predefinito 0, nessun limite): oltre, il ragionamento viene chiuso, come quando il modello finisce il turno al suo interno, e il modello risponde con il materiale che ha |
| `--keep-reasoning` | conserva nella conversazione il ragionamento dei turni passati. Di norma restano solo messaggi e risposte, come nel template di chat di Qwen3: il contesto dura molti più turni (6 problemi di fila: 1.342 token invece di 8.015, che al sesto non stavano più negli 8.192 predefiniti) |
| `--ahead N` | token di risposta scritti in anticipo quando il testo non cambia (predefinito 512) |
| `--branches N` | sperimentale: il modello prova anche a indovinare come finirai il messaggio e, su uno slot tutto suo, ragiona sulla previsione; se ha indovinato il messaggio esatto, quel ragionamento (e la risposta, se ci è arrivato) è pronto all'Invio. 1 è il valore misurato, vedi sotto |
| `--recycle` | riusa le risposte scritte per le versioni precedenti del messaggio come bozze n-gram per la decodifica speculativa (+4% di velocità misurato con una digitazione naturale, +20% sul codice) |
| `--draft repo:file` | un modello piccolo della stessa famiglia propone token per la decodifica speculativa (nessun guadagno misurato sulla RTX 2060, vedi sotto) |

`mafdet watch` e `mafdet strike` aprono la finestra con il modello e le opzioni date da riga di comando (`--gguf repo:file`), invece delle impostazioni dell'app. `demo_live.py` (`start_demo.bat`) è la vista passo passo per il debug: token, cache, holdback e tempi, dal vivo nel terminale (`--auto` scrive da sola un esempio).

## Misurare e verificare

| | Comando | | Cosa fa |
|---|---|---|---|
| <img src="assets/icons/hunt.svg" width="22" alt=""> | `mafdet hunt` | la caccia | simula persone che scrivono e misura l'attesa dopo l'invio |
| <img src="assets/icons/race.svg" width="22" alt=""> | `mafdet race` | la corsa | misura il costo di prefill e decode sulla tua macchina |
| <img src="assets/icons/judge.svg" width="22" alt=""> | `mafdet judge` | il giudizio | confronta i risultati con un calcolo da zero: deve finire con 0 errori |
| <img src="assets/icons/awaken.svg" width="22" alt=""> | `mafdet awaken` | il risveglio | controlla PyTorch e la GPU, per il backend PyTorch |

Girano su llama.cpp con `--backend llama` (senza PyTorch), oppure su PyTorch/transformers con il predefinito `--backend hf` (`pip install -e ".[research]"` dopo aver installato PyTorch dall'index CUDA: da PyPI, su Windows, pip prenderebbe la versione solo CPU). `mafdet --help` è la guida completa, `mafdet <comando> --help` le opzioni di uno. Aggiungi `--device cpu` a qualunque comando per misurare il caso senza GPU. I risultati vengono salvati come file CSV in `results/` (si cambia con `--out`).

### Scenari di digitazione

`mafdet hunt` riproduce tre scenari (si scelgono con `--scenario`):

- **clean**: un messaggio scritto dall'inizio alla fine
- **messy**: refusi corretti con backspace, una parola cancellata e riscritta, un'aggiunta a metà testo, poca punteggiatura
- **paste**: un documento lungo incollato, seguito da una domanda scritta a mano. È il caso in cui il guadagno è massimo.

La digitazione non viene riprodotta in tempo reale. Ogni prefill viene eseguito davvero sul modello, e la sua durata misurata viene collocata sulla linea del tempo della digitazione. Se il modello resta indietro rispetto a chi scrive, il ritardo si accumula e viene sommato all'attesa finale, come in un sistema reale.

## Risultati

Su una RTX 2060 (6 GB) salvo dove indicato (il Mac e la Radeon hanno le loro tabelle: [Mac](#su-un-mac), [Radeon](#su-una-amd-radeon)), llama.cpp, modelli Q4_K_M, digitazione in tempo reale (da 5 a 8 caratteri al secondo, qualche pausa naturale, invio subito dopo l'ultimo carattere, senza rileggere).

**Ragionamento in anticipo, Qwen3-4B** (sta nella VRAM, 83 tok/s): 24 problemi con una risposta numerica nota, scritti in italiano (script e dati: `research/thinking_ahead.py`).

| | Senza ragionamento in anticipo | `mafdet strike` |
|---|---|---|
| Risposte corrette | 24/24 | 24/24 |
| Attesa mediana per la prima parola della risposta | 10,7 s | **2,9 s** |
| Attesa media | 12,2 s | **3,2 s** |
| Più veloce | | in 24 problemi su 24 |
| Ragionamento in totale | 944 token | **662 token** |
| Ragionamento rimasto dopo l'invio | 944 token | **244 token** |

Stessa precisione, meno ragionamento in totale (il materiale raccolto mentre scrivi evita al modello di ripartire da zero), e quasi due terzi fatti prima dell'invio. Le due cornici precedenti, misurate allo stesso modo: una cornice generica dava 22/24 e una mediana di 4,4 s; nessuna cornice dava 22/24, 6,9 s e il 70% di ragionamento in più.

**Risposta esatta in anticipo, Qwen3-4B, senza ragionamento** (pausa prima dell'invio, testo invariato):

| Risposta | Scritti prima dell'invio | Risposta completa a schermo dopo l'invio, strike | Senza strike |
|---|---|---|---|
| 300 token | 200 | 1,2 s | 3,5 s |
| 168 token | 168 (tutta) | istantanea | 1,9 s |

**Modelli più grandi.** Quello che il modello riesce a fare prima dell'invio è limitato dal tempo di digitazione × la sua velocità di scrittura:

| Modello | Sta in 6 GB? | Scrive | Ragionamento scritto mentre digiti un messaggio da 30 s |
|---|---|---|---|
| Qwen3-4B | sì | 83 tok/s | quasi tutto |
| Qwen3-8B | no, in parte sulla CPU | da 13 a 16 tok/s | da 130 a 280 token |
| Qwen3-14B | no, in parte sulla CPU | 5 tok/s | circa 160 token |

Anche così, sul 14B (6 problemi digitati in tempo reale, 6/6 corretti in entrambi i casi) il ragionamento in anticipo dimezza l'attesa media dopo l'invio: **da 108 s a 47 s**, con 164 token di ragionamento scritti prima dell'invio in media. Le singole prove variano molto (lo stesso problema ha richiesto 106 s una volta e 154 s un'altra), quindi 6 problemi danno l'ordine di grandezza, non una cifra precisa. La lettura in anticipo guadagna di più quanto più il modello è grande: il 14B legge un documento incollato da 3.000 token in circa 10 s, tutti nascosti mentre scrivi.

### Su un Mac

MacBook Pro M2 Pro, 16 GB, macOS 26.2, llama.cpp con Metal: gli stessi script e gli stessi problemi di sopra.

| M2 Pro | Senza ragionamento in anticipo | `mafdet strike` |
|---|---|---|
| **Qwen3-4B**, 24 problemi: risposte corrette | 24/24 | 24/24 |
| Attesa mediana per la prima parola della risposta | 16,7 s | **4,1 s** |
| Attesa media | 18,1 s | **5,6 s** |
| Più veloce | | in 24 problemi su 24 |
| Ragionamento in totale / rimasto dopo l'invio | 966 / 966 token | **773 / 248 token** |
| **Qwen3-14B**, 6 problemi: risposte corrette | 6/6 | 6/6 |
| Attesa mediana | 26,5 s | **11,7 s** |
| Attesa media | 32,7 s | **12,1 s** |

Sull'M2 Pro il 4B scrive a 54 tok/s (83-87 sulla RTX 2060), quindi entrambe le attese sono più lunghe che sul PC, e il ragionamento in anticipo ne toglie più o meno la stessa parte. Il 14B è dove il Mac fa la differenza: Metal può usare 12 GB dei 16 GB di memoria unificata, quindi il modello sta tutto sulla GPU e scrive a 18 tok/s (4,5-5 sulla RTX 2060, con il 62% sulla CPU). L'attesa dopo l'invio è di 12 s invece di 47 s. La colonna senza ragionamento in anticipo viene da una prova precedente dello stesso giorno; in quella prova una risposta è uscita vuota (il modello ha chiuso il turno dentro il ragionamento, vedi [Ragionamento in anticipo, nel dettaglio](#ragionamento-in-anticipo-nel-dettaglio)), cosa che ora l'app gestisce, e la colonna con il ragionamento in anticipo è la prova dopo la correzione. Durante le prove il Mac è arrivato per qualche minuto a una pressione termica *Heavy* (54 → 51 tok/s). Dati: `research/data/thinking_ahead_4b_m2pro.json`, `thinking_ahead_4b_m2pro_fixed.json`, `thinking_ahead_14b_m2pro.json`.

### Su una AMD Radeon

**Un primo dato, da indagare più a fondo**: una scheda, una prova, mandata da [SpykeBTW](https://github.com/SpykeBTW) ([issue #1](https://github.com/Mafdet-AI/mafdet/issues/1)). Radeon RX 5700 XT (8 GB), Ryzen 5 3600, 16 GB, Windows 11, driver AMD 31.0.23013.1023, llama.cpp b11747 con Vulkan: lo stesso script e gli stessi problemi di sopra.

| RX 5700 XT | Senza ragionamento in anticipo | `mafdet strike` |
|---|---|---|
| **Qwen3-4B**, 24 problemi: risposte corrette | 24/24 | 24/24 |
| Attesa mediana per la prima parola della risposta | 10,9 s | **6,8 s** |
| Attesa media | 14,0 s | **7,7 s** |
| Più veloce | | in 22 problemi su 24 |
| Ragionamento in totale / rimasto dopo l'invio | 920 / 920 token | **595 / 252 token** |

Il ragionamento in anticipo funziona anche sulla Radeon: stessa accuratezza, attesa media quasi dimezzata. Però, con più o meno lo stesso ragionamento rimasto dopo l'invio della RTX 2060 (252 token contro 244), l'attesa è il doppio (7,7 s contro 3,2-4,2 s), e il motivo è la velocità di lettura. *Misura* dice che la scheda scrive a 69 token/s, vicino alla RTX 2060, ma legge a soli **93 token/s**, contro circa 2.000 sulla RTX 2060 (con CUDA o con Vulkan). Il ragionamento scritto in anticipo sta dopo il messaggio nel prompt, quindi le ultime parole che scrivi fanno rileggere al modello tutto il ragionamento dopo l'invio: qui 343 token, 0,17 s sulla RTX 2060, circa 3,7 s su questa scheda. Questo spiega tutta la differenza. Senza ragionamento in anticipo l'attesa è tutta scrittura; con il ragionamento in anticipo ogni problema aspetta 2-5 s in più, esattamente il tempo di rileggere il ragionamento a 93 token/s. Con una lettura a velocità normale l'attesa sarebbe di circa 4 s.

Quello che ancora non sappiamo è da dove vengano i 93 token/s: dal Vulkan di llama.cpp su questa generazione (RDNA 1) con il driver AMD per Windows, oppure da qualcosa in MAFDET. Lo diranno un `llama-bench` sulla scheda e altre Radeon ([cercasi aiuto](#cercasi-misure-su-una-amd-radeon)). Dati: `research/data/thinking_ahead_4b_rx5700xt.json`.

**Un modello piccolo che raccoglie per uno grande (`--gatherer`, sperimentale).** Per i modelli più grandi della VRAM abbiamo provato a far scrivere materiale mentre scrivi a un modello piccolo e veloce (Qwen3-0.6B, anche allenato apposta con `training/`), che il 14B legge all'invio. Sulla RTX 2060 non batte il 14B che ragiona in anticipo da solo:

| 14B, attesa media dopo l'invio | |
|---|---|
| Senza ragionamento in anticipo | 108 s |
| **Ragionamento in anticipo da solo** | **47 s** |
| Raccoglitore, Qwen3-0.6B non allenato | 60 s |
| Raccoglitore, Qwen3-0.6B allenato | 79-86 s |
| Materiale ideale scritto a mano (tutti i risultati parziali giusti), presentato come "di un assistente veloce" | 62 s |
| Lo stesso materiale ideale, presentato come ragionamento del modello | 49 s |

Anche il materiale perfetto pareggia soltanto il ragionamento in anticipo: il 14B lo legge, poi rifà e ricontrolla i calcoli, tranne sui problemi più facili (17-24 s invece di 37-63 s). Conta più come il materiale viene presentato che la sua qualità (da 62 s a 49 s). Domande aperte per chi vuole portare avanti questa ricerca: combinare le due cose (il modello grande ragiona in anticipo *e* legge il materiale come suo), insegnare al modello grande a fidarsi dei risultati parziali, oppure un raccoglitore dove il modello grande non riesce affatto a ragionare in anticipo (solo CPU).

**Indovinare il finale (`--branches`, sperimentale).** Il ragionamento che resta dopo l'Invio riguarda soprattutto la fine del messaggio, che arriva per ultima: in un problema, la domanda. Qwen3-4B spesso la indovina prima che tu la scriva: in un run, con il testo a *"…e una penna da 2"*, una delle sue previsioni era già il resto esatto del messaggio, *"euro. Quanti euro gli restano?"*. Così con `--branches 1` il modello prova anche a indovinare come finirai il messaggio (solo quando mancano poche parole, e solo quando due previsioni di fila coincidono), e su uno slot di llama-server tutto suo ragiona sul messaggio previsto come se l'avessi già inviato, mentre il ragionamento principale aspetta. Se poi invii esattamente quel messaggio, il suo ragionamento e la sua risposta sono stati scritti proprio per quel prompt e vengono presi così come sono. Stessi 24 problemi, due run ciascuno (48 messaggi; script e dati in `research/`):

| | Ragionamento in anticipo | `--branches 1` |
|---|---|---|
| Risposte giuste | 48/48 | 48/48 |
| Attesa media per la prima parola della risposta | 4,17 s | **3,51 s** |
| Attesa mediana | 2,98 s | 2,85 s |
| Il decimo più lento | 7,9 s | **5,3 s** |
| Messaggi indovinati esatti | | 14 su 48 |

La colonna del ragionamento in anticipo è stata rimisurata per questo confronto, lo stesso giorno e sullo stesso codice: 4,17 s contro i 3,2 s della tabella sopra, due run degli stessi 24 problemi differiscono di tanto. Un guadagno modesto, soprattutto sulle risposte più lente, e su 24 problemi non ancora significativo (più veloce in 16 su 24, test dei segni p = 0,15). Cosa non ha funzionato: adottare una previsione solo vicina (*"ha rimasto"* per *"gli restano"*), con una frase che chiede al modello di ricontrollare il ragionamento sul messaggio vero, ha dato 2 risposte sbagliate su 4: la previsione aveva un altro numero (32 per 41), o una parola che cambiava il problema (*"età attuale"*). Due rami insieme sono più lenti di nessuno (mediana 4,50 s contro 2,94 s su metà dei problemi): sulla RTX 2060 due sequenze scrivono 1,7 volte i token di una, quattro 2,2 volte, quindi ogni ramo rallenta gli altri.

**Più grandi della VRAM.** Su Qwen3-14B (in parte sulla CPU, 4,5 tok/s via llama-server) nemmeno una seconda sequenza è gratis: due vanno ciascuna al 77% della velocità di una, tre al 55%. E ogni previsione richiede secondi: 1 o 2 per messaggio invece delle 10-20 del 4B, così su 4 problemi nessun ramo ha mai cominciato a ragionare prima dell'Invio (attesa media 44,7 s con l'opzione, 43,9 s senza: rumore, i singoli problemi vanno da 24 s a 73 s). L'aritmetica comunque lascia poco spazio: tra il momento in cui la domanda è indovinabile e l'Invio passano pochi secondi, circa 20 token del 14B, contro i circa 200 che restano dopo l'Invio. Per questo nell'app *Indovina il finale* dà un ramo solo ai modelli che stanno in VRAM, e nessuno agli altri.

**Provate, e perché non sono attive di default:**

- `--recycle` (le risposte alle versioni precedenti del messaggio come bozze n-gram): +4% di velocità di scrittura con una digitazione naturale, +20% sul codice, primo token un po' più tardi.
- `--draft` (Qwen3-0.6B che propone token al 14B): sulla GPU toglie VRAM al 14B, che porta più layer sulla CPU (da 4,8 a 3,5 tok/s); sulla CPU guadagna l'8% con bozze da 4 token e dimezza la velocità di lettura.
- Il ragionamento in anticipo come bozza per la decodifica speculativa dopo l'Invio (`--spec-type ngram-simple`: le note sono nel prompt, e dopo l'Invio il modello rifà gli stessi passaggi). Simulata offline, la prompt lookup scriverebbe da 1,5 a 1,7 token per passo di decodifica su Qwen3-4B, contro 1,3-1,5 senza note. Dal vero non è più veloce sul 4B (73 tok/s in entrambi i casi) ed è più lenta sul 14B (da 5,0 a 3,1 tok/s): verificare una bozza di 9 token costa al 14B 2,4 passi di decodifica, perché i suoi layer sulla CPU calcolano ogni token della bozza, e solo l'11% dei token proposti era giusto. Script e dati: `research/notes_as_draft.py`.

## Cercasi: misure su una AMD Radeon

MAFDET gira sulle schede AMD Radeon con la build Vulkan di llama.cpp, e le macchine qui sono una RTX 2060 e un M2 Pro: finora è stata misurata una sola Radeon, da [SpykeBTW](https://github.com/SpykeBTW) ([primi numeri](#su-una-amd-radeon): il ragionamento in anticipo funziona, ma quella scheda legge molto più lentamente del previsto, ancora da capire). Il percorso Vulkan in sé è verificato, sulla RTX 2060 (Qwen3-4B legge 2.103 token/s e ne scrive 83, più o meno come con CUDA; 3 problemi su 3 corretti, il ragionamento in anticipo funziona). Mancano altre Radeon, soprattutto di altre generazioni. Se ne hai una (una scheda dedicata, RX 5000 o successiva; 8 GB o più è l'ideale), una mezz'ora riempirebbe questa pagina:

1. Installa MAFDET ([Avvio rapido](#avvio-rapido)) e aprilo una volta: dovrebbe trovare la scheda, installare llama.cpp *Windows · Vulkan* (o *Linux · Vulkan*) e scaricare Qwen3-4B. `mafdet doctor` mostra cosa ha trovato.
2. Nell'app, *Modelli*, **Misura** Qwen3-4B.
3. Dalla cartella di MAFDET, con il suo ambiente attivo: `python research/thinking_ahead.py radeon_4b.json`. Scrive 24 problemi in tempo reale, con e senza ragionamento in anticipo, e ci mette circa 20 minuti; lascia la macchina libera nel frattempo.
4. [Apri una issue](https://github.com/Mafdet-AI/mafdet/issues/new) con l'output di `mafdet doctor`, i numeri di *Misura* e `radeon_4b.json`. Il tuo nome va in [Grazie](#grazie), accanto ai tuoi numeri.
5. Se puoi, anche l'output di `%USERPROFILE%\.mafdet\llama.cpp\llama-bench.exe -m %USERPROFILE%\.mafdet\models\Qwen3-4B-Q4_K_M.gguf -p 512 -n 128` (su Linux, gli stessi percorsi sotto `~`): le velocità di llama.cpp da solo, senza MAFDET in mezzo.

Se qualcosa non funziona (la scheda non trovata, llama.cpp che non parte, il modello che gira sulla CPU), una issue con l'output di `mafdet doctor` e i file `server*.log` accanto a llama-server (in `~/.mafdet/llama.cpp`) è altrettanto utile. Le Radeon integrate (APU Ryzen) per ora non vengono usate: restano sulla build per CPU. Qualsiasi scheda con un driver Vulkan può provarla con `mafdet doctor --install --build vulkan`.

## Roadmap

- [x] Motore di prefill incrementale con sincronizzazione sul prefisso comune e holdback dei token
- [x] Benchmark, simulazione della digitazione e verifiche di correttezza
- [x] CLI unificata `mafdet`
- [x] `mafdet watch`: la finestra della chat
- [x] Backend llama.cpp con modelli quantizzati (GGUF)
- [x] `mafdet strike`: ragionamento in anticipo mentre scrivi, conservato tra le modifiche; la risposta esatta in anticipo a una pausa
- [x] L'app: finestra e terminale, catalogo dei modelli con le stime per il tuo hardware, installazione automatica di llama.cpp, primo avvio guidato, quattro lingue
- [x] Cinque famiglie di modelli: Qwen3, Llama 3, Gemma 3, Mistral, Phi-4
- [x] Qualsiasi GGUF: ricerca e download da Hugging Face nell'app, o file tuoi
- [x] Un modello piccolo raccoglie mentre scrivi, uno grande risponde: provato e misurato (nessun guadagno sulla RTX 2060, vedi Risultati)
- [ ] Ragionamento in anticipo per i modelli più grandi della VRAM che batta il modello grande che ragiona da solo
- [ ] Il proxy: lettura e ragionamento in anticipo nell'interfaccia e nel motore che usi già (Open WebUI, Ollama, vLLM, SGLang, ...). La lettura in anticipo su llama.cpp funziona con Open WebUI: `mafdet proxy`, vedi [Open WebUI + MAFDET in 5 minuti](mafdet/proxy/clients/README.md) (in inglese); Ollama, vLLM, SGLang e LM Studio: `mafdet proxy --url`, scritto sulle loro API documentate e non ancora provato davvero (serve aiuto); a che punto siamo, cosa manca e come aiutare: [PROXY.md](PROXY.md) (in inglese)
- [ ] L'agente: un modello locale che lavora con degli strumenti (file, shell, server MCP), prima di tutto per il reverse engineering. Il ciclo, il bilancio del contesto con le note e la compattazione, le conferme, una shell che resta aperta, il web e il client MCP ci sono (`mafdet agent`, sperimentale) e hanno girato con Qwen3-14B su una RTX 4070 SUPER; anche i nomi alle funzioni di un binario dal basso, in un libro tutto suo (`--walk`, `--book`), ci sono, e hanno dato un nome a tutte le 86 funzioni di un primo binario piccolo (le foglie bene, la cima dell'albero male); un secondo passaggio sulla cima c'è (`--refine`), da misurare; poi il ragionamento in anticipo mentre uno strumento gira o l'analista naviga, e un benchmark
- [ ] Ragionamento in anticipo oltre Qwen3, anche per i modelli di ragionamento con formati propri (DeepSeek-R1, gpt-oss): ognuno richiede di misurare la sua cornice
- [ ] Voce: il parlato trascritto in streaming (whisper.cpp) alimenta la stessa lettura e lo stesso ragionamento in anticipo mentre l'utente parla, la fine del turno fa da Invio (una pausa avvia la risposta, se l'utente riprende a parlare viene scartata) e la risposta viene pronunciata mentre il resto è ancora in scrittura: la voce va a 3-4 token al secondo, quindi si aspettano solo il ragionamento rimasto e la prima frase. Si parla circa il doppio più in fretta di quanto si scriva, quindi si ragiona meno in anticipo: da misurare come per la scrittura, come tempo fino alla prima parola pronunciata
- [x] macOS su Apple Silicon (Metal)
- [x] AMD Radeon con Vulkan (pronta; misurata su una scheda, una RX 5700 XT, con la lettura lenta ancora da capire; altre schede: cercasi aiuto, sopra)
- [ ] Linux provato

## Ricerca correlata

- [LiveMind: Low-latency Large Language Models with Simultaneous Inference](https://arxiv.org/abs/2406.14319)
- [StreamingThinker: Large Language Models Can Think While Reading](https://arxiv.org/abs/2510.17238)
- [SHANKS: Simultaneous Hearing and Thinking for Spoken Language Models](https://arxiv.org/abs/2510.06917)
- [Asynchronous Reasoning: Training-Free Interactive Thinking LLMs](https://arxiv.org/abs/2512.10931)

## Grazie

A chi ha misurato MAFDET sul proprio hardware:

- [SpykeBTW](https://github.com/SpykeBTW): la prima AMD Radeon, una RX 5700 XT ([issue #1](https://github.com/Mafdet-AI/mafdet/issues/1), [numeri](#su-una-amd-radeon)).

## Licenza

[Apache-2.0](LICENSE)
