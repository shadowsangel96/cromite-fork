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
- **Correzioni di sicurezza di Chromium senza aspettare Cromite** (obbligatorio). Quando Chrome
  pubblica una correzione dentro la stessa versione principale (per esempio da 153.0.8010.37 a
  153.0.8010.55) e Cromite non l'ha ancora seguita, il controllo giornaliero applica le patch di
  Cromite e le nostre su quella versione e avvia la build.
  - Dentro la stessa versione principale le differenze sono minime: le patch quasi sempre
    entrano pulite. Se una non entra, il workflow si ferma e avvisa come per gli altri conflitti,
    e resta l'ultima build buona.
  - Le versioni principali nuove (ogni 4 settimane) aspettano sempre Cromite.
  - In `forkctl`: la versione di Chromium in `upstream.lock` diventa indipendente dal tag di
    Cromite, e il controllo guarda anche le release stabili di Chromium.
- **Aggiornamenti in "Informazioni su"** (`chrome://settings/help`), come in Chrome: il browser
  mostra la versione, controlla se ce n'è una nuova, la scarica e chiede di riavviare. La pagina
  c'è già in Chromium; il pezzo che la collega all'aggiornamento (`VersionUpdater`) va scritto
  per ogni piattaforma: il nostro aggiornamento su Windows, Sparkle su macOS, TestFlight su iOS
  (lì la pagina rimanda all'app TestFlight).

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
- **WebGL spento di default** (decisione): espone la scheda video ed è la fonte di impronta più
  ricca. Cromite lo blocca già e lo consente sito per sito (`Add-webGL-site-setting.patch`, che
  non rivela il dispositivo nemmeno nei messaggi di errore). In più mettiamo nel pannello del sito
  **"Consenti WebGL su questo sito (randomizzato)"**, per i siti che ne hanno bisogno (mappe 3D,
  editor, giochi). Lì funziona, ma con rumore diverso per sito sull'immagine letta (`readPixels`),
  nome della GPU generico come in Safari e parametri arrotondati a valori comuni. Il rumore non
  ferma le tecniche che riconoscono la GPU dai tempi di calcolo: per questo resta un'eccezione e
  non il default.
- WebGPU: Cromite lo spegne già (`Disable-WebGPU.patch`), e resta spento.
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
- **Con un container VPN già attivo (es. gluetun):** SearXNG manda tutte le richieste al proxy
  HTTP del container (`outgoing.proxies` in `settings.yml`), senza toccare chi lo usa già. Se la
  VPN cade, le ricerche falliscono invece di uscire dall'IP di casa, e anche i nomi dei siti li
  risolve la VPN. Prova fatta: dal proxy di una VPN con uscita all'estero Google, Bing, DuckDuckGo, Startpage
  e Qwant rispondono, Brave Search risponde 429 (blocca gli IP delle VPN). Il paese di uscita
  conta poco, perché SearXNG chiede comunque i risultati in italiano. Se la stessa VPN porta anche
  download pesanti, a pieno carico le ricerche rallentano: in quel caso conviene un container VPN apposta,
  su un server diverso dagli altri se usano la stessa chiave WireGuard, perché due tunnel con la
  stessa chiave sullo stesso server si disturbano.
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

## 7. Navigazione tramite la VPN del proprio server

Solo il browser passa dalla VPN, il resto del dispositivo no: le altre app (banca, giochi)
restano sulla connessione normale e Tailscale resta acceso (su iOS si può tenere attiva una
sola VPN alla volta, quindi l'app della VPN escluderebbe Tailscale). Il browser usa come proxy
il proxy HTTP di un container VPN sul proprio server (es. gluetun), raggiunto in LAN o via
Tailscale. È il modello di Mullvad Browser: protezione dal fingerprinting nel browser più un IP
condiviso con altri utenti della VPN.

- **Base:** Cromite ha già la pagina `chrome://proxy` (`Add-a-proxy-configuration-page.patch`).
  Salva le impostazioni nel Local State, quindi il proxy vale anche per le richieste interne
  del browser (aggiornamenti, liste dell'adblock). Da verificare che la pagina ci sia anche
  nelle build desktop.
- **Patch nostra:**
  - Voce "VPN personale" in cui si inserisce solo l'indirizzo del proxy (mai nel codice).
  - Esclusioni automatiche dal proxy: `<local>`, reti private (10.0.0.0/8, 172.16.0.0/12,
    192.168.0.0/16) e Tailscale (100.64.0.0/10, `*.ts.net`). Così il server stesso (SearXNG,
    sync) resta raggiungibile direttamente.
  - WebRTC: con il proxy attivo, politica `disable_non_proxied_udp`, perché altrimenti WebRTC
    può uscire diretto e rivelare l'IP di casa. Verifica con un test di leak WebRTC.
  - Server non raggiungibile (spento di notte o guasto): nessun ripiego silenzioso sulla
    connessione diretta, che mostrerebbe l'IP di casa senza accorgersene. Un indicatore nella
    barra degli strumenti mostra sempre da dove si esce.
  - **Riserva estrema, se il server resta giù a lungo:** sulla pagina di errore del proxy
    compare il tasto "Naviga diretto (riserva)", solo lì e mai nel menu normale. Prima di
    attivarlo chiede conferma e spiega che i siti vedranno l'IP di casa. La modalità diretta:
    - è temporanea e scade da sola (alla chiusura del browser, al massimo dopo 24 ore);
    - lascia l'indicatore rosso e una barra fissa "Connessione diretta: il tuo IP è visibile";
    - torna da sola al proxy, con un avviso, appena il server risponde di nuovo (controllo
      ogni pochi minuti).
  - Livello intermedio consigliato prima della riserva estrema: con il server guasto anche
    Tailscale non serve, quindi si può accendere l'app della VPN sul dispositivo. In
    modalità diretta il browser esce allora dalla VPN di sistema e l'IP di casa resta
    nascosto.
  - Elenco modificabile di siti che escono diretti (banche, servizi che bloccano gli IP delle
    VPN).
- **Coerenza:** uscita della VPN nello stesso paese di fuso orario e lingua del browser. Un IP
  estero con il fuso italiano è già di per sé un segnale per gli script antifrode. Il proxy non
  deve aggiungere intestazioni (`Via`, `X-Forwarded-For`): in gluetun `HTTPPROXY_STEALTH=on`.
- **Prova fatta:** proxy di gluetun raggiunto da LAN e Tailscale, uscita nel paese scelto,
  nessuna intestazione aggiunta, in casa circa 70 Mbit/s contro i 115 della connessione
  diretta. Fuori casa la velocità dipende dall'upload della connessione di casa.
- **iOS:** da iOS 17 WebKit accetta un proxy per il solo browser
  (`WKWebsiteDataStore.proxyConfigurations`).
- **Limite:** la VPN nasconde l'IP, non l'identità quando si fa login a un account.

## 8. Tema scuro dei siti (come Dark Reader)

Con il browser in modalità scura, i siti che non hanno un tema scuro diventano scuri da soli;
quelli che ce l'hanno usano il loro. Vale per tutte e tre le piattaforme.

- **Impostazione:** "Tema scuro dei siti": Automatico (predefinito: solo con il browser scuro e
  solo sui siti senza tema scuro), Sempre, Mai. Eccezioni per sito dal pannello informazioni sul
  sito ("Tema scuro del sito").
- **Mac e Windows:** Chromium ha già un motore nativo (Blink, flag `#enable-force-dark`, "Auto
  Dark Mode for Web Contents") che scurisce la pagina mentre la disegna e lascia stare foto e
  video. La patch lo trasforma in un'impostazione vera, legata al tema del browser, con le
  eccezioni per sito. Da verificare sulla build che salti davvero i siti che dichiarano un tema
  scuro (`color-scheme` o `prefers-color-scheme`); se non lo fa, quel controllo lo aggiungiamo noi.
- **Privacy:** il motore nativo lavora in fase di disegno, quindi la pagina non vede stili
  aggiunti. Un'estensione come Dark Reader invece inserisce i suoi stili nella pagina, e un sito
  può accorgersene: è un piccolo segnale di fingerprinting in più.
- **iOS:** WebKit non ha un motore equivalente. Serve uno script iniettato, partendo dal motore
  di Dark Reader (licenza MIT), in un mondo JavaScript separato dalla pagina. Gli stili restano
  comunque visibili alla pagina, quindi lì vale la regola "solo se il sito non ha un tema suo".

## 9. macOS

- Porting: Cromite non compila per Mac, le correzioni vanno in `patches/macos/`.
- **Tema "Liquid Glass" come Safari:** barra delle schede e degli strumenti traslucide con
  l'effetto vetro di sistema di macOS 26 (`NSGlassEffectView`) dietro la UI di Chromium,
  forme e spaziature in stile Safari, modalità chiara e scura, rispetto di "Riduci trasparenza".
- **Schede a scelta** nelle impostazioni: nella barra degli strumenti (compatte, come Safari) o in
  una riga separata sopra (come Chromium).
- **Firma e aggiornamenti:** firma Developer ID e notarizzazione Apple, così macOS la apre senza
  avvisi. Aggiornamenti con Sparkle, firmati con una nostra chiave EdDSA e mostrati in
  "Informazioni su".
- **La firma resta fuori dalla CI pubblica.** Il certificato Developer ID contiene il nome del
  titolare dell'account, e i log delle Actions e le release di questo repo sono pubblici. Quindi
  GitHub compila senza firmare, e firma, notarizzazione e feed di Sparkle stanno su un Mac e un
  server privati. Le build macOS firmate non vanno nelle release pubbliche.

## 10. Cookie: tre modalità e sincronizzazione

Una scelta nelle impostazioni, con eccezioni per sito dal pannello del sito ("Cookie e dati dei
siti"):

- **Blocca tutti:** nessun cookie e nessun dato salvato dai siti (anche `localStorage`,
  IndexedDB e cache dei service worker). È la più privata, ma i login non funzionano.
- **Temporanei** (predefinito, decisione): i cookie funzionano durante la visita e vengono
  cancellati, con tutti i dati del sito, **appena lasci il sito**: quando chiudi la scheda,
  oppure quando nella stessa scheda passi a un altro sito e nessun'altra scheda lo tiene aperto.
  - **Cancellazione istantanea** (decisione): appena lasci il sito tu (chiudi la scheda, scrivi
    un altro indirizzo, segui un link verso un altro sito o torni indietro verso un altro sito),
    i dati spariscono subito, senza attese.
  - **Unica attesa automatica: i giri che il sito stesso fa fare fuori e ritorno**, come il login
    con un account esterno o il pagamento che passa dalla pagina della banca. Si riconoscono
    perché l'uscita parte dal sito con un reindirizzamento del server o con l'invio di un modulo,
    non da un link diretto. In quel caso i dati restano finché non torni, al massimo 5 minuti; se
    nel frattempo chiudi la scheda o vai altrove, si cancellano subito. Senza questa eccezione
    il pagamento o il login fallirebbero al ritorno. Da verificare sui casi reali.
  - Per i siti dove vuoi restare collegato c'è "Ricorda questo sito" nel pannello. L'elenco dei
    siti da ricordare (per esempio iCloud) si decide sito per sito.
  - Base: il "Dimenticami quando chiudo il sito" di Brave (brave-core, MPL-2.0), esteso a tutti i
    siti. Chromium da solo cancella i dati di sessione solo alla chiusura del browser.
- **Normale:** come Chrome, più quello che Cromite fa già: tutti i cookie separati per sito
  principale (`Partitioning-all-cookies-by-top-frame-domain.patch`), memoria dei siti incorporati
  partizionata (`Enable-third-party-storage-partitioning.patch`) e cancellazione dei dati
  all'uscita come opzione (`Add-setting-to-clear-data-on-exit.patch`).

**Siti ricordati, uguali su tutti i dispositivi.** L'elenco è un'impostazione del browser che si
sincronizza con il sync sul proprio server (task 5), come le altre impostazioni. Un elenco iniziale
personale si importa al primo avvio da un file privato, fuori dal repo.

**Assistente all'accesso.** Quando il browser riconosce che hai fatto l'accesso a un sito non
ricordato, chiede con un fumetto accanto all'indirizzo (come per "Salva password") se vuoi restare
collegato, con un giudizio e il perché.

- **Come riconosce l'accesso:** le euristiche del gestore delle password di Chromium (modulo con
  la password inviato e poi sparito), più passkey/WebAuthn e il ritorno da un login esterno
  (Google, Apple e simili).
- **Come valuta, tutto in locale** (nessun sito visitato esce dal browser):
  - indirizzo simile a un marchio noto ma diverso (i controlli "lookalike" che Chromium ha già):
    possibile sito falso, quindi sconsigliato e con avviso;
  - il sito è anche una rete di tracciamento presente su molti altri siti (elenchi aperti come
    quello di Disconnect, GPL-3.0): si può ricordare, perché da noi i cookie restano separati per
    sito, ma l'assistente lo segnala;
  - quanti tracker ha cercato di caricare la pagina (li blocca già l'adblock);
  - che cookie ha salvato il sito: di accesso, di analisi o pubblicitari, riconosciuti per nome
    con l'Open Cookie Database (Apache-2.0).
- **Risposta:** "Sicuro da ricordare", "Ricordabile con attenzione" o "Sconsigliato", con i
  motivi, e i pulsanti "Ricorda" e "Non ricordare".
- **"Ricorda solo l'accesso":** tiene i cookie di accesso e cancella comunque quelli di analisi e
  pubblicitari, riconosciuti come sopra. Da provare sito per sito, perché alcuni siti legano
  l'accesso anche a cookie con nomi da analisi.

**Cookie centralizzati**, dopo il sync: sincronizzazione solo cifrata e solo per i siti
"ricordati" (alcuni siti legano la sessione al dispositivo e non funzionerebbero comunque). I
cookie temporanei non si sincronizzano mai.

## 11. Password di Apple

Le password restano nell'app Password di Apple (portachiavi iCloud). Il gestore di Chromium resta
spento come già in Cromite (`Do-not-store-passwords-by-default.patch`,
`Disable-privacy-issues-in-password-manager.patch`), così non ci sono doppioni.

- **macOS, integrazione nativa:** il browser parla direttamente con il programma di Apple che fa
  da ponte verso le password (`PasswordManagerBrowserExtensionHelper`), lo stesso che usa
  l'estensione ufficiale "Password di iCloud". Le password compaiono nel menu di compilazione di
  Chromium, quelle nuove si salvano nell'app Password, e la prima volta macOS mostra un codice di
  6 cifre da inserire nel browser. Il protocollo non è documentato: il riferimento è il progetto
  open source `au2001/icloud-passwords-firefox` (Apache-2.0).
- **Windows:** iCloud per Windows ha un ponte analogo, da verificare.
- **Riserva:** se Apple cambia il protocollo, l'estensione ufficiale "Password di iCloud",
  configurata in automatico. Il browser registra da solo il programma di Apple, cosa che su
  Brave e Vivaldi va fatta a mano.
- **Passkey:** su macOS il browser usa quelle del portachiavi tramite il sistema. Serve
  l'autorizzazione di Apple per i browser, da chiedere con l'account Developer per
  l'identificativo dell'app.
- **iOS:** niente da fare, le password di Apple compaiono sopra la tastiera come in ogni browser.
  Per le passkey serve l'autorizzazione da browser predefinito.
- L'assistente all'accesso (task 10) riconosce i login anche con il gestore di Chromium spento.

## 12. uBlock Origin ed estensioni

- **uBlock Origin integrato nel browser** al posto dell'adblock di Cromite (motore eyeo), perché è
  più efficace sui casi difficili: scriptlet contro i muri anti-adblock e la pubblicità nei video,
  filtri cosmetici avanzati, liste scritte per la sua sintassi.
  - **Integrato, non estensione:** gira come componente interno del browser, come il lettore
    PDF di Chromium. Non compare tra le estensioni e non si disinstalla per sbaglio.
  - **Impostazioni** nella sezione dove prima c'era l'adblock di Cromite ("Blocco contenuti"):
    liste di filtri, filtri personali, regole e siti esclusi di uBlock Origin dentro la pagina
    delle impostazioni.
  - **Icona a destra dell'indirizzo**, speculare allo scudo del fingerprinting a sinistra, con il
    numero di elementi bloccati nella pagina. Apre il pannello "Blocco contenuti": acceso o spento
    sul sito, bloccati e domini esterni, blocchi extra per sito (popup, media grandi, font
    esterni, JavaScript), "Nascondi un elemento", registro e impostazioni. Su iPhone l'icona
    prende il posto di "Ricarica" nella capsula dell'indirizzo (si ricarica tirando giù la
    pagina) e il pannello si apre dal basso.
  - Versione completa (Manifest V2): Cromite tiene ancora attive le estensioni MV2
    (`Override-the-MV2-deprecation.patch`).
  - Distribuito con il browser dalla release ufficiale su GitHub (GPL-3.0, come Cromite), non dal
    Chrome Web Store, così Google non lo vede. Il controllo giornaliero segue anche le release di
    uBlock Origin. Le liste di filtri si aggiornano da sole, passando dalla VPN.
  - L'adblock di Cromite resta disponibile ma spento di default, per non filtrare due volte.
  - **Piano B**, quando Chromium toglierà del tutto MV2 e la patch di Cromite non basterà più:
    il motore nativo `adblock-rust` di Brave (MPL-2.0), che legge le liste e gli scriptlet di
    uBlock Origin. Nel frattempo, uBlock Origin Lite (MV3).
- **Estensioni: solo quelle approvate.** Un elenco di estensioni ammesse, all'inizio solo uBlock
  Origin (più "Password di iCloud" se serve la riserva); le altre si valutano una per una. Spente
  di default in incognito. I motivi: fingerprinting (le estensioni che inseriscono elementi nelle
  pagine si vedono), sicurezza (accesso a tutti i siti, aggiornamenti che possono diventare
  malevoli) e il fatto che le funzioni più comuni sono già nel browser.

## 13. iOS

- Base: Chromium per iOS (WebKit, regola Apple).
- Funzioni di Cromite da reimplementare in `ios/`: adblock con le regole di contenuto di
  WebKit (partendo dalle liste di uBlock Origin convertite), randomizzazione con script iniettati come fa Brave iOS
  (`ios/browser/brave_shields/resources/farbling.ts`).
- **Tema "Liquid Glass" nativo** con gli effetti vetro di iOS 26 (`UIGlassEffect`).
- **Barra a scelta** nelle impostazioni: tutta in basso (indietro, indirizzo, menu), oppure in
  alto come Safari, con l'indirizzo in alto e i pulsanti (indietro, avanti, condividi, schede,
  menu) in una barra in basso.
- **Distribuzione: TestFlight** con l'account Apple Developer (test interno, senza revisione di
  Apple): gli aggiornamenti arrivano da soli e ogni build vale 90 giorni, più del mese tra un
  Chromium e l'altro. Come per macOS, firma e caricamento su App Store Connect avvengono su un
  Mac privato, mai nella CI pubblica. AltStore/SideStore restano l'alternativa senza account.

## 14. Claude integrato nel browser

Claude come parte del browser, non come estensione: lo stile di Claude in Chrome, ma con
l'interfaccia nativa (pannello laterale in vetro su macOS, foglio dal basso su iOS) e con funzioni
che un'estensione non può avere.

**Cosa fa**

- **Pannello "Claude"** (pulsante nella barra e scorciatoia da tastiera): domande sulla pagina
  aperta (riassunto, spiegazione, traduzione, con i rimandi ai punti della pagina), su più schede
  insieme ("confronta queste tre offerte") e sui PDF aperti.
- **Barra degli indirizzi:** "Chiedi a Claude" come voce accanto alla ricerca (SearXNG resta il
  motore predefinito), con la risposta nel pannello.
- **Testo selezionato:** "Spiega", "Traduci", "Riassumi", "Chiedi a Claude".
- **Scrittura:** "Aiutami a scrivere" nei campi di testo (riscrivere, cambiare tono, tradurre,
  rispondere a un'email). Mai nei campi di password e carte.
- **Agente "Fallo tu":** Claude naviga, clicca, compila e confronta pagine al posto tuo, come
  Claude in Chrome, in un gruppo di schede dedicato e visibile, con "Ferma" sempre in vista.
- **Schede, cronologia e segnalibri in linguaggio naturale:** "raggruppa le schede per argomento",
  "ritrova la ricetta che ho visto martedì". La ricerca nella cronologia gira in locale; a Claude
  arrivano solo i risultati che scegli di mandare.
- **Le tue chat di claude.ai:** claude.ai dentro il pannello, con il tuo account, quindi chat,
  progetti e artifact come nell'app. Non risulta un'API ufficiale per leggere le chat di claude.ai,
  quindi niente scorciatoie non ufficiali: le chat si usano da claude.ai stesso.
- **Ponte con gli altri Claude:** il browser espone i suoi strumenti (leggere una pagina, cliccare,
  compilare, fare screenshot) come server MCP locale, così Claude Code e l'app Claude sul Mac
  possono usarlo, come già fa Claude in Chrome.
- **Voce:** dettatura e lettura delle risposte con i servizi vocali del sistema.

**Come funziona**

- **Due canali, perché si pagano in modo diverso:** claude.ai (abbonamento) per le chat nel
  pannello; l'API di Anthropic (chiave API, a consumo) per tutto ciò che legge o usa il browser.
  La chiave sta nel portachiavi del sistema, mai in chiaro.
- **Modelli:** Claude Opus 5.5 (`claude-opus-5-5`) per l'agente e le domande difficili. Per i
  compiti veloci e frequenti (riassunti, suggerimenti di scrittura) si può valutare Claude Haiku
  5.5 (`claude-haiku-5-5`), molto più economico: da decidere misurando la qualità.
- **Strumenti dell'agente** scritti in C++ nel browser e dati a Claude con il tool use: l'albero di
  accessibilità della pagina (testo e riferimenti agli elementi, più affidabile e più economico
  degli screenshot), clic e testo come eventi di input veri, navigazione, schede. Screenshot e
  `computer_toolset_20260801` solo per le pagine disegnate in canvas.
- **Prompt caching** sul prompt di sistema e sugli strumenti, che restano fissi.

**Privacy e sicurezza (regole fisse)**

- Niente parte senza che tu lo chieda: nessuna analisi delle pagine in background.
- Esclusi di default: banche, sanità, pagine con campi di password o carte, navigazione in
  incognito. Elenco modificabile.
- Le richieste passano dalla VPN come il resto del browser.
- Il pannello è interfaccia del browser, non uno script nella pagina: i siti non vedono che Claude
  c'è, quindi nessun segnale di fingerprinting in più.
- **Istruzioni nascoste nelle pagine (prompt injection):** il contenuto delle pagine arriva a
  Claude come dato, mai come istruzione. Conferma esplicita prima di acquisti, pagamenti, invio di
  messaggi o email, invio di moduli, download, cancellazioni e accessi a siti nuovi. L'agente
  lavora solo sui siti del compito e si ferma a chiedere quando una pagina contiene istruzioni.
- Registro di ogni azione dell'agente, leggibile dal pannello.
- **iOS:** pannello come foglio dal basso. L'agente lì passa da script iniettati in WebKit, quindi
  è più limitato e visibile ai siti: si parte dalle funzioni di lettura e scrittura.
- **Ponte temporaneo:** finché non è pronto, l'estensione ufficiale Claude in Chrome tra le
  estensioni approvate, se funziona sui browser basati su Chromium.

## Windows

- Tema: quello standard di Chromium, nessuna personalizzazione.
- Aggiornamento in-app da `update.json` (Cromite su Windows disattiva l'updater di Chromium),
  dalla pagina "Informazioni su": pacchetto firmato con una nostra chiave e verificato prima di
  installarlo, sostituzione dei file al riavvio, versione precedente tenuta per tornare indietro.
