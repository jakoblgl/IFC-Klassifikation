# IFC-Attribut-Klassifikations-Prototyp

Lokales Tool zur automatisierten Klassifikation von Bauteilen aus IFC-Modellen
(BIM) anhand frei konfigurierbarer Attribut-Konzepte – z.B. "ist das Bauteil
tragend?" oder "aus welchem Material besteht der Träger?". Die Einordnung
übernimmt ein Sprachmodell (LLM), das die relevanten IFC-Attribute eines
Bauteils gegen die gewählten Zielkategorien abgleicht. Teil einer
Masterarbeit; die eigentliche Klassifikationslogik ist in `src/` beschrieben,
diese Oberfläche ist reine Präsentationsschicht.

## Herunterladen

- **Ohne Git**: oben auf dieser Seite auf den grünen "Code"-Button klicken →
  "Download ZIP" → die Datei an einem beliebigen Ort entpacken.
- **Mit Git**: `git clone https://github.com/jakoblgl/IFC-Klassifikation.git`

## Voraussetzungen

- Windows
- Python 3.9 (wird beim ersten Start automatisch als lokale Umgebung
  eingerichtet, falls der `py`-Launcher verfügbar ist – sonst vorher von
  https://www.python.org/downloads/ installieren)
- Für das Standard-Backend Ollama – das lokale, kostenlose Sprachmodell, mit
  dem klassifiziert wird:
  1. Installieren: https://ollama.com/download
  2. Einmalig das benötigte Modell laden (ca. 4,7 GB):
     `ollama pull qwen2.5:7b-instruct`

  Alternativ lässt sich in der Oberfläche auf die Cloud-API von Claude
  umschalten (siehe unten) – dann ist kein Ollama nötig, dafür ein eigener
  Anthropic-API-Key.

## Start

`start_gui.bat` doppelklicken. Beim allerersten Start wird automatisch eine
lokale Python-Umgebung eingerichtet (kann ein paar Minuten dauern), danach
öffnet sich das Tool in einem eigenen Fenster. Es ist keine weitere Software
nötig außer Python und Ollama.

## Datenschutz

Das Tool läuft ausschließlich lokal auf diesem Rechner (Server nur auf
127.0.0.1) – IFC-Dateien werden nur von der Festplatte gelesen, nichts wird
hochgeladen. Die Klassifikation selbst läuft komplett offline über das lokal
installierte Ollama-Modell. Einzige Ausnahme: eine optionale Anreicherung mit
Definitionen aus dem buildingSMART Data Dictionary (bSDD) beim "Attributpfade
vorschlagen lassen". dabei wird nur ein generischer Klassenname wie `IfcWall` 
übertragen, keine Projekt- oder Bauteildaten. Ist bSDD nicht erreichbar, 
funktioniert das Tool unverändert weiter.

## Bedienung – kurzer Ablauf

1. **IFC-Datei(en) auswählen** – eine oder mehrere `.ifc`-Dateien von der
   Festplatte auswählen.
2. **Anwendungsfälle konfigurieren** – entweder eine vorhandene Vorlage
   übernehmen (z.B. "Tragende Funktion") oder einen eigenen Anwendungsfall
   erstellen: Konzept, betroffene Bauteilklasse(n), Zielkategorien und die
   Attributpfade angeben, anhand derer klassifiziert werden soll. Passende
   Attributpfade lassen sich auch per Knopfdruck vom LLM vorschlagen lassen.
3. **Klassifizieren** – Button "Alle Anwendungsfälle klassifizieren".
   **Das kann je nach Anzahl unterschiedlicher Attribut-Kombinationen mehrere
   Minuten dauern**.
4. **Ergebnis** – Tabelle ansehen, als CSV oder als um die Klassifikation
   angereicherte IFC-Datei in einem frei wählbaren Zielordner speichern.

Eigene Zusammenstellungen von Anwendungsfällen lassen sich in der
Seitenleiste unter "Projekt" für spätere Sitzungen speichern und wieder
laden.

## Warum dauert die Klassifikation manchmal lange?

Für jede *einzigartige* Kombination der konfigurierten Attributwerte macht
das Tool einen eigenen Aufruf an das LLM (nicht pro Bauteil-Instanz, aber
z.B. bei 40 unterschiedlichen Attribut-Kombinationen entsprechend 40
Aufrufe). Das lokale Modell läuft ohne dedizierte GPU spürbar langsamer als
eine Cloud-API. Bei vielen unterschiedlichen Kombinationen kann ein einziger
Anwendungsfall daher durchaus mehrere Minuten dauern.

Diese Aufrufe laufen dabei zu zweit gleichzeitig statt strikt nacheinander
(unabhängige Einzelanfragen, kein gemeinsamer Prompt – das würde die
Genauigkeit verschlechtern, siehe Kommentar in `classify_generic_v3.py`).

## LLM-Backend wählen: Ollama oder Claude API

In der Seitenleiste unter "LLM-Backend" lässt sich zwischen zwei Backends
umschalten:

- **Ollama (lokal)** – Standard, kostenlos, läuft komplett offline (siehe
  Datenschutz oben). Spürbar langsamer, siehe vorheriger Abschnitt.
- **Claude API (Cloud)** – erfordert einen eigenen Anthropic-API-Key (Feld
  erscheint nach der Auswahl). Deutlich schneller, aber die Attributwerte
  der klassifizierten Bauteile werden dafür an die Anthropic-API übertragen.
  Für Projekte mit entsprechenden Datenschutzanforderungen bleibt Ollama
  die vorgesehene Wahl. Der Key wird nur für die laufende Sitzung im
  Arbeitsspeicher gehalten, nicht gespeichert.

## Problembehebung

**"Ollama nicht erreichbar"** (rotes Badge unten in der Seitenleiste):
Ollama muss installiert UND gestartet sein (läuft nach der Installation
meist automatisch im Hintergrund). Prüfen: im Terminal `ollama list`
ausführen – erscheint `qwen2.5:7b-instruct` in der Liste? Falls nicht:
`ollama pull qwen2.5:7b-instruct`, danach die Seite neu laden.

**Fehlermeldung während der Klassifikation ("… fehlgeschlagen: … Läuft
Ollama noch?")**: Ollama wurde während des Laufs beendet oder ist
abgestürzt. Ollama neu starten und den betroffenen Anwendungsfall erneut
klassifizieren.

**Die Oberfläche reagiert lange nicht / bleibt bei "Klassifiziere …"
stehen**: bei vielen Attribut-Kombinationen normal (siehe oben) – nicht
abbrechen.

## Aufbau des Repositories (für Entwickler)

- `src/gui_app.py` – Streamlit-Oberfläche (reine Präsentationsschicht)
- `src/schema_extraction.py`, `classify_generic*.py`, `classify_dynamic.py`,
  `attribute_diagnostics.py`, `bsdd_client.py`, `llm_client.py`,
  `export_output.py` – die in der Masterarbeit beschriebenen
  Klassifikationsmodule
- `data/` – die vom Programm genutzten Anwendungsfall-Presets (werden über
  "Vorlage übernehmen" angeboten)
