# Roadmap

Ordine indicativo. Ogni voce diventa una o più patch in `patches/` (titolo del commit con
`win:`, `mac:` o `ios:` per quelle di una sola piattaforma), quindi sopravvive agli
aggiornamenti di Cromite come tutto il resto.

## Fatto

- Sistema di patch che si riapplica a ogni release di Cromite (`tools/forkctl.py`, rerere,
  controllo giornaliero).
- Repo pubblico solo per le build, con controllo dei dati personali in CI.
- Build Windows a tappe sui runner gratuiti di GitHub (prima esecuzione in corso).

## 1. Prima build Windows installabile, poi build veloci

- Portare a termine la prima build e provarla.
- Prima release pubblica con `update.json`.

**Riuso della build precedente.** Oggi ogni build riparte da zero (i runner di GitHub sono puliti
a ogni esecuzione), quindi dura come la prima. Per gli aggiornamenti mensili di Chromium va bene,
perché cambiano comunque quasi tutti i file. Mentre sviluppiamo le nostre patch, invece, serve
una build che ricompili solo quello che cambia:

- a fine build si salva la cartella di build completa (sorgenti e `out/`, orari dei file
  conservati) come artifact legato alla versione di Chromium e al tag di Cromite;
- se la build successiva ha la stessa versione di Chromium e lo stesso Cromite, parte da lì
  invece di riscaricare tutto;
- si riscrivono solo i file toccati dalle patch, vecchie e nuove. Il contenuto originale (Chromium
  più Cromite) lo dà il mini-albero di `forkctl`, e i file identici non si riscrivono, così
  ninja non li considera cambiati;
- ninja ricompila solo i file toccati e ciò che ne dipende, più il link finale;
- da misurare con la prima build: quanto spazio occupa l'artifact, quanto a lungo GitHub lo
  conserva gratis per un repo pubblico, quanto dura il link finale di `chrome.dll` con le
  ottimizzazioni complete. Se il link pesa troppo, una configurazione "di sviluppo" con
  ottimizzazioni ridotte, separata da quella delle release.

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
- Due strategie, su segnali diversi:
  - **randomizzare** i segnali "rumorosi", dove una piccola alterazione non si nota: canvas e
    OffscreenCanvas, audio, font, plugin;
  - **uniformare** i valori precisi che i siti usano davvero (versioni, schermo): un valore
    inventato renderebbe l'impronta più rara e romperebbe l'impaginazione, quindi tutti
    dichiarano gli stessi valori (vedi sotto). CPU, memoria e lingua li fissa già Cromite.
- Da valutare sul modello di brave-core (`browser/farbling/*_browsertest.cc` elenca i casi che
  randomizza): `enumerateDevices`, voci di `speechSynthesis` (Cromite le spegne già),
  `navigator.usb`.
- **WebGL resta spento** (decisione): espone la scheda video ed è dannoso per la privacy, quindi
  non lo randomizziamo e non lo riattiviamo. WebGPU espone la GPU allo stesso modo: verificare
  cosa fa Cromite e, per coerenza, spegnerlo anche lui.
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
  - Con WebGL spento i suoi due attributi non contano: i 4 da far variare sono **canvas, audio,
    font e plugin**.

**Metodo: il meno tracciabile possibile.**

- **Un'impronta diversa per ogni sito:** seme per sessione × sito (eTLD+1) × area di storage.
  Due siti non possono collegarti confrontando le impronte; l'impronta cambia a ogni riavvio del
  browser e quando cancelli i dati del sito.
- **Varianti a rotazione:** per ogni sessione e sito, poche varianti fisse (per esempio 3) di
  canvas, audio, font e plugin, usate a turno a ogni lettura.
  - Due letture di fila sono diverse, quindi per Cover Your Tracks l'impronta è "randomized".
  - La media di tante letture dà la media delle varianti, che hanno uno scostamento comune dal
    valore vero: non lo fa riemergere, come invece succede con un rumore nuovo a ogni lettura.
- **Valori credibili:** rumore impercettibile su canvas e audio, sottoinsiemi diversi di un elenco
  di font comune, elenchi di plugin plausibili come quelli di Brave. Mai valori impossibili, che
  renderebbero l'impronta rara invece di confonderla.
- **Fuso orario:** resta fisso per sessione (casuale o scelto, come già fa Cromite), non a ogni
  lettura, per non sbagliare gli orari mostrati dai siti.
- Prima di scrivere codice, verificare come ci riescono Brave ed Epic.

**Priorità**, in base a quello che i tracker leggono davvero (riferimento: i segnali raccolti
da FingerprintJS, la libreria open source più diffusa, `src/sources/index.ts`):

1. **Canvas, font e audio:** tra i segnali più usati e più identificanti, e contano per Cover Your
   Tracks. Font in entrambi i modi in cui vengono letti: elenco dei font installati (misurando il
   testo) e resa dei font predefiniti. Audio compresa la latenza (`audioBaseLatency`).
2. **Plugin:** solo per arrivare a 4 attributi in Cover Your Tracks. Valgono quasi niente per il
   tracciamento (in Chromium restituiscono a tutti lo stesso elenco di lettori PDF), ma sono il
   quarto più innocuo: supporto touch e fuso orario a ogni lettura romperebbero i siti, i
   supercookie non si possono randomizzare.
3. **Client Hints e schermo, uniformati** (sotto): non contano per Cover Your Tracks, ma sono tra i
   segnali più ricchi per i tracker.
4. Il resto, confrontato con quello che fa già Cromite: preferenze di visualizzazione (contrasto,
   HDR, gamma di colori, movimento ridotto), formato di data e lingua, `enumerateDevices`.

**Uniformare: Client Hints.** Sono le informazioni su browser e sistema negli header
`Sec-CH-UA-*` e in `navigator.userAgentData`. Quelle ad alta precisione diventano uguali per
tutti:

- versione del browser solo maggiore (`153.0.0.0` invece di `153.0.8010.37`), come la stringa
  User-Agent: Cromite ha già una patch per la versione completa, da verificare e completare;
- versione del sistema (`platformVersion`): un solo valore per sistema operativo, invece di quello
  vero che distingue le singole build di Windows o di macOS;
- architettura, bit e formato del dispositivo: quelli standard della piattaforma, mai più
  dettagliati del necessario;
- **coerenza** fra stringa User-Agent, `navigator.platform`, `userAgentData` e header: un browser
  che si contraddice è facilissimo da riconoscere.

**Uniformare: schermo e finestra.**

- Risoluzione dichiarata come uno dei tagli più comuni per quel sistema; area utilizzabile
  (`availWidth`/`availHeight`) uguale allo schermo intero, così non si leggono dimensione e
  posizione di barra delle applicazioni e Dock; profondità colore standard.
- **Letterboxing** come in Tor Browser e Mullvad Browser: l'area della pagina viene arrotondata a
  multipli fissi (per esempio 200×100 pixel) con un bordo neutro, così la dimensione esatta della
  finestra, il dato più identificante, non si legge. Il bordo si vede, quindi è un'opzione:
  attiva di default in incognito, attivabile anche nella navigazione normale.

**Verifica** (tutte devono passare):

1. Cover Your Tracks dice "your browser has a randomized fingerprint" invece di "unique".
2. La media di 100 letture di canvas e audio non coincide con il valore vero.
3. Due siti diversi vedono impronte diverse nella stessa sessione.
4. Dopo un riavvio o dopo aver cancellato i dati del sito l'impronta è nuova.
5. Client Hints, User-Agent e `navigator.platform` danno gli stessi valori su ogni computer con
   lo stesso sistema, e non si contraddicono tra loro.
6. Con il letterboxing attivo, finestre di misure diverse danno la stessa dimensione della pagina
   entro un multiplo.

Licenza: brave-core è MPL-2.0, compatibile con la GPL-3.0 di Cromite; i file portati mantengono
la loro intestazione MPL.

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

## 6. Motore di ricerca sul proprio server (SearXNG)

SearXNG è un metamotore open source: interroga Google, Bing, DuckDuckGo e altri per conto tuo e
restituisce i risultati senza pubblicità, account o profilazione.

- **Server:** SearXNG in Docker sul proprio NAS o server, raggiungibile solo dalla rete privata
  (LAN o Tailscale), come il sync, con HTTPS e il proxy delle immagini attivo.
- **Uscita delle ricerche:** se SearXNG interroga i motori direttamente, Google e Bing vedono tutte
  le ricerche arrivare dall'IP di casa. Per la massima privacy le sue richieste in uscita passano
  da una VPN o da Tor (impostazioni `outgoing` di SearXNG); in cambio, ogni tanto qualche motore
  risponde con un CAPTCHA e i suoi risultati mancano.
- **Nel browser:**
  - Chromium sa già usare un motore personalizzato (Impostazioni → Motore di ricerca → Ricerca
    sui siti → Aggiungi, poi "Imposta come predefinito"), e Cromite ha già corretto il supporto
    OpenSearch per gli indirizzi con un percorso: SearXNG funziona anche senza patch.
  - Patch nostra: una voce "SearXNG personale" in cui si inserisce solo l'indirizzo del server
    (mai nel codice), con i suggerimenti mentre scrivi (`/autocompleter`) e già impostata come
    predefinita.
  - Ripiego automatico su DuckDuckGo Light (già nell'elenco di Cromite) quando il server non è
    raggiungibile, per esempio fuori casa senza VPN.
  - Su iOS Chromium limita la scelta del motore predefinito: lì serve una patch apposta.

## 7. macOS

- Porting: Cromite non compila per Mac, le correzioni vanno in `patches/macos/`.
- **Tema "Liquid Glass" come Safari:** barra delle schede e degli strumenti traslucide con
  l'effetto vetro di sistema di macOS 26 (`NSGlassEffectView`) dietro la UI di Chromium,
  forme e spaziature in stile Safari, modalità chiara e scura, rispetto di "Riduci trasparenza".
- Aggiornamento in-app da `update.json`, firma ad-hoc (Developer ID più avanti).

## 8. Cookie centralizzati

Dopo il sync: sincronizzazione dei cookie solo cifrata e solo per i siti scelti (alcuni siti
legano la sessione al dispositivo e non funzionerebbero comunque).

## 9. iOS

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
