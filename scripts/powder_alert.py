#!/usr/bin/env python3
# ╔══════════════════════════════════╗
# ║      ᚺ  ᚢ  ᚷ  ᛁ  ᚾ  ᛋ  ᛟ  ᚾ      ║
# ║         H  U  G  I  N  S  O  N    ║
# ╚══════════════════════════════════╝
# powder_alert.py v0.3 — Tiefschnee-Chance je Skigebiet aus Open-Meteo-Ensembles (ICON-EPS 40 + ECMWF 51, Familien je 50 %)
# + fünf deterministischen Modellen. Rechnet die Matrix D+1…D+4 für die App (data/stand.json), archiviert jeden Lauf
# (data/laeufe/), Alarm-Zustandsmaschine (neu · Update · Absage für JEDEN noch kommenden alarmierten Tag), Schalter aus
# config/schalter.json. v0.1/v0.2 2026-09-16, v0.3 2026-10-03 (App, Astra-Review: docs/app-konzept-astra-review-2026-10-03.md).
"""
Aufruf:
  powder_alert.py                          Normallauf (launchd 17:00, nur in der Saison): rechnen, stand.json, Alarm prüfen + senden
  powder_alert.py --nur-rechnen            rechnen + stand.json + Archiv, Alarm nur als Vorschau (App-Knopf „Jetzt rechnen")
  powder_alert.py --trocken                rechnen und ausgeben — nichts senden, nichts schreiben (außer Cache)
  powder_alert.py --immer                  Saisonfenster ignorieren
  powder_alert.py --alle                   Ausgabe: alle Gebiete je Tag statt Top 5
  powder_alert.py --simulieren 35          35 cm Neuschnee in der Nacht vor dem Alarmtag in alle Läufe (Kettentest, immer trocken)
  powder_alert.py --datum 2027-01-20       „heute" überschreiben (immer trocken; die Wetterdaten bleiben die von heute)
  powder_alert.py --gebiet warth           nur ein Gebiet (immer trocken)
  powder_alert.py --absage 2027-01-20      Absage für einen aktiven Alarm senden (App: Abschalten bei aktivem Alarm)
  powder_alert.py --nachsenden 2027-01-20  zuletzt fehlgeschlagene Nachricht erneut senden
  powder_alert.py --tour 2027-01-20 warth  Sturmbrecher-Sitzung /powder-tag öffnen (App-Knopf „Tour planen")
  powder_alert.py --geocheck               Koordinaten gegen Nominatim prüfen
Exit-Code 3 = es läuft schon ein Lauf (Sperre data/.lauf.lock).
"""
import argparse, datetime as dt, fcntl, hashlib, json, math, os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path
import requests

VERSION = "0.3.0"
BASIS = Path(__file__).resolve().parents[1]
CFG, DATA = BASIS / "config", BASIS / "data"
STATE, STAND, SCHALTER, GEOCHECK = DATA / "state.json", DATA / "stand.json", CFG / "schalter.json", DATA / "geocheck.json"
LAEUFE, CACHE, SPERRE = DATA / "laeufe", DATA / "cache", DATA / ".lauf.lock"
OM_DET = "https://api.open-meteo.com/v1/forecast"
OM_ENS = "https://ensemble-api.open-meteo.com/v1/ensemble"
UA = f"sysy-powder-alert/{VERSION} (privates Spassprojekt)"
WOCHENTAG = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
KOMPASS = {"N": 0, "NO": 45, "O": 90, "SO": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
KURZ = {"icon_seamless": "ICON", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS", "gfs025": "GFS", "meteofrance_seamless": "MF",
        "ukmo_seamless": "UKMO"}
VARS = ["snowfall", "precipitation", "temperature_2m", "wind_speed_10m", "wind_direction_10m", "cloud_cover", "freezing_level_height"]
SCHALTER_VORGABE = {"aktiv": True, "versand": "gruppe", "pause_bis": None, "sitzung": "knopf", "gebiete_aus": []}


# ---------- Hilfen ----------
def lade(p, vorgabe=None):
    try:
        return json.loads(Path(p).read_text())
    except FileNotFoundError:
        if vorgabe is not None:
            return vorgabe
        raise


def speichere(p, obj, eng=False):
    """Atomar (.tmp → rename): die App liest parallel, ein Abbruch hinterlässt nie halbes JSON (Astra 03.10.)."""
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if eng else json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, p)


def richtung(deg): return None if deg is None else list(KOMPASS)[round(deg / 45) % 8]


def kreis_mittel(dirs, gew):
    paare = [(d, w) for d, w in zip(dirs, gew) if d is not None and w]
    sx = sum(w * math.sin(math.radians(d)) for d, w in paare); sy = sum(w * math.cos(math.radians(d)) for d, w in paare)
    return (math.degrees(math.atan2(sx, sy)) % 360) if (abs(sx) + abs(sy)) > 1e-9 else None


def im_sektor(deg, buchstaben, tol=30):
    return deg is not None and any(min(abs(deg - KOMPASS[b]), 360 - abs(deg - KOMPASS[b])) <= tol for b in buchstaben)


def gueltig(xs): return [x for x in xs if x is not None]
def maxi(xs): v = gueltig(xs); return max(v) if v else None
def mini(xs): v = gueltig(xs); return min(v) if v else None
def mittel(xs): v = gueltig(xs); return sum(v) / len(v) if v else None
def pz(p): return "–" if p is None else f"{round(p * 100)} %"
def tag_text(d): return f"{WOCHENTAG[d.weekday()]} {d.strftime('%d.%m.')}"


def familie(suffix):
    return "ICON" if suffix.startswith("icon") else "ECMWF" if suffix.startswith("ecmwf") else suffix.upper()


# ---------- Schalter, Sperre ----------
def schalter():
    s = dict(SCHALTER_VORGABE)
    try:
        s.update(lade(SCHALTER, {}))
    except json.JSONDecodeError:
        print("  config/schalter.json kaputt — Vorgaben genommen")
    return s


def versand_jetzt(heute_echt):
    """Direkt vor dem Senden frisch gelesen — die App kann während eines laufenden Abrufs umschalten (Astra 03.10.).
    Rangfolge: aus > Pause (bis einschließlich) > Versandart."""
    s = schalter()
    if not s.get("aktiv", True):
        return "aus"
    pb = s.get("pause_bis")
    try:
        if pb and heute_echt <= dt.date.fromisoformat(pb):
            return "pause"
    except ValueError:
        pass
    return s.get("versand") if s.get("versand") in ("gruppe", "nur_ich", "stumm") else "gruppe"


def sperre(warten_s):
    """Ein Lauf zur Zeit — launchd-Lauf, App-Knopf und App-Aktionen teilen sich diese Sperre (fcntl, fällt mit dem Prozess)."""
    DATA.mkdir(parents=True, exist_ok=True)
    f = open(SPERRE, "a+")
    ende = time.time() + warten_s
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.seek(0); f.truncate(); f.write(f"{os.getpid()} {dt.datetime.now().isoformat(timespec='seconds')}\n"); f.flush()
            return f
        except BlockingIOError:
            if time.time() >= ende:
                f.close(); return None
            time.sleep(3)


# ---------- Datenholer ----------
_letzter_abruf = [0.0]


def hol(url, q, schluessel, fenster_h=3):
    """Cache-Schlüssel = Parameter-Hash + 3-h-Fenster: Wiederholungen kosten nichts, neue Modellläufe kommen trotzdem durch
    (v0.2 kannte nur das Datum — ein Normallauf mit 3 Tagen ließ danach --alle mit 6 Tagen still verhungern).
    1,5 s Drosselung, 429 → warten: Open-Meteo zählt Ensemble-Abfragen nach Datenmenge."""
    jetzt = dt.datetime.now()
    h = hashlib.sha1((url + json.dumps(q, sort_keys=True)).encode()).hexdigest()[:10]
    cf = CACHE / jetzt.date().isoformat() / f"{schluessel}-{h}-{jetzt.hour // fenster_h}.json"
    if cf.exists():
        return json.loads(cf.read_text()), dt.datetime.fromtimestamp(cf.stat().st_mtime)
    for versuch in range(4):
        warte = 1.5 - (time.time() - _letzter_abruf[0])
        if warte > 0:
            time.sleep(warte)
        r = requests.get(url, params=q, headers={"User-Agent": UA}, timeout=90)
        _letzter_abruf[0] = time.time()
        if r.status_code == 429:
            print(f"  429 bei {schluessel}, warte {20 * (versuch + 1)} s"); time.sleep(20 * (versuch + 1)); continue
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(j.get("reason"))
        speichere(cf, j, eng=True)
        return j, jetzt
    raise RuntimeError("Ratenlimit, 4 Versuche")


def cache_aufraeumen():
    """Nur heute + gestern behalten — ein Volllauf sind ~10 MB, über eine Saison sonst Gigabytes."""
    grenze = dt.date.today() - dt.timedelta(1)
    for d in CACHE.glob("*"):
        try:
            if d.is_dir() and dt.date.fromisoformat(d.name) < grenze:
                shutil.rmtree(d)
        except ValueError:
            pass


def abfrage(g, R, models):
    # past_days=1: das 48-h-Fenster für morgen beginnt gestern früh (v0.2 nahm dafür 0 cm an)
    return {"latitude": g["lat"], "longitude": g["lng"], "elevation": g["berg"], "hourly": ",".join(VARS),
            "models": ",".join(models), "past_days": 1, "forecast_days": 5, "timezone": "Europe/Berlin"}


def reihen(h, suffix):
    n = len(h["time"])
    return hoehenkorrektur({v: list(h.get(v + suffix) or [None] * n) for v in VARS})


def hoehenkorrektur(r):
    """Open-Meteo rechnet die Temperatur auf Bergstationshöhe herunter (elevation=), den Schneefall aber nicht — der kommt
    aus der Gitterpunkthöhe des Modells (ECMWF 0,25° liegt weit unter dem Gipfel) und ist dort Regen. Gemessen 03.10.2026:
    ECMWF-Ensemble, 89 Stunden Niederschlag bei ≤ 0 °C auf Gipfelhöhe, davon 0 mit Schnee. Darum Schnee = Niederschlag ×
    Schneeanteil der Stationstemperatur (≤ 0 °C ganz, ≥ 2 °C gar nicht, dazwischen linear) × 0,7 cm/mm wie Open-Meteo.
    Modellschnee nur, wo die Temperatur fehlt — max(Modell, Schätzung) behielt warmen Modellschnee (Astra 03.10.)."""
    sf, pr, t2 = r["snowfall"], r["precipitation"], r["temperature_2m"]
    for i, (p, t) in enumerate(zip(pr, t2)):
        if p is not None and t is not None:
            anteil = 1.0 if t <= 0 else 0.0 if t >= 2 else (2 - t) / 2
            sf[i] = p * anteil * 0.7
    return r


def ensemble(g, R):
    j, t = hol(OM_ENS, abfrage(g, R, R["modelle_ens"]), f"{g['id']}-ens")
    h = j["hourly"]; laeufe = []
    for k in h:
        m = re.match(r"^snowfall(_member\d+)?_(.+)$", k)
        if m:
            laeufe.append((familie(m.group(2)), reihen(h, (m.group(1) or "") + "_" + m.group(2))))
    return h["time"], laeufe, t


def deterministisch(g, R):
    j, t = hol(OM_DET, abfrage(g, R, R["modelle_det"]), f"{g['id']}-det")
    h = j["hourly"]
    return h["time"], {m: reihen(h, "_" + m) for m in R["modelle_det"]}, t


# ---------- Bewertung ----------
def fenster(zeit, ziel, R):
    """Stundenindizes. Open-Meteo-Stundenwerte gelten für die Stunde, die zum Zeitstempel endet → Fenster (start, ende]."""
    ts = [dt.datetime.fromisoformat(t) for t in zeit]
    ss, se = R["skistart_uhr"], R["skitag_bis_uhr"]
    a = dt.datetime.combine(ziel - dt.timedelta(2), dt.time(ss)); b = dt.datetime.combine(ziel, dt.time(ss))
    c = dt.datetime.combine(ziel, dt.time(12)); e = dt.datetime.combine(ziel, dt.time(se))
    ix = {"schnee": [i for i, t in enumerate(ts) if a < t <= b], "mittag": [i for i, t in enumerate(ts) if b < t <= c],
          "skitag": [i for i, t in enumerate(ts) if b < t <= e]}
    return ix if len(ix["schnee"]) == 48 and len(ix["skitag"]) == se - ss else None


def simuliere(r, zeit, ziel, cm):
    """Kettentest: cm Neuschnee zwischen 20 und 6 Uhr in der Nacht vor dem Zieltag; von da bis Skitagsende −4 °C oder
    kälter (sonst wird der Simulationsschnee im Oktober zu Recht als Matsch verworfen), danach Höhenkorrektur neu."""
    a = dt.datetime.combine(ziel - dt.timedelta(1), dt.time(20)); b = dt.datetime.combine(ziel, dt.time(6))
    e = dt.datetime.combine(ziel, dt.time(23))
    ts = [dt.datetime.fromisoformat(t) for t in zeit]
    idx = [i for i, t in enumerate(ts) if a < t <= b]
    r = {k: list(v) for k, v in r.items()}
    for i, t in enumerate(ts):
        if a < t <= e:
            r["temperature_2m"][i] = min(r["temperature_2m"][i] if r["temperature_2m"][i] is not None else 0.0, -4.0)
    for i in idx:
        r["snowfall"][i] = (r["snowfall"][i] or 0.0) + cm / len(idx)
        r["precipitation"][i] = (r["precipitation"][i] or 0.0) + cm / len(idx) / 0.7
    return hoehenkorrektur(r)


def bewerte(g, r, ix, R):
    """Ein Lauf für einen Zieltag. Fehlende Werte bleiben unbekannt — nie Schönwetter raten (v0.2: kein Wind = +15)."""
    sf, pr, t2, ws, wd, wo, ng = (r[v] for v in VARS)
    fs, tag = ix["schnee"], ix["skitag"]
    # Fenster getrennt prüfen: Lücken am Skitag dürfen kein „fahrbar" erzeugen (Astra 03.10.)
    if any(sum(reihe[i] is None for i in fs) > 0.1 * len(fs) for reihe in (sf, pr, t2)):
        return None
    if any(sum(reihe[i] is None for i in tag) > 1 for reihe in (pr, t2, ws)) or sum(wd[i] is None for i in tag) > 2:
        return None
    neu = sum(sf[i] or 0.0 for i in fs)
    mitte, kum = fs[-1], 0.0               # Stunde, bis zu der die Hälfte des Fensterschnees gefallen ist
    for i in fs:
        kum += sf[i] or 0.0
        if neu > 0 and kum >= neu / 2:
            mitte = i; break
    nach = [i for i in fs if i >= mitte] + ix["mittag"]
    foehn_idx = sorted(set(nach) | set(tag))
    if any(sum(reihe[i] is None for i in foehn_idx) > 0.1 * len(foehn_idx) for reihe in (ws, wd)):
        return None   # unbekannter Wind nach dem Schneefall darf keinen Föhn verschweigen (Astra 03.10.)

    def regen(i): return max(0.0, (pr[i] or 0.0) - (sf[i] or 0.0) / 0.7)
    regen_nach = sum(regen(i) for i in nach)
    t_nach = maxi(t2[i] for i in nach)
    warm = t_nach is not None and t_nach >= R["tmax_nass_c"]
    nass = regen_nach >= R["regen_grenze_mm"]
    wind_tag = maxi(ws[i] for i in tag)
    regen_tag = sum(regen(i) for i in tag)
    wolken = mittel(wo[i] for i in tag)
    t_tag = maxi(t2[i] for i in tag)
    wd_tag = kreis_mittel([wd[i] for i in tag], [(ws[i] or 0.0) + 0.1 for i in tag])
    sh = [i for i in fs if (sf[i] or 0.0) > 0.1]
    wd_schnee = kreis_mittel([wd[i] for i in sh], [sf[i] for i in sh])
    # Wind WÄHREND des Schneefalls (v0.2 nahm hier den Tageswind 8–16 Uhr samt Gewichtungszuschlag)
    ws_schnee = sum((ws[i] or 0.0) * sf[i] for i in sh) / sum(sf[i] for i in sh) if sh else None
    lo, hi = R["foehn_sektor_grad"]
    foehn = bool(g.get("foehn")) and sum(1 for i in foehn_idx if wd[i] is not None and lo <= wd[i] <= hi
                                         and (ws[i] or 0.0) >= R["foehn_wind_ab_kmh"] and t2[i] is not None and t2[i] >= 0) >= 3
    sturm = wind_tag is not None and wind_tag >= R["wind_aus_kmh"]
    regen_ko = regen_tag >= R["regen_grenze_mm"]
    tief, trocken, fahrbar = neu >= R["tiefschnee_ab_cm"], not (warm or nass), not (sturm or regen_ko or foehn)
    # Punkte-Score v0.2 als Zweitwert „Top-Tag" — jetzt mit Stundenfenster, Faktoren strukturiert
    pkt = {"neuschnee": min(50, round(50 * neu / R["neuschnee_top_cm"]))}
    tm = t_nach if t_nach is not None else maxi(t2[i] for i in fs)
    if tm is not None:
        if tm <= R["tmax_trocken_c"] - 5: pkt["kalt"] = 20
        elif tm <= R["tmax_trocken_c"]: pkt["kalt"] = 15
        elif tm >= R["tmax_nass_c"]: pkt["warm"] = -20
    if wind_tag is not None:
        if wind_tag <= R["wind_ok_kmh"]: pkt["wind"] = 15
        elif wind_tag <= R["wind_grenz_kmh"]: pkt["wind"] = 5
        elif wind_tag >= R["wind_aus_kmh"]: pkt["wind"] = -25
    if wolken is not None:
        if wolken <= 25: pkt["sonne"] = 15
        elif wolken <= 60: pkt["sonne"] = 8
    if regen_ko: pkt["regen"] = -30
    regen_fenster = sum(regen(i) for i in fs)
    if regen_fenster >= 2 and regen_fenster >= neu / 0.7: pkt["regen_statt_schnee"] = -30
    ngg = mini(ng[i] for i in fs)
    if ngg is not None and ngg - 300 <= g["tal"]: pkt["bis_tal"] = 5
    stau = im_sektor(wd_schnee, g.get("stau", [])) and (ws_schnee or 0.0) >= R["stau_wind_ab_kmh"]
    if stau: pkt["stau"] = 5
    if foehn: pkt["foehn"] = -20
    if wind_tag is not None and wind_tag >= R["wind_ok_kmh"] and im_sektor(wd_tag, [g.get("hang", "N")], 45): pkt["hang_im_wind"] = -5
    if g.get("hang", "N") in ("N", "NO", "NW") and wolken is not None and wolken <= 60: pkt["nordhang"] = 3
    punkte = sum(pkt.values())
    if neu < R["neuschnee_min_cm"]:
        punkte = min(punkte, 30)
    punkte = max(0, min(100, punkte))
    return {"neu": neu, "tief": tief, "trocken": trocken, "fahrbar": fahrbar, "ereignis": tief and trocken and fahrbar,
            "warm": warm, "nass": nass, "sturm": sturm, "regen_tag": regen_ko, "foehn": foehn, "stau": stau,
            "kalt": tm is not None and tm <= R["tmax_trocken_c"], "wind_ok": wind_tag is not None and wind_tag <= R["wind_ok_kmh"],
            "sonne": wolken is not None and wolken <= 30, "punkte": punkte, "top": punkte >= R["alarm_ab_punkte"], "pkt": pkt,
            "w": {"tmax": t_tag, "wind": wind_tag, "wd": wd_tag, "wolken": wolken, "regen": regen_tag, "ngg": ngg},
            "wd_schnee": wd_schnee}


def fam_ordnung(kv): return (kv[0] != "ICON", kv[0])   # ICON vor ECMWF, wie in regeln.json → modelle_ens


def familien(bew, R):
    fam = {}
    for f, b in bew:
        fam.setdefault(f, []).append(b)
    return {f: v for f, v in fam.items() if len(v) >= R["min_laeufe_je_familie"]}


def anteil(fam, pred):
    """Familien gleich gewichtet: ICON-EPS (40) und ECMWF (51) zählen je 50 % — gepoolt zählte ECMWF mehr (Astra 03.10.)."""
    w = [sum(1 for b in v if pred(b)) / len(v) for v in fam.values()]
    return sum(w) / len(w) if w else None


def quantile(fam, wert, qs):
    gruppen = [g for g in ([x for x in (wert(b) for b in v) if x is not None] for v in fam.values()) if g]
    paare = sorted((x, 1.0 / (len(gruppen) * len(g))) for g in gruppen for x in g)   # Gewichte nur über vorhandene Werte
    if not paare:
        return [None] * len(qs)
    out = []
    for q in qs:
        kum = 0.0
        for x, w in paare:
            kum += w
            if kum >= q - 1e-9:
                out.append(x); break
        else:
            out.append(paare[-1][0])
    return out


def richtung_gew(fam, wert, gewicht):
    dirs, gew = [], []
    for v in fam.values():
        for b in v:
            dirs.append(wert(b)); gew.append(gewicht(b) / (len(fam) * len(v)))
    return kreis_mittel(dirs, gew)


def analyse(g, bew_ens, bew_det, R):
    fam = familien(bew_ens, R)
    if not fam:
        return None
    def p(pred): return round(anteil(fam, pred), 3)
    q = quantile(fam, lambda b: b["neu"], [0.1, 0.5, 0.9])
    def med(k): return quantile(fam, lambda b: b["w"][k], [0.5])[0]
    malus = round(max(0, g["fahrzeit_min"] - 30) / 30 * R["fahrzeit_malus_pro_30min"])
    return {"id": g["id"], "name": g["name"], "ort": g["ort"], "lat": g["lat"], "lng": g["lng"], "fahrzeit_min": g["fahrzeit_min"],
            "km": g.get("km"), "lwd": g.get("lwd"), "berg": g["berg"], "tal": g["tal"], "gletscher": bool(g.get("gletscher")),
            "p_tief": p(lambda b: b["ereignis"]), "p_top": p(lambda b: b["top"]),
            "p_fam": {f: round(sum(b["ereignis"] for b in v) / len(v), 3) for f, v in sorted(fam.items(), key=fam_ordnung)},
            "laeufe": {f: len(v) for f, v in sorted(fam.items(), key=fam_ordnung)},
            "trichter": {"tief": p(lambda b: b["tief"]), "trocken": p(lambda b: b["tief"] and b["trocken"]),
                         "fahrbar": p(lambda b: b["ereignis"])},
            "ko": {"sturm": p(lambda b: b["sturm"]), "regen": p(lambda b: b["regen_tag"]), "foehn": p(lambda b: b["foehn"]),
                   "warm": p(lambda b: b["warm"]), "nass": p(lambda b: b["nass"])},
            "faktoren": {"kalt": p(lambda b: b["kalt"]), "wind_ok": p(lambda b: b["wind_ok"]), "sonne": p(lambda b: b["sonne"]),
                         "stau": p(lambda b: b["stau"])},
            "neu": {"p10": q[0], "p50": q[1], "p90": q[2],
                    "kurve": [[x, p(lambda b, x=x: b["neu"] >= x)] for x in range(0, 65, 5)]},
            "wetter": {"tmax": med("tmax"), "wind": med("wind"), "wolken": med("wolken"), "regen": med("regen"), "ngg": med("ngg"),
                       "wd": richtung_gew(fam, lambda b: b["w"]["wd"], lambda b: 1.0)},
            "wd_schnee": richtung_gew(fam, lambda b: b["wd_schnee"], lambda b: b["neu"]),
            "det": {KURZ.get(m, m): (None if b is None else {"neu": round(b["neu"]), "tiefschnee": b["ereignis"], "punkte": b["punkte"]})
                    for m, b in bew_det.items()},
            "malus": malus}


def rang(a):
    # Chance in 5-%-Stufen zuerst, dann Neuschnee-Median minus Fahrzeit-Malus, dann die kürzere Anfahrt
    return (-round(a["p_tief"] * 20), -((a["neu"]["p50"] or 0) - a["malus"]), a["fahrzeit_min"])


# ---------- Ausgabe ----------
def zeile(a):
    det = " / ".join(f"{m} {d['neu']}{'✓' if d['tiefschnee'] else '✗'}" for m, d in a["det"].items() if d)
    fam = " / ".join(f"{f} {pz(v)}" for f, v in a["p_fam"].items())
    n = a["neu"]
    return (f"{a['name']}: {pz(a['p_tief'])} Tiefschnee ({fam}) · Top-Tag {pz(a['p_top'])} · Neuschnee {n['p50']:.0f} cm "
            f"({n['p10']:.0f}–{n['p90']:.0f}) · Schneefall aus {richtung(a['wd_schnee']) or '–'} · Modelle {det or '–'}")


def maps_links(R, b):
    s = R["start"]
    return (f"https://www.google.com/maps/dir/?api=1&origin={s['lat']},{s['lng']}&destination={b['lat']},{b['lng']}&travelmode=driving",
            f"https://maps.apple.com/?daddr={b['lat']},{b['lng']}&dirflg=d")


def alarm_text(ziel, heute, best, rest, R, art, stand_zeit, alt=None):
    d, n = tag_text(ziel), (ziel - heute).days
    wann = {1: "morgen", 2: "übermorgen"}.get(n, f"in {n} Tagen")
    if art == "absage":
        return (f"🎿 Powder-Alarm für {d} zurückgezogen: Prognose gekippt "
                f"({best['name']} jetzt {pz(best['p_tief'])} Tiefschnee-Chance).")
    w, nn = best["wetter"], best["neu"]
    z = [f"🎿 *POWDER ALERT{' — Update' if art == 'update' else ''}* — {d} ({wann})"]
    if art == "update" and alt:
        z.append(f"vorher: {alt['name']} {pz(alt['p'])}")
    z += [f"Top: *{best['name']}* — {pz(best['p_tief'])} Tiefschnee-Chance (≥ {R['tiefschnee_ab_cm']} cm bis {R['skistart_uhr']} Uhr, "
          f"trocken, fahrbar)",
          f"Neuschnee ≈ {nn['p50']:.0f} cm ({nn['p10']:.0f}–{nn['p90']:.0f}) · " + " / ".join(f"{f} {pz(v)}" for f, v in best["p_fam"].items())]
    sk = []
    if w.get("tmax") is not None: sk.append(f"{w['tmax']:.0f} °C")
    if w.get("wind") is not None: sk.append(f"Wind {w['wind']:.0f} km/h" + (f" aus {richtung(w['wd'])}" if w.get("wd") is not None else ""))
    if w.get("wolken") is not None: sk.append("Sonne ☀" if w["wolken"] <= 30 else f"Wolken {w['wolken']:.0f} %")
    if sk: z.append("Skitag: " + " · ".join(sk))
    if best.get("wd_schnee") is not None:
        z.append(f"Schneefall aus {richtung(best['wd_schnee'])}" + (" (Staulage)" if (best["faktoren"]["stau"] or 0) >= 0.5 else ""))
    if rest:
        z.append("Auch gut: " + " · ".join(f"{r['name']} {pz(r['p_tief'])}" for r in rest))
    gm, am = maps_links(R, best)
    z += [f"Anfahrt ab {R['start']['label']} ≈ {best['fahrzeit_min']} min", "Route Google Maps:", gm, "Route Apple Karten:", am]
    lwd = R.get("lawinenberichte", {}).get(best.get("lwd"))
    if best.get("gletscher"):   # Tiefschnee auf dem Gletscher: abseits der Piste Spalten, früh in der Saison kaum überdeckt
        z.append("Gletscher: abseits der markierten Pisten Spaltengefahr.")
    if lwd:
        z += ["Lawinenbericht vorher lesen:", lwd]
    z.append(f"Wer fährt mit? 👍 hier melden. Quelle: Open-Meteo Ensembles ICON-EPS + ECMWF, Stand {stand_zeit:%H:%M} Uhr.")
    return "\n".join(z)


# ---------- Versand ----------
def zustellung_pruefen(R, jid, text):
    """Bridge-„success" heißt nur angenommen (Lehre 03.09.). Nachweis: die Nachricht steht als eigene in der Bridge-DB."""
    db = Path(os.path.expanduser(R.get("bridge_db", "")))
    if not db.is_file():
        return None
    for _ in range(5):
        time.sleep(2)
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            rows = con.execute("SELECT content FROM messages WHERE chat_jid=? AND is_from_me=1 ORDER BY timestamp DESC LIMIT 5",
                               (jid,)).fetchall()
            con.close()
        except sqlite3.Error:
            return None
        if any((c or "").strip() == text.strip() for (c,) in rows):
            return True
    return False


def jid_fuer(R, modus):
    return R["whatsapp"]["gruppe_jid"] if modus == "gruppe" else R["whatsapp"]["dany_jid"]


def senden(R, text, modus):
    """modus: gruppe | nur_ich. Nachrichten an DANY selbst mit 🤖 — sonst liest Sturmbrechers Poller sie als Befehl.
    Eine Zeitüberschreitung der Bridge heißt nicht „nicht gesendet": dann entscheidet die Bridge-DB (Astra 03.10.)."""
    jid = jid_fuer(R, modus)
    if modus == "nur_ich":
        text = "🤖 " + text
    try:
        r = requests.post(R["whatsapp"]["bridge"], json={"recipient": jid, "message": text}, timeout=40)
        ok, antwort = bool(r.ok and r.json().get("success")), r.text[:200]
    except Exception as e:
        ok, antwort = False, str(e)[:200]
    zu = zustellung_pruefen(R, jid, text)
    if not ok and zu:
        ok, antwort = True, f"Bridge-Antwort fehlte ({antwort[:80]}), Nachricht steht aber in WhatsApp"
    print(f"  WhatsApp ({modus}) → {'ok' if ok else 'FEHLER ' + antwort}"
          f"{' · in WhatsApp gespeichert' if zu else ' · nicht in der Bridge-DB gefunden' if zu is False else ''}")
    return {"ok": ok, "zugestellt": zu, "fehler": None if ok else antwort}


def alarm_datei(ziel, art, best, rest, R):
    """data/alarm-<Datum>.json — Eingabe für den Skill powder-tag."""
    gr = [f"Tiefschnee-Chance {pz(best['p_tief'])} (" + " / ".join(f"{f} {pz(v)}" for f, v in best["p_fam"].items()) + ")",
          f"Neuschnee {best['neu']['p50']:.0f} cm ({best['neu']['p10']:.0f}–{best['neu']['p90']:.0f})",
          f"Top-Tag {pz(best['p_top'])}", f"kalt in {pz(best['faktoren']['kalt'])} der Läufe",
          f"Sonne in {pz(best['faktoren']['sonne'])}", f"Sturm {pz(best['ko']['sturm'])} · Föhn {pz(best['ko']['foehn'])}"]
    speichere(DATA / f"alarm-{ziel.isoformat()}.json",
              {"ziel": ziel.isoformat(), "art": art, "best": best, "gruende": gr, "rest": [r["id"] for r in rest], "start": R["start"]})


def sitzung_oeffnen(R, ziel, best, art):
    s = R.get("sturmbrecher", {})
    prompt = (f"/powder-tag {ziel.isoformat()} {best['id']} {art} — {best['name']} ({pz(best['p_tief'])} Tiefschnee-Chance, "
              f"Neuschnee ≈ {best['neu']['p50']:.0f} cm). Skill powder-tag ausführen: Termine prüfen, Kalendereintrag mit Adresse, "
              f"Route, Hütte vorschlagen. Details: data/alarm-{ziel.isoformat()}.json")
    try:
        subprocess.run([str(Path(s["cli"]).expanduser()), "oeffnen", s["projekt"], "--prompt", prompt], check=True, timeout=30)
        print("  Sturmbrecher-Befehlsdatei abgelegt"); return True
    except Exception as e:
        print(f"  Sturmbrecher nicht erreichbar: {e}"); return False


# ---------- Alarm-Zustandsmaschine ----------
def vollstaendig(a, R):
    """Beide Ensemble-Familien da — sonst rechnet die Chance still nur mit einer (Astra 03.10.)."""
    return a is not None and {familie(m) for m in R["modelle_ens"]} <= set(a.get("laeufe", {}))


def alarm_entscheiden(heute, matrix, R, alarme, daten_ok):
    """Je Zieltag D+1…D+4. Neu nur bis vorlauf_tage voraus (übermorgen, verspätet auch morgen). Update/Absage für JEDEN
    noch kommenden aktiven Alarm — v0.2 prüfte nur übermorgen, das versprochene „Update morgen" kam nie (03.10.).
    `best` ist das Gebiet, über das die Nachricht spricht: das neue bei Neualarm/Gebietswechsel, sonst das alarmierte."""
    out = []
    for ziel_s, ana in matrix.items():
        ziel = dt.date.fromisoformat(ziel_s)
        if not ana:
            continue
        top, alt, ok = ana[0], alarme.get(ziel_s) or {}, daten_ok.get(ziel_s, False)
        art, grund, gebiet = None, "", top
        if alt.get("aktiv"):
            jetzt_alt = next((a for a in ana if a["id"] == alt.get("id")), None)
            if not ok or not vollstaendig(jetzt_alt, R) or not vollstaendig(top, R):
                grund = "Datenlücke — kein Update, keine Absage"
            elif top["p_tief"] < R["absage_unter_wahrscheinlichkeit"]:
                art, grund, gebiet = "absage", f"unter {pz(R['absage_unter_wahrscheinlichkeit'])}", jetzt_alt
            elif top["id"] != alt["id"] and (jetzt_alt["p_tief"] < R["alarm_ab_wahrscheinlichkeit"]
                                             or top["p_tief"] - jetzt_alt["p_tief"] >= R["gebietswechsel_ab_diff"]):
                art, grund = "update", f"Gebietswechsel {alt['name']} → {top['name']}"
            elif abs(jetzt_alt["p_tief"] - alt["p"]) >= R["update_ab_diff_wahrscheinlichkeit"]:
                # auch wenn ein anderes Gebiet knapp vorne liegt (Astra 03.10.: sonst blieb der Einbruch ungemeldet)
                art, grund, gebiet = "update", f"Chance {pz(alt['p'])} → {pz(jetzt_alt['p_tief'])}", jetzt_alt
        elif 1 <= (ziel - heute).days <= R["vorlauf_tage"] and top["p_tief"] >= R["alarm_ab_wahrscheinlichkeit"]:
            if ok and vollstaendig(top, R):
                art, grund = "neu", "ab Schwelle"
            else:
                grund = "Datenlücke — kein neuer Alarm"
        rest = [x for x in ana if x["id"] != gebiet["id"] and x["p_tief"] >= R["absage_unter_wahrscheinlichkeit"]][:3]
        out.append({"ziel": ziel_s, "art": art, "grund": grund, "best": gebiet, "rest": rest, "alt": alt or None})
    return out


def _gesendet(eintrag, art, info, modus, text, zugestellt, jetzt):
    """Erfolgreichen Versand in den Alarmzustand übernehmen. `empfaenger` merkt sich, wer den stehenden Alarm hat — die
    Absage geht an alle (Astra 03.10.: Gruppe → „nur an mich" → Update hätte der Gruppe sonst die Absage genommen)."""
    for k in ("unterwegs", "offen", "offen_info", "offen_modus", "fehler", "stumm"):
        eintrag.pop(k, None)
    if art == "absage":
        eintrag.update({"aktiv": False, "art": "absage", "gesendet": jetzt, "zugestellt": zugestellt, "text": text, "empfaenger": []})
        return
    emp = list(eintrag.get("empfaenger") or ([eintrag["modus"]] if eintrag.get("modus") else [])) if eintrag.get("aktiv") else []
    if modus not in emp:
        emp.append(modus)
    eintrag.update({"id": info["id"], "name": info["name"], "p": info["p"], "art": art, "aktiv": True, "gesendet": jetzt,
                    "modus": modus, "zugestellt": zugestellt, "text": text, "empfaenger": emp})


def einmal_senden(R, state, eintrag, art, info, modus, text, von=None):
    """Erst `unterwegs` sichern, dann senden — bricht der Lauf dazwischen ab, klärt der nächste über die Bridge-DB, ob die
    Nachricht draußen ist (kein Doppelversand, Astra 03.10.). Der Aufrufer übernimmt das Ergebnis und sichert."""
    jetzt = dt.datetime.now().isoformat(timespec="minutes")
    eintrag["unterwegs"] = {"art": art, "modus": modus, "text": text, "zeit": jetzt, "von": von or "lauf", **info}
    speichere(STATE, state)
    erg = senden(R, text, modus)
    eintrag.pop("unterwegs", None)
    v = {"zeit": jetzt, "art": art, "id": info.get("id"), "p": info.get("p"), "modus": modus, "ok": erg["ok"], "zugestellt": erg["zugestellt"]}
    if von:
        v["von"] = von
    eintrag["verlauf"] = (eintrag.get("verlauf") or [])[-11:] + [v]
    return erg, jetzt


def versenden(R, state, eintrag, art, info, ziele, text, von=None):
    """An alle Ziele senden, Zustand nach jedem Versand sichern. Rückgabe: Liste der fehlgeschlagenen (modus, fehler).
    Bei der Absage steht die Restliste VOR dem Senden im Zustand und die letzte schließt sofort ab — nie ein halb
    gespeicherter Phantomalarm (Astra 03.10.)."""
    fehl = []
    if art == "absage":
        eintrag["empfaenger"] = list(ziele)
    for m in ziele:
        erg, jetzt = einmal_senden(R, state, eintrag, art, info, m, text, von)
        if erg["ok"]:
            if art == "absage":
                eintrag["empfaenger"] = [x for x in eintrag["empfaenger"] if x != m]
                if not eintrag["empfaenger"]:
                    _gesendet(eintrag, "absage", info, m, text, erg["zugestellt"], jetzt)
            else:
                _gesendet(eintrag, art, info, m, text, erg["zugestellt"], jetzt)
        else:
            fehl.append((m, erg["fehler"]))
        speichere(STATE, state)
    if fehl:
        eintrag.update({"offen": art, "offen_info": info, "offen_modus": [m for m, _ in fehl], "text": text,
                        "fehler": fehl[0][1], "offen_von": von or "lauf"})
        speichere(STATE, state)
    return fehl


def unterwegs_klaeren(state, R):
    """Ein Lauf, der zwischen Senden und Sichern abbrach, hinterlässt `unterwegs`: steht die Nachricht in der Bridge-DB, gilt
    sie als gesendet, sonst als fehlgeschlagen — dann entscheidet der Lauf neu."""
    for z, e in state.get("alarme", {}).items():
        u = e.get("unterwegs")
        if not u:
            continue
        zu = zustellung_pruefen(R, jid_fuer(R, u["modus"]), ("🤖 " if u["modus"] == "nur_ich" else "") + u["text"])
        e.pop("unterwegs", None)
        info = {k: u.get(k) for k in ("id", "name", "p")}
        print(f"  {z}: abgebrochener Versand ({u['art']}, {u['modus']}) {'steht in WhatsApp — übernommen' if zu else 'nicht in WhatsApp'}")
        if u["art"] == "absage":
            # Restliste: wer den Alarm noch hat — ohne Nachweis bleibt auch der abgebrochene Empfänger drauf (Astra 03.10.)
            rest = list(e.get("empfaenger") or [e.get("modus") or u["modus"]])
            if zu:
                rest = [x for x in rest if x != u["modus"]]
            if not rest:
                _gesendet(e, "absage", info, u["modus"], u["text"], True, u["zeit"])
            else:
                e["empfaenger"] = rest
                e.update({"offen": "absage", "offen_info": info, "offen_modus": rest, "text": u["text"],
                          "fehler": "Lauf brach beim Senden ab", "offen_von": u.get("von") or "lauf"})
        elif zu:
            _gesendet(e, u["art"], info, u["modus"], u["text"], True, u["zeit"])
        else:
            e.update({"offen": u["art"], "offen_info": info, "offen_modus": [u["modus"]], "text": u["text"],
                      "fehler": "Lauf brach beim Senden ab", "offen_von": u.get("von") or "lauf"})
    speichere(STATE, state)


def alarm_ausfuehren(entscheidungen, heute, heute_echt, R, state, stand_zeit):
    alarme = state.setdefault("alarme", {})
    s = schalter()
    # Eine in der App ausgelöste, gescheiterte Absage ist ein ausdrücklicher Wunsch: nachholen, nie verwerfen (Astra 03.10.)
    for z, eintrag in alarme.items():
        if eintrag.get("offen") == "absage" and eintrag.get("offen_von") == "app" and eintrag.get("aktiv") and z >= heute.isoformat():
            ziele = list(eintrag.get("offen_modus") or eintrag.get("empfaenger") or [eintrag.get("modus") or "gruppe"])
            print(f"  {z}: Absage aus der App nachholen → {', '.join(ziele)}")
            versenden(R, state, eintrag, "absage", eintrag.get("offen_info") or {k: eintrag.get(k) for k in ("id", "name", "p")},
                      ziele, eintrag.get("text") or f"🎿 Powder-Alarm für {tag_text(dt.date.fromisoformat(z))} zurückgezogen.", von="app")
    for e in entscheidungen:
        eintrag = alarme.get(e["ziel"])
        if e["art"] in ("update", "absage") and not (eintrag and eintrag.get("aktiv")):
            continue   # Alarm steht inzwischen nicht mehr (z. B. App-Absage eben nachgeholt)
        if not e["art"]:
            if eintrag and eintrag.get("offen") and eintrag.get("offen_von") != "app" and not e["grund"].startswith("Datenlücke"):
                # Fehlversuch, den die neue Prognose nicht mehr trägt — sonst schickte „Erneut senden" alte Zahlen
                print(f"  {e['ziel']}: offener {eintrag['offen']}-Versuch verworfen — Prognose trägt ihn nicht mehr")
                for k in ("offen", "offen_info", "offen_modus", "fehler"):
                    eintrag.pop(k, None)
                speichere(STATE, state)
            continue
        ziel, best, art = dt.date.fromisoformat(e["ziel"]), e["best"], e["art"]
        text = alarm_text(ziel, heute, best, e["rest"], R, art, stand_zeit, e["alt"])
        print(f"\n  → {art.upper()} für {e['ziel']} ({e['grund']})\n  --- Nachricht ---\n{text}\n  ---")
        eintrag = alarme.setdefault(e["ziel"], {"aktiv": False})
        info = {"id": best["id"], "name": best["name"], "p": best["p_tief"]}
        modus = versand_jetzt(heute_echt)
        if modus not in ("gruppe", "nur_ich"):        # aus/Pause/stumm: merken, App zeigt „wäre gesendet"
            eintrag["stumm"] = {"zeit": dt.datetime.now().isoformat(timespec="minutes"), "art": art, "modus": modus, "text": text, **info}
            print(f"  nicht gesendet — Schalter: {modus}"); e["ergebnis"] = "stumm"
            speichere(STATE, state)
            continue
        # eine Absage geht an alle, die den Alarm bekommen haben; neu/Update an die aktuelle Versandart
        ziele = list(eintrag.get("empfaenger") or [eintrag.get("modus") or modus]) if art == "absage" else [modus]
        fehl = versenden(R, state, eintrag, art, info, ziele, text)
        e["ergebnis"] = "fehler" if fehl else "gesendet"
        if not fehl and art != "absage":
            try:
                alarm_datei(ziel, art, best, e["rest"], R)
                if s.get("sitzung") == "auto":
                    sitzung_oeffnen(R, ziel, best, art)
            except Exception as ex:
                print(f"  Alarmdatei/Sitzung: {ex}")


# ---------- Archiv, Stand ----------
def archivieren(lauf, heute, matrix):
    kompakt = {z: {a["id"]: [a["p_tief"], a["p_top"], a["neu"]["p10"], a["neu"]["p50"], a["neu"]["p90"]] for a in ana}
               for z, ana in matrix.items()}
    speichere(LAEUFE / f"{lauf:%Y-%m-%dT%H%M}.json", {"lauf": lauf.isoformat(timespec="minutes"), "heute": heute.isoformat(),
                                                      "tage": kompakt}, eng=True)


def trends(matrix, lauf):
    """Chance je Zieltag über die bisherigen Läufe — „steigt seit drei Läufen" ist die halbe Vorhersage."""
    frueher = []
    for f in sorted(LAEUFE.glob("*.json"))[-40:]:
        try:
            j = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if j.get("lauf") and j["lauf"] < lauf.isoformat(timespec="minutes"):
            frueher.append(j)
    for z, ana in matrix.items():
        for a in ana:
            a["trend"] = [[j["lauf"], j["tage"][z][a["id"]][0]] for j in frueher
                          if z in j.get("tage", {}) and a["id"] in j["tage"][z]][-6:]


def extras(a, R, huetten):
    gm, am = maps_links(R, a)
    a["links"] = {"google": gm, "apple": am, "lawine": R.get("lawinenberichte", {}).get(a.get("lwd"))}
    a["huetten"] = [{k: h.get(k) for k in ("name", "lage", "reservierung", "url", "mail", "telefon")}
                    for h in huetten if h.get("gebiet_id") == a["id"]]


# ---------- Aktionen aus der App ----------
def aktion(a, R):
    lock = sperre(60)   # Referenz halten — ein verworfenes Dateiobjekt gäbe die Sperre sofort wieder frei
    if not lock:
        print("Es läuft gerade ein Lauf — bitte gleich nochmal."); sys.exit(3)
    state = lade(STATE, {}); alarme = state.setdefault("alarme", {})
    unterwegs_klaeren(state, R)
    stand = lade(STAND, {})
    stand_zeit = dt.datetime.fromisoformat(stand["lauf"]) if stand.get("lauf") else dt.datetime.now()
    if a.tour:
        ziel_s, gid = a.tour
        best = next((x for x in stand.get("tage", {}).get(ziel_s, []) if x["id"] == gid), None)
        if not best:
            print(f"Keine Daten für {gid} am {ziel_s} in data/stand.json — erst rechnen."); sys.exit(1)
        ziel = dt.date.fromisoformat(ziel_s)
        alarm_datei(ziel, "tour", best, [], R)
        sys.exit(0 if sitzung_oeffnen(R, ziel, best, "tour") else 1)
    ziel_s = a.absage or a.nachsenden
    ziel, heute = dt.date.fromisoformat(ziel_s), dt.date.today()
    e = alarme.get(ziel_s)
    ana = stand.get("tage", {}).get(ziel_s, [])
    if ziel < heute:
        print(f"{ziel_s} ist vorbei — nichts gesendet."); sys.exit(1)
    if a.absage:
        if not e or not e.get("aktiv"):
            print(f"Kein aktiver Alarm für {ziel_s}."); sys.exit(1)
        art = "absage"
        akt = next((x for x in ana if x["id"] == e.get("id")), None)
        info = {"id": e.get("id"), "name": e.get("name"), "p": akt["p_tief"] if akt else e.get("p")}
        text = (f"🎿 Powder-Alarm für {tag_text(ziel)} zurückgezogen"
                + (f": {e['name']} jetzt {pz(akt['p_tief'])} Tiefschnee-Chance." if akt else "."))
        ziele = list(e.get("empfaenger") or [e.get("modus") or "gruppe"])
    else:
        if not e or not e.get("offen"):
            print(f"Nichts offen für {ziel_s}."); sys.exit(1)
        if e["offen"] == "absage" and e.get("offen_von") == "app":
            # ausdrücklicher Absagewunsch aus der App: so wiederholen, nur mit aktueller Zahl
            if not e.get("aktiv"):
                print("Der Alarm steht nicht mehr — nichts abzusagen."); sys.exit(1)
            art = "absage"
            akt = next((x for x in ana if x["id"] == e.get("id")), None)
            info = {"id": e.get("id"), "name": e.get("name"), "p": akt["p_tief"] if akt else e.get("p")}
            text = (f"🎿 Powder-Alarm für {tag_text(ziel)} zurückgezogen"
                    + (f": {e['name']} jetzt {pz(akt['p_tief'])} Tiefschnee-Chance." if akt else "."))
            ziele = list(e.get("offen_modus") or e.get("empfaenger") or [e.get("modus") or "gruppe"])
        else:
            # automatischer Fehlversuch: mit dem AKTUELLEN Stand neu entscheiden — nie den alten Text (Astra 03.10.)
            alter_h = (dt.datetime.now() - stand_zeit).total_seconds() / 3600 if stand.get("lauf") else 99
            if alter_h > 6:
                print("Stand ist älter als 6 h — erst „Jetzt rechnen“."); sys.exit(1)
            modus = versand_jetzt(heute)
            if modus not in ("gruppe", "nur_ich"):
                print(f"Schalter steht auf „{modus}“ — nichts gesendet."); sys.exit(1)
            dok = stand.get("status", {}).get("daten_ok_tage", {}).get(ziel_s, False)
            ent = alarm_entscheiden(heute, {ziel_s: ana}, R, alarme, {ziel_s: dok}) if ana else []
            d = ent[0] if ent else None
            if not d or not d["art"]:
                warum = (d or {}).get("grund") or ("Prognose stützt ihn nicht mehr" if d else "keine Daten für den Tag")
                print(f"Mit dem aktuellen Stand steht kein Versand an ({warum}) — offener Versuch verworfen.")
                for k in ("offen", "offen_info", "offen_modus", "fehler", "offen_von"):
                    e.pop(k, None)
                speichere(STATE, state); sys.exit(1)
            art, akt = d["art"], d["best"]
            info = {"id": akt["id"], "name": akt["name"], "p": akt["p_tief"]}
            text = alarm_text(ziel, heute, akt, d["rest"], R, art, stand_zeit, d["alt"])
            ziele = list(e.get("empfaenger") or [e.get("modus") or modus]) if art == "absage" else [modus]
    print(f"{art} für {ziel_s} → {', '.join(ziele)}:\n{text}")
    fehl = versenden(R, state, e, art, info, ziele, text, von="app")
    if not fehl and art in ("neu", "update"):
        best = next((x for x in ana if x["id"] == info["id"]), None)
        if best:
            alarm_datei(ziel, art, best, [], R)
    sys.exit(1 if fehl else 0)


def geocheck(gebiete):
    out = []
    for g in gebiete:
        r = requests.get("https://nominatim.openstreetmap.org/search", params={"q": g["ort"], "format": "json", "limit": 1},
                         headers={"User-Agent": UA}, timeout=30)
        j = r.json() if r.ok else []
        if j:
            la, lo = float(j[0]["lat"]), float(j[0]["lon"])
            dist = math.hypot((la - g["lat"]) * 111.0, (lo - g["lng"]) * 111.0 * math.cos(math.radians(la)))
            out.append({"id": g["id"], "config": [g["lat"], g["lng"]], "nominatim": [la, lo], "abstand_km": round(dist, 1)})
            print(f"  {g['id']:<14} {dist:5.1f} km  {j[0]['display_name'][:70]}")
        else:
            out.append({"id": g["id"], "fehler": "kein Treffer"}); print(f"  {g['id']:<14} kein Treffer")
        time.sleep(1.1)
    speichere(GEOCHECK, out)


# ---------- Hauptlauf ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nur-rechnen", action="store_true"); ap.add_argument("--trocken", action="store_true")
    ap.add_argument("--immer", action="store_true"); ap.add_argument("--alle", action="store_true")
    ap.add_argument("--simulieren", type=float, default=0.0); ap.add_argument("--datum"); ap.add_argument("--gebiet")
    ap.add_argument("--absage"); ap.add_argument("--nachsenden"); ap.add_argument("--tour", nargs=2, metavar=("DATUM", "GEBIET"))
    ap.add_argument("--geocheck", action="store_true")
    a = ap.parse_args()
    R, G = lade(CFG / "regeln.json"), lade(CFG / "skigebiete.json")["gebiete"]
    for d in (a.absage, a.nachsenden, a.tour[0] if a.tour else None, a.datum):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            ap.error(f"Datum {d!r} nicht im Format JJJJ-MM-TT")
    if a.geocheck:
        geocheck(G); return
    if a.absage or a.nachsenden or a.tour:
        aktion(a, R); return
    heute_echt = dt.date.today()
    test = bool(a.trocken or a.simulieren or a.datum or a.gebiet)
    modus = "trocken" if test else ("nur_rechnen" if a.nur_rechnen else "normal")
    s = schalter()
    if modus == "normal" and not s.get("aktiv", True):
        print(f"{heute_echt}: Hauptschalter AUS (config/schalter.json) — nichts zu tun."); return
    heute = dt.date.fromisoformat(a.datum) if a.datum else heute_echt
    in_saison = R["saison_start"] <= heute.isoformat() <= R["saison_ende"]
    if not a.immer and not in_saison:
        print(f"{heute}: außerhalb der Saison ({R['saison_start']} – {R['saison_ende']}), nichts zu tun."); return
    lock = sperre(900 if modus == "normal" else 0)
    if not lock:
        print("Es läuft schon ein Lauf — abgebrochen."); sys.exit(3)
    aus = set(s.get("gebiete_aus") or [])
    gebiete = [g for g in G if g["id"] == a.gebiet] if a.gebiet else [g for g in G if g["id"] not in aus]
    lauf = dt.datetime.now()
    print(f"Powder Alert v{VERSION} · {lauf:%Y-%m-%d %H:%M} · Modus {modus} · {len(gebiete)} Gebiete"
          + (f" · Simulation {a.simulieren:.0f} cm" if a.simulieren else ""))
    zieltage = [heute + dt.timedelta(n) for n in range(1, 5)]
    sim_ziel = heute + dt.timedelta(R["vorlauf_tage"])
    matrix, status = {z.isoformat(): [] for z in zieltage}, {}
    for g in gebiete:
        st = status[g["id"]] = {"ens": None, "det": None, "fehler": []}
        try:
            zeit, mitgl, st["ens"] = ensemble(g, R)
        except Exception as e:
            st["fehler"].append(f"Ensemble: {e}"); print(f"  {g['id']}: Ensemble-Fehler {e}"); continue
        try:
            zeit_d, det, st["det"] = deterministisch(g, R)
        except Exception as e:   # fehlt nur die Deterministik, bleibt das Ensemble gültig (Astra 03.10.)
            zeit_d, det = None, {}; st["fehler"].append(f"Modelle: {e}"); print(f"  {g['id']}: Modell-Fehler {e}")
        for ziel in zieltage:
            ix = fenster(zeit, ziel, R)
            if not ix:
                continue
            sim = a.simulieren if a.simulieren and ziel == sim_ziel else 0.0
            bew_e = []
            for f, r in mitgl:
                b = bewerte(g, simuliere(r, zeit, ziel, sim) if sim else r, ix, R)
                if b:
                    bew_e.append((f, b))
            bew_d = {}
            ix_d = fenster(zeit_d, ziel, R) if zeit_d else None
            for m, r in det.items():
                bew_d[m] = bewerte(g, simuliere(r, zeit_d, ziel, sim) if sim else r, ix_d, R) if ix_d else None
            x = analyse(g, bew_e, bew_d, R)
            if x:
                matrix[ziel.isoformat()].append(x)
    for z in matrix:
        matrix[z].sort(key=rang)
    ok_gebiete = sum(1 for v in status.values() if v["ens"])
    # Freigabe je Zieltag nach BEWERTBAREN Gebieten mit beiden Familien, nicht nach erfolgreichen Abrufen (Astra 03.10.)
    daten_ok = {z: bool(gebiete) and sum(1 for x in ana if vollstaendig(x, R)) / len(gebiete) >= R["min_gebiete_anteil_fuer_alarm"]
                for z, ana in matrix.items()}
    for z, ana in matrix.items():
        zd = dt.date.fromisoformat(z)
        if not ana:
            print(f"\n{tag_text(zd)} {z}: keine Daten"); continue
        print(f"\n{tag_text(zd)} {z}")
        for x in ana if a.alle else ana[:5]:
            print("   " + zeile(x))
    state = lade(STATE, {})
    state.setdefault("alarme", {})
    if modus == "normal":
        unterwegs_klaeren(state, R)
    entsch = alarm_entscheiden(heute, matrix, R, state["alarme"], daten_ok)
    if modus != "normal":
        for e in entsch:
            if e["art"]:
                print(f"\n  [Vorschau] {e['art'].upper()} für {e['ziel']} ({e['grund']}) — {modus}: nichts gesendet")
                if modus == "trocken":
                    zd = dt.date.fromisoformat(e["ziel"])
                    print(alarm_text(zd, heute, e["best"], e["rest"], R, e["art"], lauf, e["alt"]))
    if modus == "trocken":
        print("\n(trocken: nichts gesendet, nichts geschrieben)"); return
    huetten = lade(CFG / "huetten.json", [])
    for ana in matrix.values():
        for x in ana:
            extras(x, R, huetten)
    trends(matrix, lauf)
    archivieren(lauf, heute, matrix)
    if modus == "normal":
        alarm_ausfuehren(entsch, heute, heute_echt, R, state, lauf)
        grenze = (heute - dt.timedelta(7)).isoformat()
        state["alarme"] = {k: v for k, v in state["alarme"].items() if k >= grenze}
        speichere(STATE, state)
    vorschau = {}
    for e in entsch:
        if e["art"]:
            vorschau[e["ziel"]] = {"art": e["art"], "grund": e["grund"], "id": e["best"]["id"], "ergebnis": e.get("ergebnis"),
                                   "text": alarm_text(dt.date.fromisoformat(e["ziel"]), heute, e["best"], e["rest"], R, e["art"],
                                                      lauf, e["alt"])}
    speichere(STAND, {
        "version": VERSION, "lauf": lauf.isoformat(timespec="minutes"), "modus": modus, "heute": heute.isoformat(),
        "saison": {"start": R["saison_start"], "ende": R["saison_ende"], "in_saison": in_saison},
        "regeln": {k: R[k] for k in ("alarm_ab_wahrscheinlichkeit", "absage_unter_wahrscheinlichkeit", "tiefschnee_ab_cm",
                                     "skistart_uhr", "skitag_bis_uhr", "vorlauf_tage", "alarm_ab_punkte", "lauf_uhrzeit")},
        "start": R["start"]["label"],
        "status": {"gebiete": len(gebiete), "ok": ok_gebiete, "daten_ok": all(daten_ok.values()), "daten_ok_tage": daten_ok,
                   "aus": sorted(aus),
                   "abruf": {k: {"ens": v["ens"] and v["ens"].isoformat(timespec="minutes"),
                                 "det": v["det"] and v["det"].isoformat(timespec="minutes"), "fehler": v["fehler"]}
                             for k, v in status.items()}},
        "tage": matrix, "alarm": vorschau})
    cache_aufraeumen()
    print(f"\nstand.json geschrieben · {ok_gebiete}/{len(gebiete)} Gebiete · {(dt.datetime.now() - lauf).seconds} s")


if __name__ == "__main__":
    main()
