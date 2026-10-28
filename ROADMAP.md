# Roadmap

Ordine indicativo. Ogni voce diventa una o più patch in `patches/` (titolo del commit con
`win:`, `mac:` o `ios:` per quelle di una sola piattaforma), quindi sopravvive agli
aggiornamenti di Cromite come tutto il resto.

## Fatto

- Sistema di patch che si riapplica a ogni release di Cromite (`tools/forkctl.py`, rerere,
  controllo giornaliero).
- Repo pubblico solo per le build, con controllo dei dati personali in CI.
- Build Windows a tappe sui runner gratuiti di GitHub (prima esecuzione in corso).

## 1. Prima build Windows installabile

- Portare a termine la prima build e provarla.
- Prima release pubblica con `update.json`.

## 2. Anti-fingerprinting: base di Cromite + randomizzazione come Brave

**Cosa fa già Cromite** (`docs/FEATURES.md`, sezione "Anti fingerprinting"):

- API spente di default: Battery, Gamepad (anche gli eventi), SpeechSynthesis, WebGL, WebRTC
  (che comunque non espone gli IP locali), Compression Dictionary Transport.
- Attenuazioni: Canvas e ClientRects, Media API, AudioBuffer e AnalyserNode.
- Valori sostituiti: `navigator.language`, `navigator.connection`, memoria del dispositivo e
  numero di CPU; fuso orario personalizzabile o casuale; frequenza reale dello schermo nascosta;
  `screen.isExtended` corretto.
- Desktop: attenuazione della Keyboard Layout API. Windows: PublicKeyCredential, niente font
  locali o non standard.

**Da aggiungere: la randomizzazione ("farbling") di Brave.** Valori alterati in modo
impercettibile ma diversi per ogni sito, così due visite non danno la stessa impronta.

- Seme come Brave: cambia per sessione, per sito (eTLD+1) e per area di storage; i frame e gli
  script di terze parti usano il seme del sito principale.
- API da coprire, sul modello di brave-core (`browser/farbling/*_browsertest.cc` ne elenca i
  casi): Canvas e OffscreenCanvas, WebGL, WebGPU, Web Audio, `navigator.userAgent`,
  `hardwareConcurrency`, `deviceMemory`, `languages`, `plugins`, `enumerateDevices`, voci di
  `speechSynthesis`, `navigator.usb`.
- API per API, confronto con Cromite: dove Cromite blocca o fissa un valore, decidere se
  tenere il blocco o passare alla randomizzazione (che rompe meno siti).
- Interruttore per sito, come gli Shields di Brave, e un elenco di siti "fragili" su cui
  alleggerire le protezioni.
- Verifica con la pagina di test del farbling di Brave e con i test di impronta più noti: il
  valore deve cambiare tra siti, tra sessioni e dopo aver cancellato i dati del sito.
- **Obiettivo da superare: Cover Your Tracks (EFF) deve dire "your browser has a randomized
  fingerprint"**, come con Brave ed Epic. Dal suo codice (`static/fetch_whorls.js`, funzione
  `determine_randomized`): legge l'impronta **due volte di fila nella stessa pagina** e dà quel
  risultato se **almeno 4** di questi attributi cambiano tra le due letture: canvas, hash WebGL,
  vendor/renderer WebGL, audio, font, supporto touch, fuso orario (offset e nome), plugin,
  supercookie.
  - Quindi un valore fisso per sessione e sito non basta: almeno 4 attributi (i candidati
    naturali sono canvas, WebGL, audio e font) devono variare da una lettura all'altra.
  - Con WebGL spento, come fa Cromite di default, quei due attributi non contano: o lo si
    riattiva con la randomizzazione, o si arriva a 4 con gli altri.
  - Rischio da gestire: un rumore nuovo a ogni lettura si può togliere facendo la media di tante
    letture. Idea da verificare: per ogni sessione e sito, poche varianti fisse che si alternano,
    così due letture consecutive differiscono ma la media non riporta al valore vero.
  - Prima di scrivere codice, verificare come ci riescono Brave ed Epic.
- Licenza: brave-core è MPL-2.0, compatibile con la GPL-3.0 di Cromite; i file portati
  mantengono la loro intestazione MPL.

## 3. Pannello "Fingerprinting" nel menu delle informazioni sul sito

Il menu che si apre dall'icona a sinistra dell'indirizzo, quello con "Cookie e dati dei siti"
e le autorizzazioni.

- Una sezione con **tutte le API di fingerprinting** che la pagina ha usato e cosa ha fatto
  il browser (bloccata, randomizzata, permessa), per categoria: Canvas, WebGL, WebGPU, Audio,
  ClientRects, Font, Timezone, Navigator, WebRTC, Screen, Storage, Permessi, Battery, `eval`,
  Referrer.
- **Canvas e WebGL:** un riquadro con l'anteprima dell'immagine che il sito ha disegnato per
  ricavare l'impronta, accanto all'elenco delle API bloccate.
- **Contatore:** un numero accanto all'icona nella barra degli indirizzi con quante API sono
  state bloccate nella pagina; si azzera quando si cambia sito.
- Come farlo: Blink registra per ogni frame le chiamate intercettate (stesso punto in cui agisce
  la randomizzazione) e le manda al processo del browser via Mojo, come Chromium fa per i
  contenuti bloccati (`PageSpecificContentSettings`); la UI è una nuova sezione di Page Info,
  il contatore una variante dell'icona di Page Info nella barra degli indirizzi.
- Comportamento di riferimento: le estensioni di analisi della privacy che mostrano, sito per
  sito, gli eventi di fingerprinting, l'anteprima di canvas/WebGL e un badge con i blocchi.

## 4. Traduzioni italiane dove mancano

Chromium è già tradotto in italiano, ma le stringhe aggiunte da Cromite no: delle 343 che le sue
patch aggiungono ai file `.grd`/`.grdp`, solo quelle dell'adblock (eyeo) hanno le traduzioni.
Tolte le 222 solo Android, ne restano **121 che servono anche su Windows, macOS e iOS**:
autorizzazioni dei siti aggiunte da Cromite (fuso orario, WebGL, WebRTC, viewport, autoplay,
geometria della tastiera...), userscript e altre impostazioni.

- Una patch `common/` che aggiunge le voci italiane ai file `*_it.xtb` di Chromium.
- Uno strumento (`tools/i18n.py`) che elenca le stringhe di Cromite e nostre senza traduzione
  italiana, calcola gli ID come fa grit e rigenera la patch da un file di traduzioni nostro: a
  ogni aggiornamento di Cromite dice quali stringhe nuove o cambiate vanno tradotte.
- Le stringhe delle nostre patch (pannello Fingerprinting, sync, ecc.) nascono già con
  l'italiano.
- Fuori dal lavoro: `chrome://flags`, che in Chromium è sempre solo in inglese.

## 5. Sync sul proprio server

- Server: `go-sync` di Brave (MPL-2.0) in Docker sul proprio NAS o server.
- Nel browser: indirizzo del server impostato nelle impostazioni di ogni dispositivo (mai nel
  codice), login Google sostituito da una chiave nostra, cifratura con passphrase.
- Sicurezza: `go-sync` accetta qualunque client che si firmi da solo con una chiave ed25519,
  quindi va raggiungibile solo dalla rete privata (LAN o VPN come Tailscale), con un elenco
  delle chiavi dei propri dispositivi; i dati restano cifrati anche sul server.

## 6. macOS

- Porting: Cromite non compila per Mac, le correzioni vanno in `patches/macos/`.
- **Tema "Liquid Glass" come Safari:** barra delle schede e degli strumenti traslucide con
  l'effetto vetro di sistema di macOS 26 (`NSGlassEffectView`) dietro la UI di Chromium,
  forme e spaziature in stile Safari, modalità chiara e scura, rispetto di "Riduci trasparenza".
- Aggiornamento in-app da `update.json`, firma ad-hoc (Developer ID più avanti).

## 7. Cookie centralizzati

Dopo il sync: sincronizzazione dei cookie solo cifrata e solo per i siti scelti (alcuni siti
legano la sessione al dispositivo e non funzionerebbero comunque).

## 8. iOS

- Base: Chromium per iOS (WebKit, regola Apple).
- Funzioni di Cromite da reimplementare in `ios/`: adblock con le regole di contenuto di
  WebKit, randomizzazione con script iniettati come fa Brave iOS
  (`ios/browser/brave_shields/resources/farbling.ts`).
- **Tema "Liquid Glass" nativo** con gli effetti vetro di iOS 26 (`UIGlassEffect`).
- Distribuzione: `.ipa` da firmare (AltStore/SideStore o account Apple Developer) e una
  sorgente AltStore per gli aggiornamenti.

## Windows

- Tema: quello standard di Chromium, nessuna personalizzazione.
- Aggiornamento in-app da `update.json` (Cromite su Windows disattiva l'updater di Chromium).
