# NomeBrowser

Il nostro browser basato su [Cromite](https://github.com/uazo/cromite) (di uazo), a sua volta
basato su Chromium, per **Windows, macOS e iOS**. Dove Cromite non arriva (macOS, iOS) lo
implementiamo noi. `NomeBrowser` è un nome provvisorio: si cambia in `fork.json` e nella patch
di branding.

## La regola: nessuna modifica si perde negli aggiornamenti

Le nostre modifiche non stanno in un fork dei sorgenti, ma come patch in `patches/`, applicate
sopra Cromite nell'ordine di `patches/series`. È lo stesso schema che Cromite usa sopra Chromium.

- Ogni giorno la CI controlla se Cromite ha una nuova release stabile e **riapplica tutte le
  nostre patch con un vero rebase git a 3 vie**: le modifiche si spostano da sole quando il
  codice intorno cambia.
- I conflitti risolti una volta vengono **ricordati** (`git rerere`, in `state/rr-cache/`) e
  riapplicati in automatico le volte successive.
- Se una patch non si riapplica, l'aggiornamento **si ferma** e apre una issue con il conflitto.
  Niente viene compilato o pubblicato con patch mancanti.
- Se una patch diventa vuota (Cromite o Chromium hanno già la stessa modifica), si ferma e chiede
  conferma; solo su conferma la patch va in `patches/retired/`, mai cancellata.
- `forkctl export` si rifiuta di togliere patch dalla serie se non lo chiedi esplicitamente.
- Ogni aggiornamento lascia un report in `reports/` con il confronto delle patch prima/dopo
  (`git range-diff`), e arriva come PR: su GitHub vedi il diff di ogni patch fra le versioni.

## Come funziona

```
Chromium (tag di build/RELEASE)  →  patch di Cromite (cromite_patches_list.txt)  →  le nostre patch
```

Per lavorare sulle patch non serve il checkout completo di Chromium (~100 GB): `forkctl`
costruisce un **mini-albero** con i soli file toccati dalle nostre patch, li scarica da Gitiles
alla versione giusta (sottomoduli che Cromite incorpora compresi, come `v8/`) e ci applica sopra
solo le parti delle patch di Cromite che li riguardano. Così un aggiornamento gira in pochi
minuti su un Mac qualsiasi o su un runner GitHub gratuito. Il checkout completo serve solo agli
host che compilano.

## Struttura

```
fork.json            nome del fork e sorgenti upstream
upstream.lock        tag Cromite e versione Chromium su cui siamo
patches/series       ordine delle nostre patch
patches/common/      patch per tutte le piattaforme
patches/windows/     patch solo Windows      (titolo del commit "win: ...")
patches/macos/       patch solo macOS        (titolo del commit "mac: ...")
patches/ios/         patch solo iOS          (titolo del commit "ios: ...")
patches/retired/     patch ritirate (già contenute upstream o tolte di proposito)
overlay/             file binari copiati sopra i sorgenti (icone, ecc.)
gn_args/             argomenti di build nostri, sopra quelli di Cromite
state/rr-cache/      memoria delle risoluzioni dei conflitti
reports/             report di ogni aggiornamento
tools/forkctl.py     lo strumento che gestisce tutto
tools/build/         script di build completa per piattaforma
tests/               test offline di forkctl
```

## Uso

Serve solo `python3` e `git` (2.45+ consigliato). I comandi si lanciano dalla cartella del repo.

### Scrivere una patch nuova

```bash
python3 tools/forkctl.py workspace --add chrome/browser/percorso/del/file.cc
```

Apre in `.work/` il mini-albero con le patch esistenti già applicate. Modifica i file, poi crea
un commit per ogni patch (con prefisso `win:`, `mac:` o `ios:` nel titolo se è di piattaforma):

```bash
git -C .work commit -am "mac: Abilita la barra laterale nativa"
python3 tools/forkctl.py export
python3 tools/forkctl.py verify
```

Altri file da toccare a workspace aperto: `python3 tools/forkctl.py add percorso/file`.
Per modificare una patch esistente: correggi il suo commit in `.work` (`git commit --fixup` +
`git rebase -i --autosquash` o simili) e riesporta.

### Aggiornare a mano (la CI lo fa da sola)

```bash
python3 tools/forkctl.py check                 # c'è un Cromite nuovo?
python3 tools/forkctl.py update                # riapplica le patch sull'ultima release
python3 tools/forkctl.py continue              # dopo aver risolto un conflitto in .work/
python3 tools/forkctl.py continue --drop-empty # ritira una patch diventata vuota
python3 tools/forkctl.py abort                 # annulla, patches/ resta com'era
```

`update` termina con 0 (fatto), 2 (conflitto) o 3 (patch diventata vuota). In caso di conflitto
i file in `.work/` hanno i marcatori con le tre versioni (upstream nuovo, base vecchia, nostra).

### Test

```bash
python3 tests/test_forkctl.py
```

## Privacy

Il repo è pubblico: dentro ci sono solo le patch e gli strumenti, mai dati personali.

- Le funzioni che usano server personali (per esempio il sync su un NAS) si configurano nelle
  impostazioni del browser, su ogni dispositivo: indirizzi e credenziali non vanno nelle patch.
- Le patch sono firmate con l'identità pubblica di `fork.json` (`patch_author`), qualunque sia
  la configurazione git del computer; `verify` rifiuta patch con un autore diverso.
- `python3 tools/forkctl.py privacy-check` cerca email non anonime e indirizzi di rete privati
  (casa e Tailscale) in file, messaggi e autori dei commit. Gira in CI a ogni push e prima di ogni
  aggiornamento automatico. Una riga voluta si segna con il commento `privacy: ok`.
- Parole da non pubblicare mai (nomi, indirizzi): vanno in `.privacy-denylist`, che è escluso
  da git e resta solo sul tuo computer; `privacy-check` le cerca come parole intere.
- Conviene usare `privacy-check` anche come hook `pre-push` locale (`.git/hooks/pre-push`).

## Automazione su GitHub

| workflow | quando | cosa fa |
|---|---|---|
| `upstream.yml` | ogni giorno + a mano | nuovo Cromite → `update` → PR + merge + build; se serve una decisione, issue |
| `verify.yml` | push e PR | test di forkctl + `verify` sulla versione bloccata |
| `build.yml` | dopo ogni aggiornamento + a mano | build e release con `update.json`: Windows sui server GitHub (`build-windows.yml`, a tappe), macOS/iOS su un Mac self-hosted |

Da configurare nel repo GitHub:

- *Settings → Actions → General*: "Allow GitHub Actions to create and approve pull requests".
- *Settings → Variables*:
  - `AUTO_MERGE_CLEAN`: `false` per rivedere a mano anche gli aggiornamenti puliti (default `true`).
  - `MAC_BUILD_HOST`: `true` quando è collegato un Mac self-hosted (etichetta aggiuntiva
    `chromium`) per macOS e iOS.
- Windows non richiede nulla: usa i runner gratuiti di GitHub (il repo è pubblico).

## Piattaforme

| | base Cromite | stato |
|---|---|---|
| **Windows x64** | supportata | compilata sui runner Windows gratuiti di GitHub con gli strumenti di Chromium e il Visual Studio già installato lì (`build-windows.yml`, `tools/build/gh_windows_stage.py`): fino a 10 tappe da ~5 ore, ognuna riparte dalla cartella di build della precedente. In alternativa c'è il cross-build da Linux su un host nostro (`tools/build/build-windows.sh`, richiede `ACCEPT_MSVC_LICENSE=1` perché scarica il toolchain MSVC con msvc-wine). |
| **macOS** | non supportata | portiamo noi: `tools/build/build-macos.sh`, e le correzioni in `patches/macos/`. La prima build dirà cosa di Cromite non compila su Mac. Firma ad-hoc: per darla ad altri senza avvisi di Gatekeeper serve un Apple Developer ID + notarizzazione. |
| **iOS** | non supportata | su iOS Chromium usa WebKit (regola Apple), quindi le funzioni di Cromite (adblock, privacy) vanno reimplementate nel codice `ios/`. `tools/build/build-ios.sh` produce un `.ipa` non firmato, da installare firmandolo (Apple Developer, AltStore/SideStore). |

Aggiornamento automatico del browser sui dispositivi: ogni release pubblica `update.json` con
versione e link dei pacchetti. Cromite su Windows disattiva l'updater di Chromium; il passo
successivo è una nostra patch che legga quel manifest (Windows/macOS) e, per iOS, una sorgente
AltStore generata dalla release.

### Host di build

Chromium completo richiede ~32 GB di RAM (16 al minimo), ~250 GB di disco e ore di CPU.
Windows si compila da Linux x86-64; macOS e iOS solo su un Mac con Xcode completo.
Variabili: `FORK_BUILD_DIR` (default `~/fork-build`), `FORK_DIST_DIR`, `FORK_KEEP_OLD=1`.

## Cosa è verificato

- `forkctl` è provato su dati reali: patch scritte su Cromite 148 e riapplicate su 153, con una
  modifica che si sposta da sola di centinaia di righe, un conflitto vero risolto e poi
  riapplicato in automatico da rerere, e una patch diventata vuota ritirata solo su conferma.
- I test offline coprono gli stessi casi più l'applicazione su un checkout completo finto
  (incorporazione dei sottomoduli come fa Cromite, patch Cromite, nostre patch, overlay).
- La build Windows a tappe su GitHub è nuova: la prima esecuzione va seguita da vicino.
  Gli script per macOS, iOS e il cross-build Windows da Linux non sono ancora stati eseguiti.
