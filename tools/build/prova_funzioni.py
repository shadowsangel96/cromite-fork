#!/usr/bin/env python3
"""Prova delle nostre funzioni sul browser compilato: impostazioni, cookie temporanei, giri di
login, siti da ricordare, VPN, motore personale, WebGL spento. Dura una decina di minuti, perché
aspetta anche i 5 minuti dei giri di login.

Profilo temporaneo e finestra invisibile: non tocca i dati di chi la lancia. I siti sono finti, un
server e un proxy locali (--host-resolver-rules): non esce niente verso la rete.

uso: prova_funzioni.py <eseguibile del browser> [cartella per lo screenshot della pagina]
"""
import base64, http.server, json, os, shutil, socket, ssl, subprocess, sys, tempfile, threading, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser

EXE = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else None
OPENSSL = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
TMP = tempfile.mkdtemp(prefix="prova-")
PROFILE = os.path.join(TMP, "profilo")
FLAGS = ["--use-mock-keychain", "--headless=new", "--window-size=1000,1500", "--ignore-certificate-errors",
         "--host-resolver-rules=MAP * 127.0.0.1"]
hits, proxy_log = [], []
fails = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'NO  '} {name}{'  [' + str(detail) + ']' if detail != '' and not ok else ''}", flush=True)
    if not ok:
        fails.append(name)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.do_GET()

    def do_GET(self):
        hits.append((self.headers.get("Host", ""), self.path))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if self.path.startswith("/redir"):
            self.send_response(302)
            self.send_header("Location", q["to"][0])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        extra = ""
        if self.path.startswith("/set"):
            extra = "<script>localStorage.setItem('prova','1')</script>"
        if self.path.startswith("/form"):
            extra = f"<form method=post action='{q['to'][0]}'><button>via</button></form>"
        body = f"<!doctype html><title>t</title><pre id=c>{self.headers.get('Cookie', '')}</pre>{extra}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        if self.path.startswith("/set"):
            self.send_header("Set-Cookie", "prova=1; Max-Age=86400; Path=/")
        self.end_headers()
        self.wfile.write(body)


subprocess.run([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=prova", "-days", "2",
                "-keyout", f"{TMP}/k.pem", "-out", f"{TMP}/c.pem"], capture_output=True, check=True)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(f"{TMP}/c.pem", f"{TMP}/k.pem")
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
P = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def pump(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    for s in (a, b):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def proxy_conn(c):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = c.recv(4096)
        if not d:
            return
        buf += d
    proxy_log.append(buf.split(b"\r\n")[0].decode())
    up = socket.create_connection(("127.0.0.1", P))
    c.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
    threading.Thread(target=pump, args=(c, up), daemon=True).start()
    pump(up, c)


psrv = socket.socket()
psrv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
psrv.bind(("127.0.0.1", 0))
psrv.listen(32)
PX = psrv.getsockname()[1]


def proxy_loop():
    while True:
        c, _ = psrv.accept()
        threading.Thread(target=proxy_conn, args=(c,), daemon=True).start()


threading.Thread(target=proxy_loop, daemon=True).start()


def url(site, path="/"):
    return f"https://{site}:{P}{path}"


def wait_page(t, prefix, secs=25):
    end = time.time() + secs
    last = None
    while time.time() < end:
        try:
            last = t.eval("location.href + '|' + document.readyState", timeout=5)
            if isinstance(last, str) and last.startswith(prefix) and last.endswith("|complete"):
                return True
        except Exception as e:
            last = repr(e)
        time.sleep(0.3)
    print("     (pagina non pronta:", prefix, "->", str(last)[:120], ")", flush=True)
    return False


def nav(t, target, expect=None):
    t.send("Page.navigate", url=target)
    return wait_page(t, expect or target)


def js_nav(t, target, expect=None):
    t.eval(f"setTimeout(()=>{{location.href={json.dumps(target)}}},0);1")
    return wait_page(t, expect or target)


seen_partition = []


def cookies(ctrl, site):
    # tutti i cookie del profilo: Network.getCookies non vede quelli separati per sito
    r = ctrl.send("Network.getAllCookies")
    if "result" not in r:
        raise RuntimeError(r)
    out = []
    host = site.removeprefix("www.")
    for c in r["result"]["cookies"]:
        if c["domain"].lstrip(".").endswith(host):
            if "partitionKey" in c and not seen_partition:
                seen_partition.append(c["partitionKey"])
                print("     cookie separato per sito:", c["partitionKey"], flush=True)
            out.append(c["name"])
    return out


def gone(ctrl, site, secs=8):
    end = time.time() + secs
    while time.time() < end:
        if not cookies(ctrl, site):
            return True
        time.sleep(0.25)
    return False


def kept(ctrl, site, secs=3):
    time.sleep(secs)
    return bool(cookies(ctrl, site))


def proxy_page(b):
    t = b.open("chrome://proxy")
    wait_page(t, "chrome://proxy")
    time.sleep(1)
    return t


def set_mode(b, mode):
    t = proxy_page(b)
    t.eval(f"document.querySelector('input[name=fork-cookie-mode][value=\"{mode}\"]').click();1")
    time.sleep(1.5)
    b.close_tab(t)


def set_remembered(b, *sites):
    t = proxy_page(b)
    t.eval(f"document.getElementById('fork-remembered').value={json.dumps(chr(10).join(sites))};"
           "document.getElementById('fork-remembered-save').click();1")
    time.sleep(1.5)
    b.close_tab(t)


b = Browser(EXE, 9445, PROFILE, *FLAGS)
ctrl = b.open("about:blank")
time.sleep(1)

print("--- pagine", flush=True)
for page, needle in [("chrome://settings", "settings-ui"), ("chrome://settings/help", "settings-ui"),
                     ("chrome://version", None), ("chrome://flags", "flags-app"), ("chrome://proxy", None)]:
    t = b.open(page)
    probe = f"!!document.querySelector({json.dumps(needle)})" if needle else "document.body.innerText.length>0"
    n = None
    for _ in range(60):
        try:
            n = t.eval(probe, timeout=8)
        except Exception as e:
            n = repr(e)
        if n is True:
            break
        time.sleep(0.5)
    check(f"{page} si apre", n is True and not t.crashed(), n)
    b.close_tab(t)
check("il browser è ancora vivo dopo le pagine", b.alive())

print("--- webgl e pagina", flush=True)
t = b.open(url("sito-gl.test"))
wait_page(t, url("sito-gl.test"))
check("WebGL spento di default", t.eval("!document.createElement('canvas').getContext('webgl')") is True)
check("WebGL2 spento di default", t.eval("!document.createElement('canvas').getContext('webgl2')") is True)
b.close_tab(t)
t = proxy_page(b)
txt = t.eval("document.getElementById('fork-settings').innerText")
check("la pagina Rete e privacy mostra le voci", all(s in str(txt) for s in ["VPN personale", "Motore di ricerca personale", "Cookie e dati dei siti", "Siti da ricordare"]), str(txt)[:100])
check("modalità di partenza: temporanei", t.eval("document.querySelector('input[name=fork-cookie-mode]:checked').value") == "0")
shot = t.send("Page.captureScreenshot", format="png", timeout=30)
if OUT and "result" in shot:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "rete_e_privacy.png"), "wb") as f:
        f.write(base64.b64decode(shot["result"]["data"]))
b.close_tab(t)

print("--- cookie temporanei", flush=True)
t = b.open(url("sito-a.test", "/set"))
wait_page(t, url("sito-a.test"))
check("il sito salva il cookie mentre sei sopra", bool(cookies(ctrl, "sito-a.test")))
check("il sito salva localStorage", t.eval("localStorage.getItem('prova')") == "1")
nav(t, url("sito-b.test"))
check("lasciato il sito, il cookie sparisce", gone(ctrl, "sito-a.test"))
nav(t, url("sito-a.test"))
check("tornando non c'è più localStorage", t.eval("localStorage.getItem('prova')") is None, t.eval("localStorage.getItem('prova')"))
check("tornando il server non riceve cookie", t.eval("document.getElementById('c').textContent") == "")
b.close_tab(t)

t1 = b.open(url("sito-c.test", "/set"))
wait_page(t1, url("sito-c.test"))
t2 = b.open(url("sito-c.test", "/"))
wait_page(t2, url("sito-c.test"))
nav(t1, url("sito-b.test"))
check("con un'altra scheda aperta sul sito i dati restano", kept(ctrl, "sito-c.test"))
b.close_tab(t2)
check("chiusa l'ultima scheda del sito, i dati spariscono", gone(ctrl, "sito-c.test"))
b.close_tab(t1)

print("--- giri di login (i dati restano mentre vai e torni)", flush=True)
t = b.open(url("sito-d.test", "/set"))
wait_page(t, url("sito-d.test"))
js_nav(t, url("login-d.test", "/set"))
check("reindirizzato da una pagina, il sito tiene i dati", kept(ctrl, "sito-d.test"))
js_nav(t, url("sito-d.test"))
check("tornato dal login, sei ancora collegato", t.eval("document.getElementById('c').textContent") == "prova=1", t.eval("document.getElementById('c').textContent"))
nav(t, url("sito-z.test"))
check("lasciato davvero, i dati spariscono", gone(ctrl, "sito-d.test"))
b.close_tab(t)

t = b.open(url("sito-e.test", "/set"))
wait_page(t, url("sito-e.test"))
nav(t, url("sito-e.test", "/redir?to=" + urllib.parse.quote(url("login-e.test"))), url("login-e.test"))
check("redirect del server: il sito tiene i dati", kept(ctrl, "sito-e.test"))
b.close_tab(t)

t = b.open(url("sito-f.test", "/set"))
wait_page(t, url("sito-f.test"))
nav(t, url("login-f.test", "/?redirect_uri=x"))
check("indirizzo con redirect_uri: il sito tiene i dati", kept(ctrl, "sito-f.test"))
b.close_tab(t)

t = b.open(url("sito-p.test", "/set"))
wait_page(t, url("sito-p.test"))
nav(t, url("sito-p.test", "/form?to=" + urllib.parse.quote(url("login-p.test"))))
t.eval("setTimeout(()=>document.forms[0].submit(),0);1")
wait_page(t, url("login-p.test"))
check("modulo inviato a un altro sito: il sito tiene i dati", kept(ctrl, "sito-p.test"))
b.close_tab(t)

print("--- siti da ricordare", flush=True)
set_remembered(b, "ricorda-a.test", "https://www.ricorda-b.test/pagina")
t = b.open(url("www.ricorda-a.test", "/set"))
wait_page(t, url("www.ricorda-a.test"))
nav(t, url("sito-z.test"))
check("sito da ricordare: i dati restano", kept(ctrl, "www.ricorda-a.test"))
nav(t, url("ricorda-b.test", "/set"))
nav(t, url("sito-z.test"))
check("elenco con un indirizzo intero: funziona lo stesso", kept(ctrl, "ricorda-b.test"))
b.close_tab(t)

print("--- modalità", flush=True)
set_mode(b, 1)
t = b.open(url("normale.test", "/set"))
wait_page(t, url("normale.test"))
nav(t, url("sito-z.test"))
check("Normale: i dati restano", kept(ctrl, "normale.test"))
b.close_tab(t)
set_mode(b, 0)
check("tornando a Temporanei si cancella quello rimasto", gone(ctrl, "normale.test"))
check("ma i siti da ricordare restano", kept(ctrl, "www.ricorda-a.test", 0))
set_mode(b, 2)
t = b.open(url("bloccato.test", "/set"))
wait_page(t, url("bloccato.test"))
check("Blocca tutti: nessun cookie", not kept(ctrl, "bloccato.test", 2))
nav(t, url("ricorda-a.test", "/set"))
check("Blocca tutti: i siti da ricordare funzionano", kept(ctrl, "ricorda-a.test", 1))
b.close_tab(t)
set_mode(b, 0)
t = b.open(url("sblocco.test", "/set"))
wait_page(t, url("sblocco.test"))
check("di nuovo Temporanei: i cookie tornano a funzionare", bool(cookies(ctrl, "sblocco.test")))
b.close_tab(t)

print("--- VPN", flush=True)
t = proxy_page(b)
check("VPN spenta all'inizio", "Spenta" in str(t.eval("document.getElementById('fork-vpn-status').textContent")))
t.eval(f"document.getElementById('fork-vpn-address').value='127.0.0.1:{PX}';document.getElementById('fork-vpn-on').click();1")
time.sleep(2)
check("VPN attiva nella pagina", "Attiva" in str(t.eval("document.getElementById('fork-vpn-status').textContent")), t.eval("document.getElementById('fork-vpn-status').textContent"))
b.close_tab(t)
t = b.open("about:blank")
proxy_log.clear()
hits.clear()
nav(t, url("via-vpn.test"))
check("con la VPN le pagine passano dal proxy", any("via-vpn.test" in l for l in proxy_log), proxy_log[:3])
proxy_log.clear()
nav(t, url("server.ts.net"))
check("le reti Tailscale restano fuori dalla VPN", not any("ts.net" in l for l in proxy_log) and any("ts.net" in h[0] for h in hits), (proxy_log[:3], hits[-2:]))
b.close_tab(t)
t = proxy_page(b)
t.eval("document.getElementById('fork-vpn-address').value='127.0.0.1:1';document.getElementById('fork-vpn-on').click();1")
time.sleep(2)
b.close_tab(t)
t = b.open("about:blank")
hits.clear()
t.send("Page.navigate", url=url("vpn-spenta.test"))
time.sleep(5)
check("VPN che non risponde: nessuna uscita diretta", not any("vpn-spenta" in h[0] for h in hits), hits[-3:])
b.close_tab(t)
t = proxy_page(b)
t.eval("document.getElementById('fork-vpn-off').click();1")
time.sleep(2)
check("VPN spenta di nuovo", "Spenta" in str(t.eval("document.getElementById('fork-vpn-status').textContent")))
b.close_tab(t)
t = b.open("about:blank")
proxy_log.clear()
nav(t, url("senza-vpn.test"))
check("VPN spenta: le pagine escono normalmente", not proxy_log and any("senza-vpn" in h[0] for h in hits), proxy_log[:3])
b.close_tab(t)


def engines():
    t = b.open("chrome://settings/search")
    wait_page(t, "chrome://settings/search")
    time.sleep(2)
    r = t.eval("import('chrome://resources/js/cr.js').then(m=>m.sendWithPromise('getSearchEnginesList'))"
               ".then(l=>JSON.stringify(l.defaults.concat(l.actives).concat(l.others).map(e=>({n:e.name,k:e.keyword,d:e.default,u:e.url}))))", timeout=15)
    b.close_tab(t)
    return json.loads(r) if isinstance(r, str) else r


print("--- SearXNG", flush=True)
before = engines()
print("     motore di partenza:", [e["n"] for e in before if e.get("d")], flush=True)
t = proxy_page(b)
t.eval("document.getElementById('fork-search-url').value='https://cerca.prova:8080/';document.getElementById('fork-search-on').click();1")
time.sleep(2)
check("SearXNG attivo nella pagina", "Attivo" in str(t.eval("document.getElementById('fork-search-status').textContent")), t.eval("document.getElementById('fork-search-status').textContent"))
b.close_tab(t)
now = engines()
d = [e for e in now if e.get("d")]
check("SearXNG è il motore predefinito", bool(d) and d[0]["k"] == "searxng" and "cerca.prova:8080/search?q=" in d[0]["u"], d)
t = proxy_page(b)
t.eval("document.getElementById('fork-search-off').click();1")
time.sleep(2)
check("SearXNG tolto dalla pagina", "Spento" in str(t.eval("document.getElementById('fork-search-status').textContent")))
b.close_tab(t)
now = engines()
d = [e for e in now if e.get("d")]
check("tolto SearXNG c'è un altro motore predefinito", bool(d) and d[0]["k"] != "searxng" and not any(e["k"] == "searxng" for e in now), d)
print("     motore dopo:", [e["n"] for e in d], flush=True)
t = proxy_page(b)
t.eval("document.getElementById('fork-search-url').value='https://cerca.prova:8080/';document.getElementById('fork-search-on').click();1")
time.sleep(1.5)
t.eval(f"document.getElementById('fork-vpn-address').value='127.0.0.1:{PX}';document.getElementById('fork-vpn-on').click();1")
time.sleep(1.5)
b.close_tab(t)

print("--- riavvio (dopo un'uscita forzata)", flush=True)
t = b.open(url("rimasto.test", "/set"))
wait_page(t, url("rimasto.test"))
check("cookie presente prima dell'uscita forzata", bool(cookies(ctrl, "rimasto.test")))
print("     aspetto 35 s che il cookie finisca su disco", flush=True)
time.sleep(35)
b.kill()
time.sleep(3)
b = Browser(EXE, 9445, PROFILE, *FLAGS)
ctrl = b.open("about:blank")
time.sleep(4)
check("all'avvio si cancella quello che era rimasto", gone(ctrl, "rimasto.test", 10), cookies(ctrl, "rimasto.test"))
check("all'avvio i siti da ricordare restano", bool(cookies(ctrl, "ricorda-b.test")))
t = proxy_page(b)
check("dopo il riavvio la VPN è ancora attiva", "Attiva" in str(t.eval("document.getElementById('fork-vpn-status').textContent")))
check("dopo il riavvio SearXNG è ancora attivo", "Attivo" in str(t.eval("document.getElementById('fork-search-status').textContent")))
check("dopo il riavvio l'elenco è ancora lì", "ricorda-a.test" in str(t.eval("document.getElementById('fork-remembered').value")))
t.eval("document.getElementById('fork-vpn-off').click();1")
time.sleep(1.5)
b.close_tab(t)

print("--- scadenza dei 5 minuti", flush=True)
t = b.open(url("attesa.test", "/set"))
wait_page(t, url("attesa.test"))
js_nav(t, url("altro.test"))
check("giro di login: i dati restano all'inizio", kept(ctrl, "attesa.test"))
print("     aspetto 5 minuti e 15 s", flush=True)
time.sleep(315)
check("dopo 5 minuti senza ritorno, i dati spariscono", gone(ctrl, "attesa.test", 5), cookies(ctrl, "attesa.test"))
check("il browser è ancora vivo alla fine", b.alive())

b.close()
with open(PROFILE + ".log", errors="replace") as f:
    log = f.read()
for needle in ("FATAL", "Check failed"):
    check(f"nessun {needle} nel log", needle not in log, [l for l in log.splitlines() if needle in l][:2])
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nFINE: {len(fails)} problemi", fails, flush=True)
sys.exit(1 if fails else 0)
