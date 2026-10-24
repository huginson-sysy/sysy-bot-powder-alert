#!/usr/bin/env python3
# ╔══════════════════════════════════╗
# ║      ᚺ  ᚢ  ᚷ  ᛁ  ᚾ  ᛋ  ᛟ  ᚾ      ║
# ║         H  U  G  I  N  S  O  N    ║
# ╚══════════════════════════════════╝
# test_zustand.py — Zustandsmaschine von powder_alert.py offline prüfen (2026-10-03). WhatsApp-Bridge und Bridge-DB sind
# Attrappen, Zustand/Schalter/Stand liegen in einem Temp-Verzeichnis — es geht NICHTS raus, data/ bleibt unberührt.
# Braucht ein vorhandenes data/stand.json (sonst erst: powder_alert.py --immer --nur-rechnen). Aufruf im Projektordner:
#   python3 scripts/test_zustand.py
import sys, json, copy, datetime as dt, tempfile, io, contextlib
from pathlib import Path
sys.path.insert(0, "scripts")
import powder_alert as p
tmp = Path(tempfile.mkdtemp(prefix="powder-test-"))
p.DATA, p.STATE, p.SCHALTER, p.STAND, p.SPERRE = tmp, tmp / "state.json", tmp / "schalter.json", tmp / "stand.json", tmp / ".lock"
gesendet, BRIDGE, DB, SNAP = [], {"modus": "ok"}, {"hat": True}, []
class A:
    def __init__(s, ok): s.ok, s.text, s._ok = True, "{}", ok
    def json(s): return {"success": s._ok}
def post(url, json=None, timeout=None):
    gesendet.append(json)
    if BRIDGE["modus"] == "timeout": raise TimeoutError("read timed out")
    return A(BRIDGE["modus"] == "ok")
p.requests.post = post
p.zustellung_pruefen = lambda R, jid, text: DB["hat"]
_orig = p.speichere
def spy(path, obj, eng=False):
    if Path(path) == p.STATE: SNAP.append(json.loads(json.dumps(obj)))
    return _orig(path, obj, eng)
p.speichere = spy
R = json.load(open("config/regeln.json")); stand = json.load(open("data/stand.json")); heute = dt.date(2026, 10, 3)
p.dt.date = type("D", (dt.date,), {"today": staticmethod(lambda: heute)})
D1, D2, D3 = "2026-10-04", "2026-10-05", "2026-10-06"
# die Gebiete-Daten des echten Stands auf feste Testtage legen — sonst testet das Skript nach ein paar Tagen ins Leere
stand["tage"] = dict(zip([D1, D2, D3, "2026-10-07"], [stand["tage"][k] for k in sorted(stand["tage"])]))
def matrix(werte, ohne=()):
    m = copy.deepcopy(stand["tage"])
    for z, ana in m.items():
        for a in ana:
            a["p_tief"] = werte.get(z, {}).get(a["id"], 0.0)
            if a["id"] in ohne: a["laeufe"] = {"ICON": 40}
        ana.sort(key=p.rang)
    return m
def stand_setzen(werte):
    sd = copy.deepcopy(stand); sd["tage"] = matrix(werte); sd["lauf"] = dt.datetime.now().isoformat(timespec="minutes")
    sd["status"]["daten_ok_tage"] = {z: True for z in sd["tage"]}; _orig(p.STAND, sd)
def lauf(name, werte, schalter=None, ohne=()):
    _orig(p.SCHALTER, dict(p.SCHALTER_VORGABE, **(schalter or {})))
    state = p.lade(p.STATE, {}); state.setdefault("alarme", {}); n0 = len(gesendet)
    m = matrix(werte, ohne)
    dok = {z: sum(1 for x in ana if p.vollstaendig(x, R)) / len(ana) >= R["min_gebiete_anteil_fuer_alarm"] for z, ana in m.items()}
    with contextlib.redirect_stdout(io.StringIO()):
        p.unterwegs_klaeren(state, R)
        ent = p.alarm_entscheiden(heute, m, R, state["alarme"], dok)
        p.alarm_ausfuehren(ent, heute, heute, R, state, dt.datetime.now())
    _orig(p.STATE, state)
    return bericht(name, state, n0, ent)
def bericht(name, state, n0, ent=()):
    akt = {e["ziel"][-2:]: (e["art"], e.get("ergebnis")) for e in ent if e["art"]}
    an = [("G" if g["recipient"] == R["whatsapp"]["gruppe_jid"] else "ICH") for g in gesendet[n0:]]
    zust = {z[-2:]: (v.get("id", "")[:6], "AKTIV" if v.get("aktiv") else "-", v.get("offen") or "", "+".join(v.get("empfaenger") or [])) for z, v in state["alarme"].items()}
    print(f"{name:46} {akt} → {an or '–'} · {zust}")
    return state
def aktion(name, **kw):
    class Args: tour = None; absage = None; nachsenden = None
    for k, v in kw.items(): setattr(Args, k, v)
    n0 = len(gesendet)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out: p.aktion(Args, R)
    except SystemExit as ex: code = ex.code
    st = p.lade(p.STATE, {})
    print(f"{name:46} Exit {code} · {out.getvalue().strip().splitlines()[-1][:70] if out.getvalue().strip() else ''}")
    bericht("   → Zustand", st, n0)
# Grundfälle
lauf("A neu D+2 an Gruppe", {D2: {"lermoos": 0.85}})
lauf("K1 nur_ich, 85→60 → Update an mich", {D2: {"lermoos": 0.60}}, {"versand": "nur_ich"})
lauf("K2 kippt → Absage an Gruppe UND mich", {D2: {"lermoos": 0.20}}, {"versand": "nur_ich"})
# Astra-2 B: kein gespeicherter Zwischenstand „aktiv + leere Empfängerliste ohne unterwegs"
phantom = [s for s in SNAP for v in s.get("alarme", {}).values() if v.get("aktiv") and not v.get("empfaenger") and not v.get("unterwegs") and v.get("art") != "neu" and "offen" not in v]
print(f"{'B Phantom-Zwischenstände bei der Absage':46} {len(phantom)} (Soll 0)")
# Astra-2 A: Altzustand ohne Empfängerliste, Abbruch, KEIN Nachweis → darf nicht abgehakt werden
st = p.lade(p.STATE, {}); st["alarme"][D3] = {"aktiv": True, "id": "warth", "name": "Warth", "p": 0.8, "modus": "gruppe",
    "unterwegs": {"art": "absage", "modus": "gruppe", "text": "🎿 Absage TEST", "zeit": "2026-10-03T17:01", "id": "warth", "name": "Warth", "p": 0.2}}
_orig(p.STATE, st); DB["hat"] = False
lauf("A2 Abbruch-Absage ohne Nachweis → bleibt offen", {D3: {"warth": 0.8}})
DB["hat"] = True
# Astra-2 C: App-Absage scheitert → nächster Lauf (Prognose unverändert) holt sie nach
stand_setzen({D3: {"warth": 0.8}}); BRIDGE["modus"], DB["hat"] = "fehler", False
aktion("C1 App-Absage D+3 scheitert", absage=D3)
BRIDGE["modus"], DB["hat"] = "ok", True
lauf("C2 nächster Lauf, Prognose gleich → nachgeholt", {D3: {"warth": 0.8}})
# Nachsenden neu entscheiden
BRIDGE["modus"], DB["hat"] = "fehler", False
lauf("D1 neu D+1 scheitert", {D1: {"tannheim": 0.9}})
BRIDGE["modus"], DB["hat"] = "ok", True
stand_setzen({D1: {"tannheim": 0.88}})
aktion("D2 Erneut senden, Prognose 88 % → neu raus", nachsenden=D1)
stand_setzen({D1: {"tannheim": 0.88}}); BRIDGE["modus"], DB["hat"] = "fehler", False
lauf("D3 Update scheitert (88→55)", {D1: {"tannheim": 0.55}})
BRIDGE["modus"], DB["hat"] = "ok", True
stand_setzen({D1: {"tannheim": 0.86}})
aktion("D4 Erneut senden, aber wieder 86 % → verworfen", nachsenden=D1)
