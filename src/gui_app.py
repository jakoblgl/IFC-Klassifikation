"""
Lokale Bedienoberflaeche fuer den IFC-Attribut-Klassifikations-Prototyp.

Laeuft ausschliesslich als lokaler Server (127.0.0.1); es werden keine Daten
ins Internet uebertragen. Alle Klassifikationsschritte rufen dieselben, in
der Masterarbeit beschriebenen Module auf (schema_extraction,
classify_generic[_v3], classify_dynamic, llm_client, export_output) - diese
Datei ist eine reine Praesentationsschicht, keine eigene Klassifikationslogik.

Erlaubt mehrere Anwendungsfaelle (ggf. mit unterschiedlicher Bauteilklasse)
gleichzeitig zu konfigurieren und in einem Durchgang gegen dieselbe(n)
hochgeladene(n) IFC-Datei(en) laufen zu lassen.
"""
import json
import os
import tempfile
import urllib.request
import uuid
from collections import defaultdict

import ifcopenshell
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from schema_extraction import extract_schema_context_multi, extract_instance_metadata_multi
from classify_generic import extract_combinations
from classify_generic_v3 import extract_combinations_v3, classify_combinations_v3
from classify_dynamic import suggest_attribute_paths
from attribute_diagnostics import path_diagnostics_multi, trim_redundant_paths
from bsdd_client import get_class_properties
from llm_client import get_client
from export_output import enrich_ifc_files

st.set_page_config(page_title="IFC-Attribut-Klassifikation", layout="centered")

# Rein optische Anpassungen ueber CSS (kein eigenes Verhalten):
# 1) Status ("Ollama erreichbar") als kompaktes Badge - wird unten in der
#    Seitenleiste ausgegeben (siehe dort), nur die Pillenform kommt von hier.
# 2) Datei-Auswahl soll nicht wie ein Internet-Upload wirken (Cloud-Icon
#    entfernen, Text neutral: die Dateien werden nur lokal von der
#    Festplatte gelesen, nichts wird irgendwohin hochgeladen).
# 3) Der native Streamlit-Laufindikator oben rechts zeigt bei laengeren
#    Laeufen nacheinander verschiedene "Fortbewegungs"-Icons (Mensch, Rad,
#    Auto, Rakete) - durch einen schlichten sich drehenden Kreis ersetzt,
#    wie er an anderer Stelle in dieser App (st.spinner) schon verwendet wird.
# Die verwendeten data-testid-Attribute sind Streamlits eigene, stabile
# Test-Hooks (nicht die zufaellig generierten st-emotion-cache-Klassen).
st.markdown(
    """
    <style>
    .gui-status-badge {
        display: inline-block;
        padding: 0.2rem 0.7rem;
        border-radius: 1rem;
        font-size: 0.8rem;
        font-weight: 500;
        white-space: nowrap;
    }
    .gui-status-ok { background: rgba(33, 195, 84, 0.15); color: #15803d; }
    .gui-status-error { background: rgba(255, 43, 43, 0.15); color: #b91c1c; }

    /* Status-Container in der Seitenleiste an den unteren Rand schieben
       (statt nur "als letztes Element", das liesse bei kurzem Projekt-
       Inhalt einfach Leerraum darunter). Der eigentliche Flex-Elternblock
       ist der AEUSSERE stVerticalBlock innerhalb von stSidebarUserContent
       (nicht stSidebarUserContent selbst - dazwischen liegt noch ein
       generischer Wrapper-Div ohne stabilen Klassennamen); der Status-
       Container selbst steckt zusaetzlich in einem stLayoutWrapper, daher
       :has() um genau diesen (und nicht die anderen Geschwister-Wrapper)
       an den unteren Rand zu schieben. */
    [data-testid="stSidebarUserContent"],
    [data-testid="stSidebarUserContent"] > div {
        height: 100%;
    }
    [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
        display: flex;
        flex-direction: column;
        min-height: 100%;
    }
    [data-testid="stSidebarUserContent"] [data-testid="stLayoutWrapper"]:has(.st-key-sidebar_status) {
        margin-top: auto;
    }

    [data-testid="stFileUploaderDropzoneInstructions"] svg {
        display: none;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span:first-child {
        font-size: 0;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span:first-child::after {
        content: "Datei(en) hierher ziehen oder auswählen";
        font-size: 14px;
    }

    [data-testid="stStatusWidgetRunningIcon"] > * {
        visibility: hidden;
    }
    [data-testid="stStatusWidgetRunningIcon"] {
        position: relative;
    }
    [data-testid="stStatusWidgetRunningIcon"]::after {
        content: "";
        position: absolute;
        top: 50%;
        left: 50%;
        width: 1rem;
        height: 1rem;
        margin: -0.5rem 0 0 -0.5rem;
        border: 2px solid rgba(120, 120, 120, 0.35);
        border-top-color: rgba(120, 120, 120, 0.9);
        border-radius: 50%;
        animation: gui-app-spin 0.7s linear infinite;
    }
    @keyframes gui-app-spin {
        to { transform: rotate(360deg); }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Streamlit liefert sein eigenes Menue ("Rerun", "Settings", ...), den
# Einstellungen-Dialog und den Design-Editor fest auf Englisch aus - es gibt
# dafuer keine offizielle Sprachoption. Da st.markdown(unsafe_allow_html)
# eingebettete <script>-Tags aus Sicherheitsgruenden entfernt, laeuft dieser
# Uebersetzer stattdessen in einem components.html()-iFrame, das echtes
# JavaScript ausfuehren darf und (gleicher Ursprung) per window.parent auf
# das eigentliche Seiten-DOM zugreifen kann. Es werden ausschliesslich reine
# Text-Knoten mit exakt passendem Originaltext ersetzt (nie ganze Elemente
# oder Attribute) - Klickverhalten und Funktion bleiben unangetastet.
# Eigennamen (Streamlit, Dateipfade wie .streamlit/config.toml) bleiben
# bewusst unuebersetzt.
components.html(
    """
    <script>
    (function() {
        var doc = window.parent.document;
        if (doc.__guiDeTranslateInstalled) return;
        doc.__guiDeTranslateInstalled = true;

        var exact = {
            "Rerun": "Neu ausführen",
            "Always rerun": "Immer neu ausführen",
            "Settings": "Einstellungen",
            "Print": "Screenshot aufnehmen",
            "Record a screencast": "Video",
            "Developer options": "Entwickleroptionen",
            "Clear cache": "Cache leeren",
            // Bestaetigungsdialog hinter "Cache leeren" - eigene Strings,
            // nicht dieselben wie das Menue-Item ("Clear caches" ist Plural
            // und dient dort sowohl als Dialogtitel als auch als
            // Bestaetigen-Button-Beschriftung).
            "Clear caches": "Caches leeren",
            "Are you sure you want to clear the app's function caches?":
                "Sollen die Funktions-Caches der App wirklich geleert werden?",
            "This will remove all cached entries from functions using":
                "Dies entfernt alle zwischengespeicherten Einträge von Funktionen, die",
            "and": " und ",
            "Cancel": "Abbrechen",
            "Report a bug": "Fehler melden",
            "Get help": "Hilfe erhalten",
            "About": "Über diese App",
            "Deploy": "Veröffentlichen",
            "Close": "Schließen",
            "Development": "Entwicklung",
            "Run on save": "Bei Speichern automatisch ausführen",
            "Automatically updates the app when the underlying code is updated.":
                "Aktualisiert die App automatisch, wenn der zugrunde liegende Code geändert wird.",
            "Appearance": "Erscheinungsbild",
            "Wide mode": "Breiter Modus",
            "Turn on to make this app occupy the entire width of the screen.":
                "Aktivieren, damit diese App die gesamte Bildschirmbreite nutzt.",
            "Choose app theme, colors and fonts": "App-Design, Farben und Schriftart wählen",
            "Use system setting": "Systemeinstellung verwenden",
            "Light": "Hell",
            "Dark": "Dunkel",
            "Edit active theme": "Aktives Design bearbeiten",
            "Changes made to the active theme will exist for the duration of a session. To discard changes and recover the original theme, refresh the page.":
                "Änderungen am aktiven Design gelten nur für die Dauer dieser Sitzung. Zum Verwerfen und Wiederherstellen des Original-Designs die Seite neu laden.",
            "Primary color": "Primärfarbe",
            "Background color": "Hintergrundfarbe",
            "Text color": "Textfarbe",
            "Secondary background color": "Sekundäre Hintergrundfarbe",
            // Dieser Hinweis ist im DOM durch die eingebetteten <code>-Elemente
            // "[theme]" und ".streamlit/config.toml" in drei Text-Fragmente
            // zerteilt (die technischen Begriffe bleiben unuebersetzt an Ort
            // und Stelle stehen) - deshalb hier fragmentweise statt als ein
            // zusammenhaengender Satz.
            "To save your changes, copy your custom theme into the clipboard and paste it into the":
                "Um die Änderungen zu speichern, das eigene Design in die Zwischenablage kopieren und in den Abschnitt ",
            "section of your": " der Datei ",
            "file.": " einfügen.",
            "Copy theme to clipboard": "Design in Zwischenablage kopieren",
            "This will record a video with the contents of your screen, so you can easily share what you're seeing with others.":
                "Dies zeichnet ein Video des Bildschirminhalts auf, damit andere leicht sehen können, was gerade angezeigt wird.",
            "Also record audio": "Audio ebenfalls aufnehmen",
            // "Press Esc any time to stop recording." ist im DOM durch ein
            // eigenes <code>-Element um "Esc" in zwei Fragmente zerteilt
            // (Tastenname bleibt unuebersetzt) - Reihenfolge im DOM ist fix
            // (Praefix vor "Esc", Suffix danach), daher die deutsche
            // Formulierung so gewaehlt, dass sie in dieser Reihenfolge liest.
            "Press": "Mit ",
            "any time to stop recording.": " jederzeit die Aufnahme beenden.",
            "Start recording!": "Aufnahme starten!",
            "Choose or add options": "Auswählen oder hinzufügen",
            "No results found.": "Keine Ergebnisse gefunden.",
            "Clear all": "Alle entfernen",
            "Browse files": "Durchsuchen",
            "Press Enter to apply": "Enter drücken zum Übernehmen",
            "Press Ctrl+Enter to apply": "Strg+Enter drücken zum Übernehmen",
        };
        // Nur der Praefix wird ersetzt, der Rest (Produktname, Version,
        // Dateipfad) bleibt unveraendert stehen.
        var prefixRules = [
            [/^Made with\\b/, "Erstellt mit"],
            [/ per file\\b/, " pro Datei"],
        ];

        function translateText(t) {
            if (Object.prototype.hasOwnProperty.call(exact, t)) return exact[t];
            for (var i = 0; i < prefixRules.length; i++) {
                if (prefixRules[i][0].test(t)) return t.replace(prefixRules[i][0], prefixRules[i][1]);
            }
            return null;
        }

        function walk(node) {
            if (node.nodeType === 3) {
                // Manche Streamlit-Hinweistexte enthalten harte Zeilenumbrueche
                // (aus dem JSX-Quelltext), normalisieren vor dem Vergleich.
                var normalized = node.textContent.replace(/\\s+/g, " ").trim();
                var translated = translateText(normalized);
                if (translated !== null) {
                    node.textContent = translated;
                }
            } else if (node.nodeType === 1) {
                for (var i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i]);
            }
        }

        var observer = new MutationObserver(function(mutations) {
            mutations.forEach(function(m) {
                m.addedNodes.forEach(function(n) { walk(n); });
            });
        });
        observer.observe(doc.body, {childList: true, subtree: true});
        walk(doc.body);
    })();
    </script>
    """,
    height=0,
)

CANDIDATE_CLASSES = [
    "IfcWall", "IfcBeam", "IfcSlab", "IfcColumn",
    "IfcPipeSegment", "IfcWindow", "IfcDoor", "IfcRoof", "IfcCovering",
]


def md_escape(text: str) -> str:
    """Escaped Markdown-Sonderzeichen in Anzeigetexten. Zwei Faelle sind
    beobachtet: (1) Attributpfade wie 'Pset_WallCommon' enthalten
    Unterstriche - werden mehrere solche Pfade kommagetrennt in einer
    st.markdown/st.caption-Zeile ausgegeben, interpretiert Markdown ein Paar
    Unterstriche (je einer pro Pfad) als Kursiv-Marker. (2) Attributpfade
    beginnen mit '$' (z.B. '$.schema...'); Streamlits Markdown-Renderer
    unterstuetzt Inline-Mathe per $...$ (KaTeX) - bei zwei Pfaden in einer
    Zeile bilden die beiden Dollarzeichen ein Mathe-Paar, wodurch der
    komplette Text dazwischen als Formel (italic-Mathe-Font) gerendert wird.
    Beides muss escaped werden, sonst sieht die Anzeige kaputt/kursiv aus."""
    for ch in ("\\", "$", "_", "*", "`", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


def generate_default_question(concept, categories):
    """Erzeugt eine deterministische Standard-Leitfrage aus Konzept und
    Zielkategorien (kein LLM-Aufruf, rein Textbaustein) - dient in der GUI
    nur als bearbeitbarer Ausgangspunkt, nicht als Ersatz fuer eine
    praezisere, von Hand formulierte Frage. "unbekannt" wird hier bewusst
    NICHT aufgefuehrt (siehe classify_generic_v3.py und die implizite
    Ergaenzung beim Speichern): es ist kein fachliches Ziel, ueber das man
    "fragen" wuerde, sondern der Ausweich-Fall, falls keine der uebrigen
    Kategorien eindeutig zutrifft."""
    categories = [c for c in categories if c and c != "unbekannt"]
    if not concept or not categories:
        return ""
    if len(categories) == 1:
        joined = categories[0]
    else:
        joined = ", ".join(categories[:-1]) + " oder " + categories[-1]
    return f"Welche der folgenden Kategorien trifft für {concept} zu: {joined}?"


def _path_item_key(form_id, row_id):
    return f"draft_path_item_{form_id}_{row_id}"


def set_path_rows(form_id, paths):
    """Ersetzt die Attributpfad-Zeilen eines Formulars vollstaendig durch
    die gegebene Liste (Vorbefuellung, z.B. beim "Bearbeiten" eines
    bestehenden Anwendungsfalls). Nur gueltig, solange die betroffenen
    Widgets in diesem Skriptlauf noch nicht instanziiert wurden (z.B.
    unmittelbar vor einem st.rerun())."""
    row_ids = []
    for p in paths:
        rid = str(uuid.uuid4())
        st.session_state[_path_item_key(form_id, rid)] = p
        row_ids.append(rid)
    st.session_state[f"draft_path_row_ids_{form_id}"] = row_ids


def add_path_rows(form_id, paths):
    """Haengt neue Attributpfade als zusaetzliche Zeilen an, ohne bereits
    vorhandene Werte zu duplizieren (z.B. Vorschlaege oder ein einzeln
    "uebernommener" Pfad). Wie set_path_rows nur gueltig VOR Instanziierung
    der betroffenen Widgets in diesem Lauf."""
    row_ids_key = f"draft_path_row_ids_{form_id}"
    row_ids = st.session_state.get(row_ids_key, [])
    existing_values = {
        st.session_state.get(_path_item_key(form_id, rid), "") for rid in row_ids
    }
    for p in paths:
        if not p or p in existing_values:
            continue
        rid = str(uuid.uuid4())
        st.session_state[_path_item_key(form_id, rid)] = p
        row_ids.append(rid)
        existing_values.add(p)
    st.session_state[row_ids_key] = row_ids

# Ein Preset je Konzept (nicht je Experiment) - jeweils die auf echten,
# heterogenen Daten validierte Attributauswahl. Die urspruenglichen,
# experimentspezifischen Konfigurationsdateien (data/usecase_*.json) bleiben
# unveraendert auf der Festplatte, damit die in der Arbeit zitierten
# Auswertungen reproduzierbar bleiben - hier wird nur gezielt eine Variante
# je Konzept fuer die GUI ausgewaehlt.
PRESET_FILES = {
    "Tragende Funktion (Wand)": "data/usecase_tragend_gui.json",
    "Trägermaterial (Träger)": "data/usecase_traeger_filtered.json",
    "Rohrmedium (Rohr)": "data/usecase_rohrmedium.json",
}

# Vom Nutzer gespeicherte PROJEKTE: buendeln ALLE zu einem Zeitpunkt
# konfigurierten Anwendungsfaelle (nicht nur einen einzelnen) unter einem
# Namen. Vorgesehener Ablauf: einmal pro Projekt die passenden Anwendungsfaelle
# (Attributpfade je nach dortiger Autorensoftware) zusammenstellen und
# speichern; bei einem spaeteren, neueren Planungsstand desselben Projekts
# laden, einzelne Anwendungsfaelle bearbeiten sowie welche hinzufuegen/
# entfernen. Bewusst ausserhalb des Git-Repos gehalten (.gitignore), da
# projektspezifisch.
USER_PRESETS_DIR = "data/user_presets"


def list_project_presets():
    projects = {}
    if os.path.isdir(USER_PRESETS_DIR):
        for fname in sorted(os.listdir(USER_PRESETS_DIR)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(USER_PRESETS_DIR, fname)
            try:
                cfg = json.load(open(path, encoding="utf-8"))
                label = cfg.get("preset_name", fname[:-5])
                projects[label] = path
            except Exception:
                continue
    return projects


def save_project_preset(name, usecases):
    os.makedirs(USER_PRESETS_DIR, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in name).strip("_") or "projekt"
    path = os.path.join(USER_PRESETS_DIR, f"{slug}.json")
    clean_usecases = [
        {
            "seed_classes": uc["seed_classes"],
            "concept": uc["concept"],
            "concept_question": uc["concept_question"],
            "categories": uc["categories"],
            "attribute_paths": uc["attribute_paths"],
        }
        for uc in usecases
    ]
    json.dump(
        {"preset_name": name, "usecases": clean_usecases},
        open(path, "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )
    return path


def ollama_reachable(host="http://localhost:11434", timeout=1):
    try:
        urllib.request.urlopen(host, timeout=timeout)
        return True
    except Exception:
        return False


CLAUDE_BACKEND_LABEL = "Claude API (Cloud)"
OLLAMA_BACKEND_LABEL = "Ollama (lokal)"


def get_active_client():
    """Liest die in der Seitenleiste gewaehlte Backend-Einstellung und liefert
    den passenden llm_client. Wirft RuntimeError mit einer fuer die GUI
    verstaendlichen Meldung, falls Claude gewaehlt ist, aber noch kein
    API-Key eingetragen wurde - so muss das nicht an jeder der drei
    Aufrufstellen einzeln geprueft werden."""
    if st.session_state.get("llm_backend_choice") == CLAUDE_BACKEND_LABEL:
        api_key = st.session_state.get("anthropic_api_key", "").strip()
        if not api_key:
            raise RuntimeError(
                "Bitte zuerst einen Anthropic API-Key in der Seitenleiste unter "
                "„LLM-Backend“ eintragen."
            )
        return get_client("claude", api_key=api_key)
    return get_client("ollama")


def load_preset(path):
    return json.load(open(path, encoding="utf-8"))


def get_schema(seed_class):
    """Schema-Kontext pro Bauteilklasse cachen, damit er bei mehreren
    Anwendungsfaellen mit derselben Klasse nicht mehrfach extrahiert wird."""
    cache_key = f"schema_cache_{seed_class}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = extract_schema_context_multi(
            st.session_state.ifc_paths, seed_class=seed_class
        )
    return st.session_state[cache_key]


def get_metadata(seed_class):
    cache_key = f"metadata_cache_{seed_class}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = extract_instance_metadata_multi(
            st.session_state.ifc_paths, seed_class=seed_class
        )
    return st.session_state[cache_key]


def get_bsdd_properties(seed_class):
    """Wie get_schema/get_metadata: einmal pro Bauteilklasse cachen, kein
    wiederholter Netzwerkzugriff bei mehreren Anwendungsfaellen mit
    derselben Klasse."""
    cache_key = f"bsdd_cache_{seed_class}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = get_class_properties(seed_class)
    return st.session_state[cache_key]


st.title("IFC-Attribut-Klassifikation")

if "usecases" not in st.session_state:
    st.session_state.usecases = []
# Bewusst KEIN "if 'class_counts' not in st.session_state: ... = {}" hier:
# das wuerde bei jedem Lauf unbedingt ausgefuehrt und wieder einen leeren
# Dict eintragen, noch bevor Schritt 2 pruefen kann, ob die Bauteilklassen
# ueberhaupt schon einmal geprueft wurden - der Button dort wuerde dadurch
# nach dem allerersten Klick wirkungslos verschwinden, ohne dass die
# eigentliche Pruefung je gelaufen waere. Stattdessen ueberall mit .get(...)
# lesen, wo class_counts noch nicht existieren koennte.

# Projektverwaltung (Speichern/Laden) ist bewusst KEIN Schritt im
# eigentlichen Arbeitsablauf, sondern eine davon unabhaengige, jederzeit
# verfuegbare Aktion - deshalb in der (nativ ein-/ausklappbaren) Seitenleiste
# statt zwischen Schritt 2 und 3 eingebettet.
with st.sidebar:
    st.subheader(
        "Projekt",
        help=(
            "Ein Projekt buendelt alle aktuell konfigurierten Anwendungsfaelle "
            "unter einem Namen - einmal fuer dieses Projekt zusammenstellen, "
            "speichern und bei einem spaeteren, neueren Planungsstand desselben "
            "Projekts wiederladen (einzelne Anwendungsfaelle danach weiterhin "
            "bearbeitbar sowie hinzufuegbar/entfernbar)."
        ),
    )
    project_presets = list_project_presets()
    no_selection = "(keins)" if project_presets else "(noch keine gespeicherten Projekte)"
    # Dropdown und Button immer anzeigen (auch ohne gespeicherte Projekte),
    # statt sie je nach Zustand ein-/auszublenden - sonst wirkt es, als
    # fehle die Funktion, wenn man noch nichts gespeichert hat, statt dass
    # sie nur leer ist.
    project_choice = st.selectbox(
        "Gespeichertes Projekt laden", [no_selection] + list(project_presets.keys()),
        key="project_load_choice",
    )
    if st.button("Projekt laden"):
        if project_choice == no_selection:
            st.warning("Bitte zuerst ein gespeichertes Projekt auswählen.")
        else:
            loaded = json.load(open(project_presets[project_choice], encoding="utf-8"))
            st.session_state.usecases = [
                {**uc, "id": str(uuid.uuid4())} for uc in loaded["usecases"]
            ]
            st.session_state.editing_id = None
            st.rerun()

    project_save_name = st.text_input(
        "Projekt speichern unter", key="project_save_name",
        placeholder="z.B. Projekt Musterstraße",
    )
    if st.button("Projekt speichern"):
        if not project_save_name:
            st.warning("Bitte einen Namen für das Projekt angeben.")
        elif not st.session_state.usecases:
            st.warning("Es sind noch keine Anwendungsfälle konfiguriert.")
        else:
            saved_path = save_project_preset(project_save_name, st.session_state.usecases)
            st.session_state["_project_just_saved"] = saved_path
            st.rerun()

    if st.session_state.get("_project_just_saved"):
        st.success(f"Projekt gespeichert: {st.session_state.pop('_project_just_saved')}")

    st.subheader(
        "LLM-Backend",
        help=(
            "Ollama laeuft komplett lokal/offline und ist kostenlos, aber auf "
            "diesem Rechner spuerbar langsamer als eine Cloud-API. Claude "
            "schickt die Attributwerte der ausgewaehlten Bauteile zur "
            "Klassifikation an die Anthropic-API - fuer Projekte mit "
            "Datenschutzanforderungen (siehe Interviewgrundlage) bleibt "
            "Ollama die vorgesehene Wahl."
        ),
    )
    backend_choice = st.radio(
        "Backend", [OLLAMA_BACKEND_LABEL, CLAUDE_BACKEND_LABEL],
        key="llm_backend_choice", label_visibility="collapsed",
    )
    if backend_choice == CLAUDE_BACKEND_LABEL:
        st.text_input(
            "Anthropic API-Key", key="anthropic_api_key", type="password",
            placeholder="sk-ant-...",
            help=(
                "Wird nur im Arbeitsspeicher dieser Sitzung gehalten, nicht auf "
                "die Festplatte geschrieben und nicht in gespeicherten Projekten "
                "abgelegt."
            ),
        )

    # Status am unteren RAND der Seitenleiste (nicht nur "als letztes
    # Element", das liesse bei kurzem Projekt-Inhalt einfach Leerraum
    # darunter) - eigener Container mit stabilem CSS-Key, den die
    # Flex-Regel oben (".st-key-sidebar_status") an den unteren Rand
    # schiebt. Bei Nichterreichbarkeit zusaetzlich die Installations-
    # anleitung direkt darunter, da dafuer im Badge kein Platz ist und es
    # sich um handlungsrelevante Information handelt.
    with st.container(key="sidebar_status"):
        st.divider()
        if backend_choice == CLAUDE_BACKEND_LABEL:
            # Kein Live-Ping gegen die Anthropic-API nur fuer die Statusanzeige
            # (wuerde unnoetig Kosten verursachen) - hier wird nur geprueft, ob
            # ueberhaupt ein Key eingetragen wurde, nicht ob er gueltig ist.
            if st.session_state.get("anthropic_api_key", "").strip():
                st.markdown(
                    '<div class="gui-status-badge gui-status-ok">● Claude API-Key gesetzt</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="gui-status-badge gui-status-error">● Claude API-Key fehlt</div>',
                    unsafe_allow_html=True,
                )
        elif ollama_reachable():
            st.markdown(
                '<div class="gui-status-badge gui-status-ok">● Ollama erreichbar</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="gui-status-badge gui-status-error">● Ollama nicht erreichbar</div>',
                unsafe_allow_html=True,
            )
            st.error(
                "Ollama muss lokal installiert und gestartet sein:\n\n"
                "1. Installieren: https://ollama.com/download\n"
                "2. Modell laden: `ollama pull qwen2.5:7b-instruct`\n"
                "3. Diese Seite neu laden."
            )

# --- Schritt 1: Dateien auswaehlen ---
# Bewusst "auswaehlen" statt "hochladen": die Dateien werden nur lokal von
# der Festplatte eingelesen, es findet keine Internet-Uebertragung statt -
# "hochladen" wuerde bei Interviewpartnern den falschen Eindruck erwecken.
st.header("1. IFC-Datei(en) auswählen")
uploaded = st.file_uploader("IFC-Datei(en)", type=["ifc"], accept_multiple_files=True)

if uploaded:
    upload_signature = tuple(sorted((f.name, f.size) for f in uploaded))
    if upload_signature != st.session_state.get("_upload_signature"):
        if "tmp_dir" not in st.session_state:
            st.session_state.tmp_dir = tempfile.mkdtemp(prefix="ifc_gui_")
        ifc_paths = []
        for f in uploaded:
            path = os.path.join(st.session_state.tmp_dir, f.name)
            with open(path, "wb") as out:
                out.write(f.getbuffer())
            ifc_paths.append(path)
        st.session_state.ifc_paths = ifc_paths
        st.session_state["_upload_signature"] = upload_signature
        # Neue Datei(en) -> vorherige Klassen-/Schema-Caches sind ungueltig
        st.session_state.pop("class_counts", None)
        for key in list(st.session_state.keys()):
            if key.startswith("schema_cache_") or key.startswith("metadata_cache_"):
                del st.session_state[key]
        st.success(f"{len(ifc_paths)} Datei(en) bereit.")

# --- Optional (zwischen Schritt 1 und 2): Bauteilklassen pruefen ---
if st.session_state.get("ifc_paths"):
    st.subheader("Optional: Verfügbare Bauteilklassen prüfen")

    if "class_counts" not in st.session_state:
        st.caption("Bei grossen Dateien kann dieser Schritt etwas dauern.")
        if st.button("Verfügbare Bauteilklassen prüfen"):
            with st.spinner("Prüfe verfügbare Bauteilklassen..."):
                counts = {}
                # Statt eine feste Kandidatenliste einzeln abzufragen (immer
                # unvollstaendig - IFC kennt Dutzende Bauteilklassen, u.a.
                # IfcStair, IfcBuildingElementProxy, die anfangs fehlten),
                # einmal IfcElement abfragen (der IFC-Oberbegriff fuer alle
                # physischen Bauteile, schliesst per Vererbung saemtliche
                # Unterklassen ein) und die tatsaechlich vorkommenden Klassen
                # direkt aus den gefundenen Instanzen ablesen. Jede Datei
                # bleibt dabei nur einmal geoeffnet. Hinter einem Button statt
                # automatisch, da dies bei grossen Dateien lange dauern kann
                # und der Nutzer selbst entscheiden soll, wann das anlaeuft.
                for p in st.session_state.ifc_paths:
                    model = ifcopenshell.open(p)
                    try:
                        elements = model.by_type("IfcElement")
                    except RuntimeError:
                        continue
                    for el in elements:
                        cls = el.is_a()
                        counts[cls] = counts.get(cls, 0) + 1
                st.session_state.class_counts = counts
                st.rerun()

    if st.session_state.get("class_counts"):
        st.caption(
            "Gefundene Klassen: "
            + ", ".join(f"{c} ({n})" for c, n in st.session_state.class_counts.items())
        )

# --- Schritt 2: Anwendungsfaelle verwalten ---
# Ueberschrift IMMER sichtbar (auch vor Schritt 1), damit der gesamte
# Arbeitsablauf von Anfang an erkennbar ist - nur der eigentliche Inhalt
# bleibt an Schritt 1 gekoppelt (ohne Datei gibt es kein Schema, gegen das
# sich Anwendungsfaelle konfigurieren liessen).
st.header("2. Anwendungsfälle")
if not st.session_state.get("ifc_paths"):
    st.caption("Lade zuerst mindestens eine IFC-Datei in Schritt 1 hoch.")
if st.session_state.get("ifc_paths"):
    if st.session_state.usecases:
        st.write("**Konfigurierte Anwendungsfälle:**")
        for uc in st.session_state.usecases:
            cols = st.columns([3, 1, 1])
            with cols[0]:
                st.markdown(
                    f"- **{md_escape(uc['concept'])}** "
                    f"({md_escape(', '.join(uc['seed_classes']))}) — "
                    f"Kategorien: {md_escape(', '.join(uc['categories']))}  \n"
                    f"  Attribute: {md_escape(', '.join(uc['attribute_paths']))}"
                )
                # Instanzen-/Kombinationen-Vorschau (frueher in Schritt 3
                # angezeigt, siehe dortiger Kommentar) - hier direkt bei der
                # jeweiligen Konfiguration, da sie zu dieser gehoert.
                n_instances = 0
                n_combos = 0
                n_free = 0
                for seed_class in uc["seed_classes"]:
                    per_instance, _ = get_schema(seed_class)
                    combos = extract_combinations(per_instance, uc["attribute_paths"])
                    n_instances += len(per_instance)
                    for combo, keys in combos.items():
                        if any(v != "(nicht vorhanden)" for _, v in combo):
                            n_combos += 1
                        else:
                            n_free += len(keys)
                uc["_n_instances"] = n_instances
                uc["_n_combos"] = n_combos
                extra = f", {n_free} ohne jegliches Signal → direkt \"unbekannt\" ohne LLM-Aufruf" if n_free else ""
                st.caption(
                    f"{n_instances} Instanzen, {n_combos} einzigartige Kombinationen "
                    f"→ {n_combos} LLM-Aufrufe{extra}"
                )
            with cols[1]:
                if st.button("Bearbeiten", key=f"editbtn_{uc['id']}"):
                    st.session_state.draft_form_id = st.session_state.get("draft_form_id", 0) + 1
                    new_form_id = st.session_state.draft_form_id
                    st.session_state["draft_seed_class"] = list(uc["seed_classes"])
                    st.session_state["draft_concept"] = uc["concept"]
                    st.session_state["draft_concept_question"] = uc["concept_question"]
                    st.session_state["draft_categories"] = ", ".join(uc["categories"])
                    set_path_rows(new_form_id, uc["attribute_paths"])
                    st.session_state.editing_id = uc["id"]
                    st.rerun()
            with cols[2]:
                if st.button("Entfernen", key=f"remove_{uc['id']}"):
                    st.session_state.usecases = [
                        u for u in st.session_state.usecases if u["id"] != uc["id"]
                    ]
                    if st.session_state.get("editing_id") == uc["id"]:
                        st.session_state.editing_id = None
                    st.rerun()

    editing_id = st.session_state.get("editing_id")
    editing_uc = (
        next((u for u in st.session_state.usecases if u["id"] == editing_id), None)
        if editing_id else None
    )

    class_options = list(st.session_state.get("class_counts", {}).keys()) or CANDIDATE_CLASSES

    # "Vorlage uebernehmen" ist ein eigener, von der Eigenerstellung
    # unabhaengiger Punkt: eine Vorlage wird direkt und vollstaendig als
    # neuer Anwendungsfall uebernommen (kein Umweg mehr ueber das Formular
    # unten) - wer sie danach anpassen will, nutzt "Bearbeiten" in der Liste
    # oben wie bei jedem anderen Anwendungsfall auch.
    st.subheader("Vorlage übernehmen")
    preset_pick_col, preset_button_col = st.columns([3, 1])
    with preset_pick_col:
        preset_choice = st.selectbox(
            "Vorlage", list(PRESET_FILES.keys()), key="preset_pick",
            label_visibility="collapsed",
        )
    with preset_button_col:
        if st.button("Vorlage hinzufügen"):
            preset = load_preset(PRESET_FILES[preset_choice])
            categories = list(preset["categories"])
            if "unbekannt" not in categories:
                categories.append("unbekannt")
            st.session_state.usecases.append({
                "id": str(uuid.uuid4()),
                "seed_classes": [preset["seed_class"]],
                "concept": preset["concept"],
                "concept_question": preset["concept_question"],
                "categories": categories,
                "attribute_paths": list(preset["attribute_paths"]),
            })
            st.rerun()

    # EIN gemeinsames Formular fuer eigenes Hinzufuegen UND Bearbeiten, das
    # bei jedem Lauf immer dieselben Widgets instanziiert (nur deren
    # Vorbefuellung und das Verhalten des Absenden-Buttons unterscheiden
    # sich je nach Modus). Wichtig: ein Widget, das je nach Modus mal
    # gezeichnet wird und mal nicht, verliert seinen Wert zwischen Laeufen,
    # sobald es fuer einen Lauf uebersprungen wird - deshalb hier bewusst
    # kein "if editing: ... else: ..." um ganze Formularabschnitte herum.
    if "draft_form_id" not in st.session_state:
        st.session_state.draft_form_id = 0
    form_id = st.session_state.draft_form_id
    # Die Attributpfade bekommen einen durchnummerierten Key (form_id
    # hochzaehlen leert sie fuer den naechsten Durchgang implizit). Die
    # uebrigen Felder haben statische Keys - ein direktes Zuruecksetzen
    # ("st.session_state[concept_key] = ...") ist hier aber NICHT erlaubt,
    # sobald das jeweilige Widget in diesem Lauf schon instanziiert wurde
    # (Streamlit wirft dann StreamlitAPIException, auch unmittelbar vor
    # einem st.rerun()). Deshalb ueber ein Pending-Flag: submit/Abbrechen
    # setzen nur das Flag, die tatsaechliche Leerung passiert hier, GANZ
    # OBEN, auf dem naechsten Lauf, bevor die Widgets instanziiert werden.
    seed_class_key = "draft_seed_class"
    concept_key = "draft_concept"
    question_key = "draft_concept_question"
    categories_key = "draft_categories"
    row_ids_key = f"draft_path_row_ids_{form_id}"
    new_path_key = f"draft_path_new_{form_id}"
    dropped_key = f"draft_dropped_paths_{form_id}"

    if st.session_state.pop("_draft_reset_pending", False):
        st.session_state[seed_class_key] = []
        st.session_state[concept_key] = ""
        st.session_state[question_key] = ""
        st.session_state[categories_key] = ""

    # Zeilen-Buchhaltung fuer die Attributpfade MUSS VOR "has_draft_content"
    # (siehe unten) passieren, nicht erst innerhalb des Expander-Blocks:
    # sonst wuerde has_draft_content beim genau dem Lauf, der gerade einen
    # neuen Pfad eintraegt, noch den ALTEN (leeren) Stand sehen, da der
    # Expander-"expanded"-Wert bereits VOR seinem eigenen Koerper feststehen
    # muss (siehe Kommentar dort) - beobachteter Bug: das Formular klappte
    # nach dem allerersten eingetragenen Pfad wieder zu.
    pending_key = "_pending_paths_append"
    if st.session_state.get(pending_key):
        add_path_rows(form_id, st.session_state.pop(pending_key))

    if row_ids_key not in st.session_state:
        st.session_state[row_ids_key] = []

    # Eine Zeile, die der Nutzer geleert und dann verlassen hat
    # (rausgetabt/-geklickt), verschwindet automatisch - kein expliziter
    # Loesch-Klick noetig. Muss VOR der Instanziierung der Zeilen-Widgets
    # passieren (siehe unten).
    st.session_state[row_ids_key] = [
        rid for rid in st.session_state[row_ids_key]
        if st.session_state.get(_path_item_key(form_id, rid), "").strip()
    ]

    # Das "neuer Pfad"-Feld: sobald befuellt, wird daraus eine echte,
    # eigene Zeile und das Feld selbst wieder geleert (fuer den naechsten
    # Pfad) - auch das muss VOR seiner eigenen Instanziierung weiter unten
    # passieren.
    new_value = st.session_state.get(new_path_key, "").strip()
    if new_value:
        add_path_rows(form_id, [new_value])
        st.session_state[new_path_key] = ""

    # st.expander hat in dieser Streamlit-Version kein "key" - "expanded"
    # wird daher bei JEDEM Skriptlauf neu ausgewertet, nicht nur beim
    # ersten. Das zeilenweise Attributpfad-UI loest bei jedem einzelnen
    # eingetippten Pfad einen eigenen Skriptlauf aus (nicht erst beim
    # Verlassen des ganzen Formulars wie frueher beim Mehrzeilen-Textfeld) -
    # ohne diesen Zusatz wuerde das Formular also nach JEDEM Pfad wieder
    # zuklappen. Deshalb bleibt es offen, sobald bereits Entwurfsinhalt
    # vorhanden ist (Konzept eingetragen oder mindestens eine Pfad-Zeile),
    # nicht nur beim Bearbeiten.
    has_draft_content = bool(st.session_state.get(concept_key)) or bool(st.session_state.get(row_ids_key))
    expander_title = "Anwendungsfall bearbeiten" if editing_uc else "+ Eigenen Anwendungsfall erstellen"
    with st.expander(
        expander_title,
        expanded=bool(editing_uc) or has_draft_content or not st.session_state.usecases,
    ):
        if editing_uc:
            st.caption(
                f"✏️ Bearbeite: **{md_escape(editing_uc['concept'])}** "
                f"({md_escape(', '.join(editing_uc['seed_classes']))})"
            )

        concept = st.text_input("Konzept", key=concept_key, placeholder="z.B. Tragende Funktion")

        # Mehrfachauswahl, damit dieselbe Leitfrage/Kategorien/Attributpfade
        # fuer mehrere Bauteilklassen zugleich geprueft werden koennen (z.B.
        # "tragend?" fuer Waende UND Stuetzen in einem Anwendungsfall).
        # Wurden die Bauteilklassen bereits geprueft (class_options aus
        # class_counts), stehen diese zur Auswahl; sonst ist class_options
        # nur die grobe CANDIDATE_CLASSES-Liste als Vorschlag -
        # accept_new_options erlaubt in beiden Faellen zusaetzlich freies
        # Eintippen (z.B. wenn eine Klasse fehlt oder gar nicht geprueft wurde).
        # WICHTIG: options MUSS pro Lauf statisch/unabhaengig vom eigenen
        # Widget-Wert bleiben (nicht z.B. "class_options + current_seed_classes"
        # aus st.session_state[seed_class_key] zusammenbauen). Ein solches
        # Ruecklesen des eigenen letzten Werts in die eigenen options fuehrte
        # zu einer instabilen Rueckkopplung: in Kombination mit
        # accept_new_options=True wurde die Auswahl bei jeder weiteren
        # Interaktion (Klick/Tippen in ein anderes Feld) im echten Browser
        # wieder auf leer zurueckgesetzt, obwohl AppTest (das set_value()
        # nutzt und damit den echten Frontend-Sync fuer accept_new_options
        # umgeht) das Problem nicht zeigte. accept_new_options haelt bereits
        # von sich aus zuvor eingetippte Werte in session_state - ein
        # manuelles Zurueckspeisen ist weder noetig noch sicher.
        seed_classes = st.multiselect(
            "Bauteilklasse(n)", class_options, key=seed_class_key,
            accept_new_options=True,
            help=(
                "Mehrfachauswahl moeglich. Wurden die verfuegbaren Bauteilklassen "
                "oben geprueft, stehen die gefundenen zur Auswahl; sonst frei "
                "eintippen (z.B. IfcWall)."
            ),
        )
        categories_raw = st.text_input(
            "Zielkategorien (kommagetrennt)", key=categories_key,
            placeholder="z.B. tragend, nicht tragend",
            help=(
                "\"unbekannt\" muss hier nicht extra angegeben werden - "
                "diese Kategorie steht bei der Klassifikation immer "
                "automatisch zur Verfügung, falls keine der übrigen "
                "Kategorien eindeutig zutrifft."
            ),
        )

        # Leitfrage: wird, solange der Nutzer sie noch nicht selbst
        # angefasst hat (Feld noch leer), deterministisch aus Konzept +
        # Zielkategorien vorbefuellt (siehe generate_default_question) -
        # sobald etwas eingetippt wird, gilt das Feld als "belegt" und wird
        # nicht mehr automatisch ueberschrieben. Beim Bearbeiten eines
        # bestehenden Anwendungsfalls ist das Feld bereits durch dessen
        # gespeicherte Frage belegt, wird also ebenfalls nicht ersetzt.
        if not st.session_state.get(question_key):
            auto_question = generate_default_question(
                concept, [c.strip() for c in categories_raw.split(",") if c.strip()]
            )
            if auto_question:
                st.session_state[question_key] = auto_question
        concept_question = st.text_input(
            "Leitfrage", key=question_key,
            placeholder="z.B. Ist das Bauteil tragend oder nicht tragend?",
            help=(
                "Wird automatisch aus Konzept und Zielkategorien vorgeschlagen, "
                "sobald beide ausgefüllt sind - frei überschreibbar."
            ),
        )

        st.write("**Attributpfade** (ein Pfad pro Zeile):")
        # WICHTIG: st.rerun() darf hier erst NACH der Schleife aufgerufen
        # werden, nicht mittendrin. Ein st.rerun() waehrend die Schleife
        # noch laeuft bricht den Skriptlauf ab, BEVOR die Widgets der noch
        # nicht erreichten (spaeteren) Zeilen in diesem Lauf ueberhaupt
        # registriert wurden - Streamlit raeumt deren session_state dann
        # als "in diesem Lauf nicht gesehen" auf, obwohl row_ids sie
        # weiterhin fuehrt (beobachtet: das Entfernen einer mittleren Zeile
        # loeschte dadurch zusaetzlich den Wert der letzten Zeile). Daher
        # erst den Klick vormerken, die Schleife vollstaendig durchlaufen
        # (alle Widgets werden dabei ganz normal registriert) und erst
        # danach mutieren/rerunnen.
        remove_rid = None
        for rid in st.session_state[row_ids_key]:
            path_col, remove_col = st.columns([6, 1])
            with path_col:
                st.text_input(
                    "Attributpfad", key=_path_item_key(form_id, rid),
                    label_visibility="collapsed",
                )
            with remove_col:
                if st.button("✕", key=f"draft_path_remove_{form_id}_{rid}"):
                    remove_rid = rid
        st.text_input(
            "Neuer Attributpfad", key=new_path_key,
            label_visibility="collapsed",
            placeholder="z.B. $.schema.Pset_WallCommon.LoadBearing",
        )
        if remove_rid is not None:
            st.session_state[row_ids_key].remove(remove_rid)
            st.rerun()

        if st.button("Attributpfade vorschlagen lassen (LLM)"):
            if not seed_classes:
                st.warning("Bitte zuerst mindestens eine Bauteilklasse auswählen.")
            elif not concept or not concept_question or not categories_raw:
                st.warning("Bitte zuerst Konzept, Leitfrage und Kategorien angeben.")
            else:
                try:
                    client = get_active_client()
                except RuntimeError as exc:
                    st.error(str(exc))
                    client = None
                if client is not None:
                    with st.spinner("Extrahiere Schema und frage LLM nach passenden Attributen..."):
                        # Schema-Kontexte aller gewaehlten Klassen zusammenfuehren,
                        # damit ein einzelner LLM-Aufruf Pfade fuer alle vorschlagen
                        # kann (statt eines Aufrufs je Klasse).
                        merged_context = {}
                        merged_bsdd = {}
                        for sc in seed_classes:
                            _, schema_context = get_schema(sc)
                            for path, examples in schema_context.items():
                                bucket = merged_context.setdefault(path, [])
                                for ex in examples:
                                    if ex not in bucket:
                                        bucket.append(ex)
                            # Optionale Anreicherung mit autoritativen bSDD-
                            # Definitionen genormter Pset-Attribute (einziger
                            # Netzwerkzugriff der App - uebertragen wird nur
                            # der generische Klassenname, z.B. "IfcBeam",
                            # keine Projektdaten). Bei Nichterreichbarkeit
                            # liefert get_bsdd_properties ein leeres dict statt
                            # abzustuerzen, der Vorschlag funktioniert dann
                            # wie zuvor rein aus dem Schema-Kontext.
                            merged_bsdd.update(get_bsdd_properties(sc))
                        suggested = suggest_attribute_paths(
                            client, merged_context, "/".join(seed_classes),
                            concept, concept_question, categories_raw,
                            bsdd_properties=merged_bsdd,
                        )
                        # Das LLM schoepft das erlaubte Maximum an Pfaden in der
                        # Praxis durchgehend aus, auch wenn ein Teil davon keine
                        # neue Instanzabdeckung bringt (siehe Docstring von
                        # trim_redundant_paths) - deshalb hier deterministisch
                        # nachtraeglich kuerzen, ohne weiteren LLM-Aufruf.
                        per_instance_by_class = [get_schema(sc)[0] for sc in seed_classes]
                        trim_result = trim_redundant_paths(suggested, per_instance_by_class)
                        suggested = trim_result["kept"]
                    # Wie bei "_project_just_saved": eine Meldung direkt vor
                    # st.rerun() wuerde durch den Rerun sofort wieder
                    # verschwinden, bevor sie sichtbar wird - deshalb
                    # zwischenspeichern und beim naechsten Lauf anzeigen.
                    if merged_bsdd:
                        n_matched = sum(1 for p in merged_context if p in merged_bsdd)
                        st.session_state["_bsdd_suggest_status"] = (
                            f"bSDD erreichbar: {n_matched} von {len(merged_context)} Attributpfaden "
                            "mit autoritativer Definition angereichert."
                        )
                    else:
                        st.session_state["_bsdd_suggest_status"] = (
                            "bSDD nicht erreichbar - Vorschlag basiert nur auf Beispielwerten."
                        )
                    # Nicht uebernommene Vorschlaege bleiben (anders als die
                    # einmaligen Statusmeldungen oben) bestehen, bis der Nutzer
                    # sie einzeln uebernimmt oder das Formular zurueckgesetzt
                    # wird - siehe Anzeige mit "Übernehmen"-Buttons weiter unten.
                    if trim_result["dropped"]:
                        st.session_state[dropped_key] = trim_result["dropped"]
                    st.session_state[pending_key] = suggested
                    st.rerun()

        if st.session_state.get("_bsdd_suggest_status"):
            st.caption(st.session_state.pop("_bsdd_suggest_status"))

        dropped_paths = st.session_state.get(dropped_key, [])
        if dropped_paths:
            st.caption(
                f"{len(dropped_paths)} vorgeschlagene Pfade ohne zusätzliche Instanzabdeckung "
                "nicht übernommen - trotzdem hinzufügen?"
            )
            # st.rerun() erst NACH der Schleife (siehe ausfuehrliche
            # Begruendung bei der Zeilen-Schleife oben) - sonst wuerden
            # spaetere, in diesem Lauf noch nicht erreichte Eintraege dieser
            # Liste ihren Zustand verlieren.
            adopt_p = None
            for i, p in enumerate(dropped_paths):
                # Breiteres Verhaeltnis als bei der "✕"-Entfernen-Spalte
                # (dort reicht ein Zeichen) - "Übernehmen" braucht Platz fuer
                # den vollen Text ohne Zeilenumbruch.
                drop_col, adopt_col = st.columns([4, 2])
                with drop_col:
                    st.text_input(
                        "Nicht übernommener Vorschlag", value=p,
                        key=f"draft_dropped_display_{form_id}_{i}",
                        label_visibility="collapsed", disabled=True,
                    )
                with adopt_col:
                    if st.button("Übernehmen", key=f"draft_dropped_adopt_{form_id}_{i}"):
                        adopt_p = p
            if adopt_p is not None:
                add_path_rows(form_id, [adopt_p])
                st.session_state[dropped_key] = [x for x in dropped_paths if x != adopt_p]
                st.rerun()

        # Kardinalitaets-Check: zeigt live, wie stark jeder aktuell
        # eingetragene Attributpfad die Anzahl der Kombinationen (=
        # LLM-Aufrufe im Klassifikationsschritt) erhoeht, gerechnet gegen
        # die vollstaendigen (nicht auf 8 Beispielwerte gekappten)
        # per_instance-Daten - siehe attribute_diagnostics.py. Rein
        # informativ, kein Zwang: hoch-kardinale Pfade (z.B. Positions-
        # nummern/IDs, die pro Instanz praktisch einzigartig sind)
        # untergraben sonst unbemerkt das Lookup-once-Prinzip.
        current_paths = [
            st.session_state.get(_path_item_key(form_id, rid), "").strip()
            for rid in st.session_state[row_ids_key]
        ]
        current_paths = [p for p in current_paths if p]
        if seed_classes and current_paths and st.session_state.get("ifc_paths"):
            per_instance_by_class = [get_schema(sc)[0] for sc in seed_classes]
            diagnostics = path_diagnostics_multi(per_instance_by_class, current_paths)
            if diagnostics:
                # Standardmaessig eingeklappt - reine Zusatzinfo, kein
                # Bestandteil des eigentlichen Workflows.
                with st.expander("Details (Kardinalitäts-Check)"):
                    st.write("Wie stark erhöht jeder Pfad die Anzahl der Kombinationen?")
                    rows = []
                    for path in current_paths:
                        d = diagnostics.get(path)
                        if not d:
                            continue
                        pct = f"{d['ratio']:.0%}" if d["n_populated"] else "–"
                        delta = d["n_with_all"] - d["n_without"]
                        rows.append({
                            "Attributpfad": path,
                            "Eindeutige Werte": f"{d['n_distinct_max']} von {d['n_populated']} ({pct})",
                            "Kombinationen ohne diesen Pfad": d["n_without"],
                            "Kombinationen mit allen Pfaden": d["n_with_all"],
                            "Zusätzliche Aufrufe durch diesen Pfad": f"+{delta}" if delta else "0",
                        })
                    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        submit_col, cancel_col = st.columns([3, 1])
        with submit_col:
            submit_label = "Änderungen speichern" if editing_uc else "Anwendungsfall hinzufügen"
            if st.button(submit_label, type="primary"):
                categories = [c.strip() for c in categories_raw.split(",") if c.strip()]
                # "unbekannt" ist immer implizit verfuegbar (siehe
                # classify_generic_v3.CLASSIFICATION_PROMPT_V3 und der
                # deterministische Zero-Signal-Fall in Schritt 3) - der
                # Nutzer muss sie nicht selbst eintragen, wird aber, falls
                # schon vorhanden (z.B. aus einer alten Projektdatei),
                # nicht dupliziert.
                if "unbekannt" not in categories:
                    categories.append("unbekannt")
                attribute_paths = [
                    st.session_state.get(_path_item_key(form_id, rid), "").strip()
                    for rid in st.session_state[row_ids_key]
                ]
                attribute_paths = [p for p in attribute_paths if p]
                if seed_classes and concept and categories and attribute_paths:
                    new_config = {
                        "seed_classes": list(seed_classes),
                        "concept": concept,
                        "concept_question": concept_question,
                        "categories": categories,
                        "attribute_paths": attribute_paths,
                    }
                    if editing_uc:
                        for u in st.session_state.usecases:
                            if u["id"] == editing_uc["id"]:
                                u.update(new_config)
                        st.session_state.editing_id = None
                    else:
                        st.session_state.usecases.append({"id": str(uuid.uuid4()), **new_config})
                    # Formular fuer den naechsten Durchgang leeren (siehe
                    # Pending-Flag weiter oben - ein direktes Zuruecksetzen
                    # hier waere zu spaet, die Widgets liefen in diesem
                    # Lauf bereits).
                    st.session_state.draft_form_id += 1
                    st.session_state["_draft_reset_pending"] = True
                    st.rerun()
                else:
                    st.warning("Bitte mindestens eine Bauteilklasse, Konzept, Kategorien und mindestens einen Attributpfad angeben.")
        with cancel_col:
            if editing_uc and st.button("Abbrechen"):
                st.session_state.editing_id = None
                st.session_state.draft_form_id += 1
                st.session_state["_draft_reset_pending"] = True
                st.rerun()

# --- Schritt 3: Klassifizieren ---
# Zweistufig (Nutzerentscheidung nach Testlauf): Stufe 1 klassifiziert NUR
# anhand der konfigurierten Pset-Attributpfade - Instanzen ganz ohne
# jegliches Signal brauchen dafuer gar keinen LLM-Aufruf, da der Prompt
# ohnehin zwingend "unbekannt" verlangt, wenn nichts bekannt ist (wird daher
# direkt im Code entschieden, spart Zeit/Kosten). Der Bauteilname wird NICHT
# mehr automatisch als Ersatzsignal herangezogen - das kostet unnoetig viele
# zusaetzliche LLM-Aufrufe (ein Aufruf pro eindeutigem Namen) fuer ein
# Signal, das sich als deutlich weniger verlaesslich erwiesen hat als ein
# Pset-Attribut (z.B. "Unterzug" faelschlich als nicht tragend erkannt).
# Stattdessen: Stufe 2 ist ein separater, vom Nutzer explizit angestossener
# Nachgang NUR fuer die tatsaechlich unaufgeloesten Faelle (siehe
# zero_signal_offer weiter unten) - Kosten/Nutzen-Abwaegung bleibt beim
# Nutzer statt automatisch/unsichtbar zu passieren.
# Ueberschrift IMMER sichtbar (siehe Schritt 2) - der Instanzen-/
# Kombinationen-Vorschau je Anwendungsfall (fruehrer hier angezeigt) steht
# jetzt direkt bei der jeweiligen Konfiguration in Schritt 2; hier nur noch
# die Gesamtsumme unmittelbar vor dem Klassifizieren-Button.
st.header("3. Klassifizieren")
if not st.session_state.get("usecases"):
    st.caption("Konfiguriere zuerst mindestens einen Anwendungsfall in Schritt 2.")
elif not st.session_state.get("ifc_paths"):
    st.caption("Lade zuerst mindestens eine IFC-Datei in Schritt 1 hoch.")

if st.session_state.get("usecases") and st.session_state.get("ifc_paths"):
    total_calls = sum(uc.get("_n_combos", 0) for uc in st.session_state.usecases)
    st.info(f"Insgesamt {total_calls} LLM-Aufrufe über {len(st.session_state.usecases)} Anwendungsfall/-fälle.")

    if st.button("Alle Anwendungsfälle klassifizieren", type="primary"):
        try:
            client = get_active_client()
        except RuntimeError as exc:
            st.error(str(exc))
            st.stop()
        all_rows = []
        usecase_results = []
        zero_signal_offer = []

        for uc in st.session_state.usecases:
            # Klassifikation und Ergebnisse werden je Anwendungsfall UEBER
            # ALLE gewaehlten Bauteilklassen hinweg zusammengefuehrt (eigene
            # Schema-Extraktion pro Klasse, da jede Klasse ihre eigenen
            # Instanzen/Attribute hat, aber dieselbe Leitfrage/Kategorien/
            # Attributpfade).
            uc_classification = {}
            uc_metadata = {}
            failed = False
            for seed_class in uc["seed_classes"]:
                per_instance, _ = get_schema(seed_class)
                metadata = get_metadata(seed_class)
                combinations = extract_combinations(per_instance, uc["attribute_paths"])

                has_signal = {
                    c: keys for c, keys in combinations.items()
                    if any(v != "(nicht vorhanden)" for _, v in c)
                }
                zero_signal = {c: keys for c, keys in combinations.items() if c not in has_signal}

                combo_to_category = {}
                combo_to_evidence = {}
                combo_to_basis = {}
                for combo in zero_signal:
                    combo_to_category[combo] = "unbekannt"
                    combo_to_evidence[combo] = "Keines der konfigurierten Attribute ist im Modell gepflegt."
                    combo_to_basis[combo] = "keine"

                if has_signal:
                    # Ein LLM-Aufruf je einzigartiger Kombination (siehe
                    # classify_combinations_v3) kann bei vielen Kombinationen
                    # mehrere Minuten dauern - ein statischer Spinner-Text gibt
                    # dabei keine Rueckmeldung, ob das Tool noch arbeitet oder
                    # haengt. Stattdessen Fortschrittsbalken + Zaehler, ueber
                    # den optionalen on_progress-Callback nach jeder Kombination
                    # aktualisiert.
                    progress_label = st.empty()
                    progress_bar = st.progress(0.0)

                    def _update_progress(done, total):
                        progress_label.text(
                            f"Klassifiziere „{uc['concept']}“ ({seed_class}): "
                            f"{done}/{total} Kombinationen..."
                        )
                        progress_bar.progress(done / total)

                    _update_progress(0, len(has_signal))
                    try:
                        cat2, ev2, basis2 = classify_combinations_v3(
                            client, has_signal, uc["concept"], uc["concept_question"], uc["categories"],
                            on_progress=_update_progress,
                        )
                        combo_to_category.update(cat2)
                        combo_to_evidence.update(ev2)
                        combo_to_basis.update(basis2)
                    except Exception as exc:
                        st.error(
                            f"Anwendungsfall „{uc['concept']}“ ({seed_class}) fehlgeschlagen: {exc}\n\nLäuft Ollama noch?"
                        )
                        failed = True
                        continue
                    finally:
                        progress_label.empty()
                        progress_bar.empty()

                for combo, keys in combinations.items():
                    category = combo_to_category[combo]
                    evidence = combo_to_evidence.get(combo, "")
                    basis = combo_to_basis.get(combo, "")
                    for key in keys:
                        uc_classification[key] = category
                        uc_metadata[key] = metadata.get(key, {})
                        meta = metadata.get(key, {})
                        attrs = per_instance.get(key, {})
                        row = {
                            "Anwendungsfall": uc["concept"],
                            "Quelldatei": os.path.basename(meta.get("source_file", "")),
                            "Name": meta.get("name", ""),
                            "IFC-Klasse": meta.get("ifc_class", ""),
                            "GUID": meta.get("guid", ""),
                        }
                        for p in uc["attribute_paths"]:
                            # str(): bei mehreren Bauteilklassen gilt ein
                            # Attributpfad oft nur fuer einen Teil davon (z.B.
                            # LoadBearing existiert nur unter Pset_WallCommon,
                            # nicht fuer Traeger) - eine Spalte, die je nach
                            # Klasse mal einen bool, mal "" enthaelt, laesst
                            # sich sonst nicht zuverlaessig als Arrow-Tabelle
                            # darstellen (uneinheitlicher Spaltentyp).
                            value = attrs.get(p, "")
                            row[p.split(".")[-1]] = str(value) if value != "" else ""
                        row["Kategorie"] = category
                        row["Basis"] = basis
                        row["Begründung"] = evidence
                        all_rows.append(row)
                        if combo in zero_signal:
                            zero_signal_offer.append({
                                "uc_id": uc["id"], "concept": uc["concept"], "seed_class": seed_class,
                                "key": key, "name": meta.get("name", ""),
                                "row_index": len(all_rows) - 1,
                            })

            if uc_classification:
                usecase_results.append(
                    {"usecase": uc, "classification": uc_classification, "metadata": uc_metadata}
                )

        st.session_state.result_rows = all_rows
        st.session_state.usecase_results = usecase_results
        st.session_state.zero_signal_offer = zero_signal_offer

    # Stufe 2: fuer Instanzen ohne jegliches Pset-Signal (siehe oben) optional
    # eine Klassifikation per Namenssuche anstossen - explizit vom Nutzer
    # bestaetigt statt automatisch, da teurer (ein LLM-Aufruf pro
    # eindeutigem Namen) und weniger verlaesslich als ein Pset-Attribut.
    if st.session_state.get("zero_signal_offer"):
        offer = st.session_state.zero_signal_offer
        with st.expander(
            f"{len(offer)} Element(e) ohne jegliches Attributsignal wurden als \"unbekannt\" eingestuft",
            expanded=True,
        ):
            st.write(
                "Für diese Elemente ist keines der konfigurierten Attribute im Modell gepflegt. "
                "Als Fallback lassen sie sich stattdessen anhand ihres Bauteilnamens klassifizieren "
                "(ein LLM-Aufruf je eindeutigem Namen) – das ist aber weniger verlässlich als ein "
                "genormtes Pset-Attribut, siehe die Grenzfälle in der Vorschau oben. Alternativ können "
                "die Attributpfade des betroffenen Anwendungsfalls angepasst und neu klassifiziert werden."
            )
            st.dataframe(
                pd.DataFrame([
                    {"Anwendungsfall": e["concept"], "Bauteilklasse": e["seed_class"], "Name": e["name"]}
                    for e in offer
                ]),
                width="stretch",
            )
            if st.button(f"Diese {len(offer)} Element(e) mit Namenssuche klassifizieren"):
                try:
                    client = get_active_client()
                except RuntimeError as exc:
                    st.error(str(exc))
                    st.stop()
                by_group = defaultdict(list)
                for entry in offer:
                    by_group[(entry["uc_id"], entry["seed_class"])].append(entry)

                for (uc_id, seed_class), entries in by_group.items():
                    uc = next((u for u in st.session_state.usecases if u["id"] == uc_id), None)
                    if uc is None:
                        continue
                    per_instance, _ = get_schema(seed_class)
                    metadata = get_metadata(seed_class)
                    names = {k: m.get("name") for k, m in metadata.items()}
                    keys = [e["key"] for e in entries]
                    subset = {k: per_instance.get(k, {}) for k in keys}

                    progress_label = st.empty()
                    progress_bar = st.progress(0.0)
                    try:
                        combinations = extract_combinations_v3(subset, uc["attribute_paths"], names)

                        def _update_progress(done, total):
                            progress_label.text(
                                f"Klassifiziere per Namenssuche: {done}/{total} Kombinationen..."
                            )
                            progress_bar.progress(done / total)

                        _update_progress(0, len(combinations))
                        combo_to_category, combo_to_evidence, combo_to_basis = classify_combinations_v3(
                            client, combinations,
                            uc["concept"], uc["concept_question"], uc["categories"],
                            on_progress=_update_progress,
                        )
                    except Exception as exc:
                        st.error(f"Namenssuche fehlgeschlagen: {exc}\n\nLäuft Ollama noch?")
                        continue
                    finally:
                        progress_label.empty()
                        progress_bar.empty()

                    key_to_entry = {e["key"]: e for e in entries}
                    for combo, combo_keys in combinations.items():
                        category = combo_to_category[combo]
                        evidence = combo_to_evidence.get(combo, "")
                        basis = combo_to_basis.get(combo, "")
                        for key in combo_keys:
                            idx = key_to_entry[key]["row_index"]
                            st.session_state.result_rows[idx]["Kategorie"] = category
                            st.session_state.result_rows[idx]["Basis"] = basis
                            st.session_state.result_rows[idx]["Begründung"] = evidence
                            for ucr in st.session_state.usecase_results:
                                if ucr["usecase"]["id"] == uc_id:
                                    ucr["classification"][key] = category

                st.session_state.zero_signal_offer = []
                st.rerun()

# --- Ergebnisse ---
if st.session_state.get("result_rows"):
    st.header("Ergebnis")

    df = pd.DataFrame(st.session_state.result_rows)
    st.dataframe(df, width="stretch")

    csv_bytes = df.to_csv(index=False, sep=";").encode("utf-8-sig")

    st.subheader("Speichern")
    if "export_folder" not in st.session_state:
        st.session_state.export_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    export_folder = st.text_input(
        "Zielordner (wird bei Bedarf angelegt)", key="export_folder",
    )

    save_csv_col, save_ifc_col = st.columns(2)
    with save_csv_col:
        if st.button("CSV im Zielordner speichern"):
            try:
                os.makedirs(export_folder, exist_ok=True)
                csv_path = os.path.join(export_folder, "klassifikation.csv")
                with open(csv_path, "wb") as f:
                    f.write(csv_bytes)
                st.success(f"Gespeichert: {csv_path}")
            except OSError as exc:
                st.error(f"Konnte nicht speichern: {exc}")
    with save_ifc_col:
        if st.button("Als IFC mit Klassifikation im Zielordner speichern"):
            try:
                os.makedirs(export_folder, exist_ok=True)
                out_dir = os.path.join(export_folder, "ifc_export")
                written = []
                for entry in st.session_state.usecase_results:
                    files = enrich_ifc_files(
                        entry["classification"], entry["metadata"],
                        classification_system_name=entry["usecase"]["concept"],
                        out_dir=out_dir,
                    )
                    for f in files:
                        # Je Anwendungsfall eigener Dateiname, sonst ueberschreibt
                        # der naechste Anwendungsfall dieselbe Quelldatei-Ausgabe.
                        base, ext = os.path.splitext(f)
                        slug = "".join(c if c.isalnum() else "_" for c in entry["usecase"]["concept"])
                        new_path = f"{base}_{slug}{ext}"
                        os.replace(f, new_path)
                        written.append(new_path)
                st.success("Gespeichert:\n" + "\n".join(written))
            except OSError as exc:
                st.error(f"Konnte nicht speichern: {exc}")

    with st.expander("Alternativ: direkt über den Browser herunterladen"):
        st.download_button(
            "CSV herunterladen (alle Anwendungsfälle)", csv_bytes,
            file_name="klassifikation.csv", mime="text/csv",
        )
        if st.button("Als IFC mit Klassifikation exportieren (Browser-Download)"):
            out_dir = os.path.join(st.session_state.tmp_dir, "enriched")
            written = []
            for entry in st.session_state.usecase_results:
                files = enrich_ifc_files(
                    entry["classification"], entry["metadata"],
                    classification_system_name=entry["usecase"]["concept"],
                    out_dir=out_dir,
                )
                for f in files:
                    base, ext = os.path.splitext(f)
                    slug = "".join(c if c.isalnum() else "_" for c in entry["usecase"]["concept"])
                    new_path = f"{base}_{slug}{ext}"
                    os.replace(f, new_path)
                    written.append(new_path)
            st.session_state.enriched_files = written

        for path in st.session_state.get("enriched_files", []):
            with open(path, "rb") as f:
                st.download_button(
                    f"{os.path.basename(path)} herunterladen", f.read(),
                    file_name=os.path.basename(path), key=f"dl_{path}",
                )
