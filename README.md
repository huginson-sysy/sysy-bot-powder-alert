<!--
╔══════════════════════════════════╗
║      ᚺ  ᚢ  ᚷ  ᛁ  ᚾ  ᛋ  ᛟ  ᚾ      ║
║         H  U  G  I  N  S  O  N    ║
╚══════════════════════════════════╝
-->
# Powder Alert ❄

Täglicher Tiefschnee-Check für Skigebiete im Allgäu, in Tirol und Vorarlberg. Das Skript liest die
Wetter-Ensembles von Open-Meteo (ICON-EPS und ECMWF), rechnet je Gebiet die **Tiefschnee-Chance** für die
nächsten Tage und meldet zwei Tage vor einem Powder-Tag per WhatsApp — mit Wahrscheinlichkeit, Neuschnee-Median
und Fahrzeit. Dazu eine kleine lokale Web-Oberfläche mit der Gebiets-Matrix und Hüttenvorschlägen.

## Was drin ist

| Pfad | Zweck |
|---|---|
| `scripts/powder_alert.py` | Prognose + Alarm, läuft täglich (launchd-Vorlage in `launchd/`) |
| `scripts/powder_app.py`, `app/index.html` | lokale Oberfläche auf `127.0.0.1:8014` (Matrix je Gebiet, Detail, Schalter) |
| `scripts/test_zustand.py` | Prüfstand für die Zustandslogik |
| `config/skigebiete.json` | 16 Gebiete mit Lage, Stau- und Föhnsektoren, Fahrzeit |
| `config/huetten.json` | Hüttenvorschläge je Gebiet (öffentliche Kontaktdaten der Hütten, Quelle je Eintrag) |
| `config/*.example.json` | Vorlagen für Regeln, Schalter und WhatsApp-Ziel → ohne `.example` kopieren und ausfüllen |
| `docs/` | Recherche zu Vorbildern, Datenquellen und Hütten |

## Wie die Tiefschnee-Chance entsteht

Je Gebiet werden rund 90 Ensemble-Läufe (ICON-EPS und ECMWF, je 50 % Gewicht) ausgewertet. Ein Lauf zählt als
Powder-Tag, wenn in den 48 Stunden bis 8 Uhr **≥ 20 cm Neuschnee** fallen, es danach **trocken** bleibt (kein Regen,
keine Erwärmung ≥ 2 °C bis 12 Uhr) und der Tag **fahrbar** ist (kein Sturm ≥ 60 km/h, kein Regen, kein
Südföhn ≥ 3 h zwischen 8 und 16 Uhr). Der Schneeanteil wird aus der Temperatur der Bergstation korrigiert, weil
Open-Meteo den Schneefall auf der Modell-Gitterhöhe liefert. Die Rangfolge der Gebiete: Tiefschnee-Chance in
5-%-Stufen, dann Neuschnee-Median abzüglich Fahrzeit-Malus, dann Fahrzeit.

## Einrichtung

1. `pip3 install -r requirements.txt` (Python ≥ 3.11)
2. `config/regeln.example.json` → `config/regeln.json`, `config/wa_gruppe.example.json` → `config/wa_gruppe.json`,
   `config/schalter.example.json` → `config/schalter.json`; eigene WhatsApp-Kennungen eintragen.
3. Der WhatsApp-Versand läuft über eine lokale Brücke mit REST-Endpunkt `127.0.0.1:8080/api/send`
   (erprobt mit einer whatsmeow-Brücke). Ohne Brücke `versand` im Schalter auf `stumm` lassen — gerechnet wird trotzdem.
4. Lauf von Hand: `python3 scripts/powder_alert.py` · Oberfläche: `python3 scripts/powder_app.py` → http://127.0.0.1:8014/
5. Täglich: Vorlagen in `launchd/` kopieren, `/Users/USER` durch den eigenen Pfad ersetzen, laden.

Ergebnisse und Zwischenstände liegen in `data/` (nicht im Repo).

## Datenquellen

- Wetter: [Open-Meteo](https://open-meteo.com) — Daten unter CC BY 4.0, API für nicht-kommerzielle Nutzung frei.
- Hüttenkontakte: öffentliche Angaben der Hütten, Fundstelle je Eintrag im Feld `quelle`.

## Hinweis

Eine Wetterprognose ist kein Lawinenlagebericht. Vor jeder Tour den amtlichen Lawinenwarndienst des Gebiets lesen.

## Lizenz

**MIT** — siehe [LICENSE](LICENSE). © 2026 Daniel Lenzenhuber (HUGINSON).
