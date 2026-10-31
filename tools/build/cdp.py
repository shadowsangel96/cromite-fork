"""Client minimo del DevTools Protocol, solo libreria standard, per provare il browser.
uso: Browser(exe, porta, profilo, *flag); .open(url) dà una Tab; tab.eval(js), tab.send(metodo)."""
import base64, json, os, signal, socket, struct, subprocess, sys, tempfile, time, urllib.parse, urllib.request

class Tab:
    def __init__(self, ws_url):
        u = urllib.parse.urlparse(ws_url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.id = 0
        self.events = []

    def _recv(self, n):
        while len(self.buf) < n:
            d = self.s.recv(65536)
            if not d:
                raise ConnectionError("chiuso")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self):
        b0, b1 = self._recv(2)
        n = b1 & 127
        if n == 126:
            n = struct.unpack(">H", self._recv(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recv(8))[0]
        return b0 & 15, self._recv(n)

    def send(self, method, timeout=20, **params):
        self.id += 1
        data = json.dumps({"id": self.id, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))
        end = time.time() + timeout
        self.s.settimeout(timeout)
        while time.time() < end:
            op, payload = self._frame()
            if op != 1:
                continue
            m = json.loads(payload)
            if m.get("id") == self.id:
                return m
            self.events.append(m)
        raise TimeoutError(method)

    def eval(self, js, timeout=20):
        r = self.send("Runtime.evaluate", timeout=timeout, expression=js, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("result", {}).get("value", r)

    def crashed(self):
        return any(e.get("method") == "Inspector.targetCrashed" for e in self.events)


class Browser:
    def __init__(self, exe, port=9444, profile=None, *flags, env=None):
        self.port = port
        self.profile = profile or tempfile.mkdtemp()
        self.p = subprocess.Popen([exe, f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
                                   "--no-first-run", "--no-default-browser-check", "--enable-logging=stderr", "--v=0",
                                   *flags, "about:blank"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=open(os.path.join(self.profile + ".log"), "w"), start_new_session=True, env=env)
        end = time.time() + 120
        while time.time() < end:
            try:
                self.get("json/version")
                return
            except Exception:
                time.sleep(0.3)
        raise TimeoutError("il browser non risponde")

    def get(self, path, method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/{path}", method=method)
        return json.load(urllib.request.urlopen(req, timeout=5))

    def open(self, url, wait=None):
        t = self.get("json/new?" + urllib.parse.quote(url, safe=":/?=&"), method="PUT")
        tab = Tab(t["webSocketDebuggerUrl"])
        tab.target_id = t["id"]
        tab.send("Inspector.enable")
        tab.send("Page.enable")
        tab.send("Runtime.enable")
        return tab

    def close_tab(self, tab):
        try:
            tab.s.close()
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/close/{tab.target_id}", timeout=3).read()
        except Exception:
            pass

    def alive(self):
        try:
            self.get("json/version")
            return True
        except Exception:
            return False

    def kill(self):
        """Uscita forzata: il browser non ha il tempo di salvare o cancellare niente."""
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.p.pid)], capture_output=True)
            return
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def close(self):
        self.kill()
        time.sleep(1.5)
