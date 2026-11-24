"""Prova del server di sync (go-sync con le patch di Shannon), dall'esterno.

uso:
  prova.py chiave <file>          crea una chiave di prova e stampa la sua chiave pubblica
                                  (da mettere in ALLOWED_CLIENT_IDS del server di prova)
  prova.py verifica <url> <file>  url = http://<server>:<porta>, senza /v2

Controlla: il server risponde; la catena ammessa legge i dati (GetUpdates riuscito, con
"birthday"); una catena non ammessa, un token scaduto e una firma sbagliata vengono rifiutati.
Il token è quello di Brave: base64url(hex(timestamp)|hex(firma)|hex(chiave pubblica)).
Usare un server di prova: la prima lettura scrive i record iniziali della catena.
"""
import base64, os, sys, time, urllib.error, urllib.request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

BOOKMARKS = 32904  # numero del campo dei segnalibri in EntitySpecifics
problems = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "NO   ") + name + (f" | {detail}" if detail != "" else ""), flush=True)
    if not ok:
        problems.append(name)


def public_hex(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def token(key, millis=None, bad_signature=False):
    stamp = str(millis if millis is not None else int(time.time() * 1000)).encode()
    signature = key.sign(stamp)
    if bad_signature:
        signature = bytes([signature[0] ^ 1]) + signature[1:]
    raw = f"{stamp.hex()}|{signature.hex()}|{public_hex(key)}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def field(num, value):
    if isinstance(value, int):
        return varint(num << 3) + varint(value)
    return varint(num << 3 | 2) + varint(len(value)) + value


def get_updates_message():
    marker = field(1, BOOKMARKS)  # DataTypeProgressMarker.data_type_id
    get_updates = field(6, marker) + field(9, 9)  # from_progress_marker, origin NEW_CLIENT
    return field(1, b"prova") + field(2, 52) + field(3, 2) + field(5, get_updates)  # share, version, GET_UPDATES


def top_fields(data):
    """I campi di primo livello di un messaggio protobuf: {numero: [valori]}."""
    fields, i = {}, 0
    while i < len(data):
        key, shift = 0, 0
        while True:
            b = data[i]; i += 1
            key |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                break
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, shift = 0, 0
            while True:
                b = data[i]; i += 1
                val |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
        elif wire == 2:
            size, shift = 0, 0
            while True:
                b = data[i]; i += 1
                size |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            val = data[i:i + size]; i += size
        elif wire == 1:
            val = data[i:i + 8]; i += 8
        elif wire == 5:
            val = data[i:i + 4]; i += 4
        else:
            raise ValueError(f"tipo di campo {wire} non previsto")
        fields.setdefault(num, []).append(val)
    return fields


def post(url, tkn, body):
    req = urllib.request.Request(url + "/v2/command/", data=body, method="POST", headers={
        "Authorization": "Bearer " + tkn, "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "chiave":
        key = Ed25519PrivateKey.generate()
        fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.private_bytes_raw().hex() + "\n")
        print(public_hex(key))
        return
    if len(sys.argv) != 4 or sys.argv[1] != "verifica":
        sys.exit(__doc__)
    url = sys.argv[2].rstrip("/")
    with open(sys.argv[3]) as f:
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(f.read().strip()))

    try:
        with urllib.request.urlopen(url + "/health-check", timeout=10) as r:
            check("il server risponde", r.status == 200, r.status)
    except Exception as e:
        check("il server risponde", False, e)
        sys.exit(1)

    status, body = post(url, token(key), get_updates_message())
    check("catena ammessa: accettata", status == 200, (status, body[:80]))
    if status == 200:
        f = top_fields(body)
        check("catena ammessa: lettura riuscita (SUCCESS)", f.get(4, [None])[0] == 0, f.get(4))
        check("catena ammessa: c'è il birthday dei dati", bool(f.get(6, [b""])[0]))

    other = Ed25519PrivateKey.generate()
    status, _ = post(url, token(other), get_updates_message())
    check("catena non ammessa: rifiutata (401)", status == 401, status)
    status, _ = post(url, token(key, millis=int(time.time() * 1000) - 2 * 86400 * 1000), get_updates_message())
    check("token scaduto: rifiutato (401)", status == 401, status)
    status, _ = post(url, token(key, bad_signature=True), get_updates_message())
    check("firma sbagliata: rifiutata (401)", status == 401, status)

    print("FINE:", len(problems), "problemi", problems, flush=True)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
