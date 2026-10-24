#!/usr/bin/env python3
# ╔══════════════════════════════════╗
# ║      ᚺ  ᚢ  ᚷ  ᛁ  ᚾ  ᛋ  ᛟ  ᚾ      ║
# ║         H  U  G  I  N  S  O  N    ║
# ╚══════════════════════════════════╝
# powder_app.py v0.3 — Oberfläche für Powder Alert im Sturmbrecher-Reiter „Apps". Nur Standardbibliothek, nur 127.0.0.1:8014,
# launchd com.sysy.powder-app. Rechnet nichts selbst: liest data/stand.json, schreibt config/schalter.json und startet für
# „Jetzt rechnen", Absage, Nachsenden und „Tour planen" powder_alert.py — ein Ort für alles, was rausgeht. Angelegt 2026-10-03.
import datetime as dt, json, os, re, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("POWDER_APP_PORT", "8014"))
BASIS = Path(__file__).resolve().parents[1]
APP, DATA, CFG = BASIS / "app", BASIS / "data", BASIS / "config"
SKRIPT = BASIS / "scripts" / "powder_alert.py"
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ORIGINS = {f"http://{h}" for h in HOSTS}
MAX_BODY = 16 * 1024
DATUM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SCHALTER_VORGABE = {"aktiv": True, "versand": "gruppe", "pause_bis": None, "sitzung": "knopf", "gebiete_aus": []}
lauf = {"laeuft": False, "start": None, "ende": None, "rc": None, "ausgabe": ""}
lauf_lock, schalter_lock = threading.Lock(), threading.Lock()


def lade(p, vorgabe):
    try:
        return json.loads(Path(p).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return vorgabe


def speichere(p, obj):
    tmp = Path(f"{p}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n"); os.replace(tmp, p)


def jetzt(): return dt.datetime.now().isoformat(timespec="minutes")


def schalter():
    s = dict(SCHALTER_VORGABE); s.update(lade(CFG / "schalter.json", {})); return s


def gebiete_alle():
    return [{"id": g["id"], "name": g["name"], "lwd": g.get("lwd")} for g in lade(CFG / "skigebiete.json", {}).get("gebiete", [])]


def aktive_alarme():
    heute = dt.date.today().isoformat()
    return [{"ziel": z, "name": v.get("name"), "p": v.get("p"), "modus": v.get("modus")}
            for z, v in sorted(lade(DATA / "state.json", {}).get("alarme", {}).items()) if v.get("aktiv") and z >= heute]


def ausfuehren(args, timeout):
    try:
        r = subprocess.run([sys.executable, str(SKRIPT), *args], capture_output=True, text=True, timeout=timeout, cwd=BASIS)
        return r.returncode, (r.stdout + r.stderr)[-6000:]
    except subprocess.TimeoutExpired:
        return 124, f"Zeitüberschreitung nach {timeout} s"


def rechnen_im_hintergrund():
    rc, out = ausfuehren(["--nur-rechnen", "--immer"], 600)
    with lauf_lock:
        lauf.update({"laeuft": False, "ende": jetzt(), "rc": rc, "ausgabe": out[-4000:]})


class Handler(BaseHTTPRequestHandler):
    server_version = "PowderApp/0.3"

    def log_message(self, fmt, *args):   # nur Schreibzugriffe und Fehler ins Log — die App fragt jede Minute nach
        if self.command == "POST" or (len(args) > 1 and str(args[1])[:1] in "45"):
            sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")

    def antwort(self, code, obj=None, body=None, typ="application/json; charset=utf-8"):
        data = body if body is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", typ); self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        if typ.startswith("text/html"):
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'")
        self.end_headers(); self.wfile.write(data)

    def host_ok(self):
        # DNS-Rebinding: eine fremde Seite, die ihren Namen auf 127.0.0.1 umbiegt, schickt ihren eigenen Host-Header
        return self.headers.get("Host", "") in HOSTS

    def do_OPTIONS(self):   # keine CORS-Freigabe: fremde Seiten scheitern am Preflight für X-Powder
        self.antwort(405, {"fehler": "nicht erlaubt"})

    def do_GET(self):
        if not self.host_ok():
            return self.antwort(403, {"fehler": "Host"})
        pfad = self.path.split("?")[0]
        if pfad in ("/", "/index.html"):
            return self.antwort(200, body=(APP / "index.html").read_bytes(), typ="text/html; charset=utf-8")
        if pfad == "/favicon.ico":
            self.send_response(204); self.end_headers(); return
        if pfad == "/api/stand":
            regeln = lade(CFG / "regeln.json", {})
            with lauf_lock:
                l = dict(lauf)
            return self.antwort(200, {
                "jetzt": jetzt(), "stand": lade(DATA / "stand.json", None), "schalter": schalter(),
                "zustand": lade(DATA / "state.json", {}).get("alarme", {}), "gebiete_alle": gebiete_alle(), "lauf": l,
                "saison": {"start": regeln.get("saison_start"), "ende": regeln.get("saison_ende")},
                "lauf_uhrzeit": regeln.get("lauf_uhrzeit")})
        if pfad == "/api/lauf":
            with lauf_lock:
                return self.antwort(200, dict(lauf))
        self.antwort(404, {"fehler": "unbekannt"})

    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-Powder") != "1":
            return self.antwort(403, {"fehler": "nur aus der App"})
        if self.headers.get("Origin") and self.headers["Origin"] not in ORIGINS:
            return self.antwort(403, {"fehler": "Origin"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.antwort(415, {"fehler": "JSON erwartet"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.antwort(400, {"fehler": "Content-Length"})
        if not 0 <= n <= MAX_BODY:   # negativ hätte bis zum Verbindungsende gelesen (Astra 03.10.)
            return self.antwort(413, {"fehler": "zu groß"})
        self.connection.settimeout(10)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, OSError):
            return self.antwort(400, {"fehler": "kaputtes JSON"})
        pfad = self.path.split("?")[0]
        if pfad == "/api/schalter":
            return self.post_schalter(body)
        if pfad == "/api/lauf":
            return self.post_lauf()
        if pfad == "/api/aktion":
            return self.post_aktion(body)
        self.antwort(404, {"fehler": "unbekannt"})

    def post_schalter(self, b):
        ids = {g["id"] for g in gebiete_alle()}
        neu = {}
        try:
            if "aktiv" in b:
                if not isinstance(b["aktiv"], bool): raise ValueError("aktiv")
                neu["aktiv"] = b["aktiv"]
            if "versand" in b:
                if b["versand"] not in ("gruppe", "nur_ich", "stumm"): raise ValueError("versand")
                neu["versand"] = b["versand"]
            if "pause_bis" in b:
                v = b["pause_bis"]
                if v is not None and not (isinstance(v, str) and DATUM.match(v) and dt.date.fromisoformat(v)): raise ValueError("pause_bis")
                neu["pause_bis"] = v or None
            if "sitzung" in b:
                if b["sitzung"] not in ("knopf", "auto"): raise ValueError("sitzung")
                neu["sitzung"] = b["sitzung"]
            if "gebiete_aus" in b:
                v = b["gebiete_aus"]
                if not isinstance(v, list) or not all(isinstance(x, str) and x in ids for x in v): raise ValueError("gebiete_aus")
                neu["gebiete_aus"] = sorted(set(v))
        except ValueError as e:
            return self.antwort(400, {"fehler": f"ungültig: {e}"})
        with schalter_lock:
            s = lade(CFG / "schalter.json", {})
            s.update(neu); s.update({"geaendert": jetzt(), "von": "App"})
            speichere(CFG / "schalter.json", s)
        eff = schalter()
        heute = dt.date.today().isoformat()
        sendet_nicht = (not eff["aktiv"]) or eff["versand"] == "stumm" or bool(eff.get("pause_bis") and heute <= eff["pause_bis"])
        self.antwort(200, {"schalter": eff, "aktive_alarme": aktive_alarme() if sendet_nicht else []})

    def post_lauf(self):
        with lauf_lock:
            if lauf["laeuft"]:
                return self.antwort(409, {"fehler": "läuft schon", **lauf})
            lauf.update({"laeuft": True, "start": jetzt(), "ende": None, "rc": None, "ausgabe": ""})
        threading.Thread(target=rechnen_im_hintergrund, daemon=True).start()
        self.antwort(202, {"gestartet": True})

    def post_aktion(self, b):
        art, ziel, gebiet = b.get("art"), b.get("ziel"), b.get("gebiet")
        if art not in ("absage", "nachsenden", "tour") or not (isinstance(ziel, str) and DATUM.match(ziel)):
            return self.antwort(400, {"fehler": "art/ziel ungültig"})
        if art == "tour":
            if gebiet not in {g["id"] for g in gebiete_alle()}:
                return self.antwort(400, {"fehler": "gebiet ungültig"})
            args = ["--tour", ziel, gebiet]
        else:
            args = [f"--{art}", ziel]
        rc, out = ausfuehren(args, 150)
        self.antwort(200 if rc == 0 else 409 if rc == 3 else 500, {"rc": rc, "ausgabe": out})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print(f"{jetzt()} Powder-App auf http://127.0.0.1:{PORT}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
