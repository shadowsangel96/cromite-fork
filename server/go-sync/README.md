# Server di sync

Il server con cui Shannon sincronizza i dati tra i propri dispositivi: è
[go-sync](https://github.com/brave/go-sync) di Brave (MPL-2.0), lo stesso protocollo di
Chromium, con l'accesso a chiave di Brave al posto dell'account Google. Gira in Docker sul
proprio NAS o server e va raggiungibile solo dalla rete di casa e da Tailscale.

Cosa aggiunge Shannon (`patches/`):

- **Solo la propria catena.** go-sync accetta qualunque dispositivo che si firmi da solo con
  una chiave ed25519. Con `ALLOWED_CLIENT_IDS` entra solo la catena elencata (la chiave
  pubblica che si ricava dal codice di sync); le altre vengono rifiutate e la loro chiave
  finisce nel log, così una catena nuova si aggiunge solo di proposito. Senza elenco il
  container non parte.

Il resto:

- `Dockerfile`: scarica go-sync a un commit fisso, applica le patch, ne esegue i test e
  aggiunge `crea-tabella`, che prepara la tabella di DynamoDB Local al primo avvio.
- `docker-compose.yml`: go-sync, DynamoDB Local (i dati, nel volume `dati`) e Redis (solo
  cache). Le impostazioni stanno in `.env`, sul server e mai nel repo (`.env.example`).
- `prova.py`: prova dall'esterno su un server di prova (`NOME=go-sync-prova`, un'altra
  porta): catena ammessa che legge i dati, catena estranea, token scaduto e firma sbagliata
  rifiutati.

## Installazione

```bash
# sul server, in una cartella con questi file
cp .env.example .env        # poi ALLOWED_CLIENT_IDS e PORTA
docker compose up -d --build
docker compose logs sync    # le catene rifiutate: "client ID not allowed: <chiave>"
```

Nel browser l'indirizzo del server è `http://<indirizzo del server>:<porta>/v2`.
