# Recherche 16.09.2026 — Vorbilder, Datenquellen, Kriterien (Research-Agent, Quellen je Punkt)

## Vorbilder (GitHub)
- **turkillo/app_surf_forecast** (Python, Open-Meteo Marine, YAML je Spot, Telegram, GitHub-Actions-Cron) — bestes Architekturvorbild, 1:1 auf Skigebiet/Schneefall übertragbar. https://github.com/turkillo/app_surf_forecast
- **b-dulaney/powder-hound** (Svelte/TS, CAIC-Forecast + Resort-Scraper, Mail via Resend) — Muster „zwei Alarmtypen": Vorhersage-Alarm 16:30 + Ist-Alarm 06:00 (Neuschnee über Nacht bestätigt). https://github.com/b-dulaney/powder-hound
- **christitensor/actually_open_snow** (Next.js; Open-Meteo + NWS + avalanche.org + Overpass, alles ohne Key). https://github.com/christitensor/actually_open_snow
- **cssidy/powder-day-alerts** (Python, Resort-Scrape, Twilio-SMS, „> 4 Zoll/24 h", Cron 07:30). https://github.com/cssidy/powder-day-alerts
- **WhiteElement/SkiFunZ** (Java, Bergfex-Scraper: Schneehöhe Berg/Tal, Lifte, Pisten). https://github.com/WhiteElement/SkiFunZ

## Datenquellen — Entscheidung
| Quelle | Befund | Entscheid |
|---|---|---|
| **Open-Meteo** | kein Key, < 10.000 Calls/Tag frei; `snowfall` (cm), `snow_depth`, `freezing_level_height`, Wind, Temp; `elevation`-Korrektur; ICON-D2 (2 km, 48 h), ICON-EU, ECMWF. https://open-meteo.com/en/docs | **Kernquelle** (im Skript) |
| Windy Point Forecast API | Free-Tier nur Entwicklung, Daten absichtlich verfälscht; Professional 990 €/Jahr. https://api.windy.com/point-forecast/pricing | **nicht nutzen** |
| Bergfex | keine API; Schneebericht-Seiten scrapebar (HA-Community), robots.txt blockt AI-Crawler, AGB-Lage unklar. | optional, 1×/Tag, nur wenn DANY will — Kernfunktion braucht es nicht |
| avalanche.report (EUREGIO Tirol) | offene API `https://api.avalanche.report/albina/`, CAAML v6/JSON, ohne Auth. https://avalanche.report/more/open-data | Zusatzmodul ab Dezember (St. Anton, Lermoos, Zugspitze-Tirol, Tannheim, Reutte) |
| Vorarlberg | ALBINA-Instanz `admin.lawinen-warnung.eu/albina/api/bulletins/…?region=AT-08`, Struktur nicht voll dokumentiert | prüfen, sobald Saison läuft (Warth, Damüls, Lech, Diedamskopf) |
| SLF (Schweiz) | `https://aws.slf.ch/api/bulletin/caaml`, CC BY 4.0 | nicht nötig (kein CH-Gebiet in der Liste) |
| Lawinenwarndienst Bayern | nur HTML (Allgäuer Alpen), keine JSON/CAAML-API gefunden; EAWS-Registry (eaws-regions/pyAvaCore) prüfen | HTML-Fallback als Zusatzmodul |
| Liftstatus (OnTheSnow, Weather Unlocked) | nur Partner-/Bezahlmodelle | weglassen |
| DWD ICON-D2 GRIB / GeoSphere Austria | Rohdaten bzw. INCA-Nowcast; Open-Meteo liefert ICON-D2 schon aufbereitet | nur bei Bedarf |
- Lageberichte starten erst Anfang Dezember — für den 15.10. irrelevant.

## Kriterien (Communities)
- OpenSnow „Powder-Alarm" ab **≥ 15 cm/24 h** (6 Zoll), „Deep" ab 25 cm (10 Zoll). https://support.opensnow.com/feature-guides/powder-quality
- Ideale Gipfeltemperatur **−15 bis −12 °C**, Wind **< ~24 km/h** (stärker verpackt den Schnee). https://opensnow.com/news/post/perfect-powder-explained
- Schnee-Wasser-Verhältnis: 20:1 leicht, 5:1 „Cement", 30:1 „Blower"; Referenz ~12:1. https://opensnow.com/news/post/snow-ratios-explained
- Sicht/Sonne danach und Lawinenstufe: kein belegter Zahlen-Trigger gefunden — bei uns Bonus (Bluebird) bzw. Zusatzmodul.

## Abgleich mit `config/regeln.json` v0.1
- `neuschnee_min_cm 15` = OpenSnow-Schwelle ✓ · `neuschnee_top_cm 30` ≈ „Deep" ✓
- `wind_ok_kmh 30` etwas großzügiger als OpenSnow (24) — Liftbetrieb im Allgäu läuft bis ~40 km/h; nach der ersten Saison prüfen.
- Temperatur: wir prüfen Tmax am Vortag in Bergstationshöhe (≤ 0 °C trocken, ≤ −5 kalt) statt Gipfel −12 °C — konservativ genug, Datenlage ist 2-m-Temperatur.
- **Idee aus powder-hound:** zweiter Lauf 06:00 am Skitag als Ist-Bestätigung (Open-Meteo `snow_depth`-Differenz / Vortag) → „Go/No-Go" morgens.
