"""
Regressionstest fuer gui_app.py ueber Streamlits AppTest-Framework (treibt
das echte Skript inkl. Session-State-Lifecycle, ohne echten Server/Browser).

Deckt einen Bug ab, der beim manuellen Testen auftrat: Nach Klick auf
"Anwendungsfall hinzufuegen" schlug Streamlit fehl, weil der Code
versuchte, den Wert eines Text-Widgets nach dessen Instanziierung im selben
Lauf direkt zu setzen (verboten). Behoben durch einen durchnummerierten
Widget-Key statt direkter Mutation; das "Bearbeiten"-Formular teilt sich
denselben Key-Satz wie "Hinzufuegen" (kein separater Zweig pro Modus), da
ein Widget, das je nach Modus mal instanziiert wird und mal nicht, seinen
Wert zwischen Laeufen verlieren kann.

BEKANNTE EINSCHRAENKUNG DIESER TESTS: Eine Sequenz aus mehreren
Hinzufuegen-/Bearbeiten-Zyklen hintereinander (z.B. hinzufuegen ->
bearbeiten -> speichern -> erneut hinzufuegen -> erneut bearbeiten) laesst
sich mit AppTest bislang nicht zuverlaessig durchspielen: AppTest wirft dann
einen KeyError auf einen laengst verworfenen, durchnummerierten Widget-Key,
obwohl derselbe Code bei isolierten Minimalbeispielen mit vergleichbarem
Muster (durchnummerierte Keys, wachsende Liste eigener Buttons,
verschachtelte Expander) fehlerfrei lief - vermutlich eine Einschraenkung
des Testframeworks selbst, kein bestaetigter Anwendungsfehler. Einzelne
Zyklen (hinzufuegen+bearbeiten+speichern; mehrfaches Hinzufuegen ohne
Bearbeiten) sind unten abgedeckt und laufen zuverlaessig durch. Die volle
Sequenz wurde manuell in start_gui.bat gegengeprueft (ohne Probleme).

ZWEITE BEKANNTE EINSCHRAENKUNG: st.multiselect(..., accept_new_options=True)
laesst sich mit AppTest NICHT zuverlaessig testen. AppTest's
multiselect.set_value(...) schreibt den Wert direkt in session_state und
umgeht damit den echten Frontend/Backend-Sync-Mechanismus, den
accept_new_options fuer neu hinzugefuegte (nicht in options enthaltene)
Werte benutzt. Ein echter Bug in gui_app.py (Bauteilklasse(n)-Auswahl
verschwand nach jeder weiteren Interaktion wieder, weil options bei jedem
Lauf per "class_options + current_seed_classes" selbstreferenziell aus dem
eigenen letzten Widget-Wert neu berechnet wurde - siehe Kommentar bei
seed_class_key in gui_app.py) blieb deshalb in allen AppTest-Laeufen
unentdeckt (inkl. test_custom_usecase_with_multiple_seed_classes unten) und
wurde erst durch manuelles Testen im echten Browser gefunden. Nach dem Fix
(options bleibt pro Lauf statisch, kein Ruecklesen des eigenen Werts) lief
es im echten Browser ueber mehrere Interaktionen hinweg stabil. Fuer
Aenderungen an accept_new_options-Widgets gilt daher: AppTest allein reicht
nicht, ein echter Browsertest (siehe unten) ist noetig.

Ausfuehren (aus prototype/, mit aktivierter .venv):
    PYTHONPATH=src python tests/test_gui_app.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from streamlit.testing.v1 import AppTest

USER_PRESETS_DIR = "data/user_presets"


def _add_paths_via_ui(at, paths, timeout=60):
    """Tippt Attributpfade einzeln in das 'neuer Pfad'-Feld ein, wie es ein
    echter Nutzer taete: jeder Pfad wird eingetippt und committet (.run()),
    wird dadurch zu einer eigenen Zeile, und ein neues leeres Feld fuer den
    naechsten Pfad erscheint (siehe add_path_rows in gui_app.py)."""
    for p in paths:
        form_id = at.session_state["draft_form_id"]
        at.text_input(key=f"draft_path_new_{form_id}").input(p).run(timeout=timeout)
        assert not at.exception, f"Exception beim Eintippen von {p!r}: {at.exception}"
    return at


def test_class_check_button_actually_runs():
    """Deckt einen Bug ab: ein unbedingtes
    "if 'class_counts' not in st.session_state: ... = {}" ganz oben im
    Skript lief bei JEDEM Durchlauf erneut, noch bevor Schritt 2 pruefen
    konnte, ob die Bauteilklassen ueberhaupt schon einmal untersucht wurden.
    Dadurch verschwand der "Verfuegbare Bauteilklassen pruefen"-Button nach
    dem allerersten Klick wirkungslos, OHNE dass die eigentliche Pruefung
    je gelaufen waere (der Klick "kam nicht mehr an", weil das Button-
    Widget in dem Lauf, der ihn verarbeiten sollte, gar nicht mehr
    gezeichnet wurde). Behoben durch Verzicht auf die unbedingte
    Vorbelegung; ueberall sonst wird stattdessen ueber .get(...) gelesen."""
    at = AppTest.from_file("src/gui_app.py")
    at.run(timeout=60)
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    check_button = [b for b in at.button if b.label == "Verfügbare Bauteilklassen prüfen"]
    assert len(check_button) == 1, "Button muss vor der ersten Pruefung sichtbar sein"

    check_button[0].click().run(timeout=60)
    assert not at.exception, f"Exception nach Klick: {at.exception}"
    assert at.session_state["class_counts"] == {"IfcWall": 10}, (
        "Klick muss die Pruefung tatsaechlich ausfuehren, nicht nur das Widget verschwinden lassen"
    )

    check_button_after = [b for b in at.button if b.label == "Verfügbare Bauteilklassen prüfen"]
    assert len(check_button_after) == 0, "Button muss nach erfolgreicher Pruefung verschwinden"
    assert any("Gefundene Klassen" in str(c.value) for c in at.caption)


def test_add_multiple_usecases():
    """'Vorlage übernehmen' ist ein eigener, direkter Punkt (kein Umweg
    mehr über das Eigenerstellen-Formular): Auswahl + Klick auf 'Vorlage
    hinzufügen' muss den Anwendungsfall sofort 1:1 übernehmen."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception, f"Exception beim initialen Lauf: {at.exception}"

    at.selectbox(key="preset_pick").select("Tragende Funktion (Wand)").run(timeout=60)
    assert not at.exception, f"Exception nach Preset-Auswahl: {at.exception}"

    add_button = next(b for b in at.button if b.label == "Vorlage hinzufügen")
    add_button.click().run(timeout=60)
    assert not at.exception, f"Exception nach 'Vorlage hinzufügen'-Klick: {at.exception}"

    usecases = at.session_state["usecases"]
    assert len(usecases) == 1
    assert usecases[0]["concept"] == "Tragende Funktion"
    assert usecases[0]["seed_classes"] == ["IfcWall"]

    # Zweite, andersartige Vorlage hinzufuegen - unabhaengige Aktion, muss
    # den ersten Anwendungsfall unangetastet lassen.
    at.selectbox(key="preset_pick").select("Trägermaterial (Träger)").run(timeout=60)
    assert not at.exception, f"Exception nach zweiter Preset-Auswahl: {at.exception}"

    add_button2 = next(b for b in at.button if b.label == "Vorlage hinzufügen")
    add_button2.click().run(timeout=60)
    assert not at.exception, f"Exception beim zweiten Hinzufuegen: {at.exception}"

    usecases = at.session_state["usecases"]
    assert len(usecases) == 2
    assert usecases[1]["concept"] == "Traegermaterial"
    assert usecases[1]["seed_classes"] == ["IfcBeam"]
    assert usecases[1]["attribute_paths"] != usecases[0]["attribute_paths"]


def test_custom_usecase_with_multiple_seed_classes():
    """Eigener Anwendungsfall (keine Vorlage): eigener Name/Konzept muss
    eingebbar sein, und mehrere Bauteilklassen muessen sich per
    Mehrfachauswahl derselben Leitfrage/Kategorien/Attributpfaden zuordnen
    lassen (z.B. "tragend?" fuer Waende UND Traeger zugleich)."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    seed_class_ms = next(m for m in at.multiselect if m.key == "draft_seed_class")
    seed_class_ms.set_value(["IfcWall", "IfcBeam"]).run(timeout=60)
    assert not at.exception, f"Exception nach Mehrfachauswahl: {at.exception}"

    at.text_input(key="draft_concept").input("Eigenes Konzept").run(timeout=60)
    at.text_input(key="draft_concept_question").input("Eigene Leitfrage?").run(timeout=60)
    at.text_input(key="draft_categories").input("A, B, unbekannt").run(timeout=60)
    _add_paths_via_ui(at, ["$.schema.Pset_WallCommon.LoadBearing"])
    assert not at.exception

    add_button = next(b for b in at.button if b.label == "Anwendungsfall hinzufügen")
    add_button.click().run(timeout=60)
    assert not at.exception, f"Exception beim Hinzufuegen: {at.exception}"

    usecases = at.session_state["usecases"]
    assert len(usecases) == 1
    assert usecases[0]["concept"] == "Eigenes Konzept"
    assert usecases[0]["seed_classes"] == ["IfcWall", "IfcBeam"]


def test_unbekannt_category_is_implicit():
    """'unbekannt' muss der Nutzer nicht mehr selbst als Zielkategorie
    eintragen - sie wird beim Speichern automatisch ergaenzt, falls nicht
    schon vorhanden (und nicht dupliziert, falls doch)."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    seed_class_ms = next(m for m in at.multiselect if m.key == "draft_seed_class")
    seed_class_ms.set_value(["IfcWall"]).run(timeout=60)
    at.text_input(key="draft_concept").input("Ohne unbekannt eingetragen").run(timeout=60)
    at.text_input(key="draft_concept_question").input("Testfrage?").run(timeout=60)
    at.text_input(key="draft_categories").input("A, B").run(timeout=60)
    _add_paths_via_ui(at, ["$.schema.Pset_WallCommon.LoadBearing"])
    assert not at.exception

    add_button = next(b for b in at.button if b.label == "Anwendungsfall hinzufügen")
    add_button.click().run(timeout=60)
    assert not at.exception, f"Exception beim Hinzufuegen: {at.exception}"

    usecases = at.session_state["usecases"]
    assert usecases[0]["categories"] == ["A", "B", "unbekannt"]


def test_leitfrage_auto_generated_and_overridable():
    """Die Leitfrage wird, solange sie noch nicht angefasst wurde,
    deterministisch aus Konzept + Zielkategorien vorbefuellt (kein
    LLM-Aufruf) - tippt der Nutzer selbst etwas hinein, bleibt das erhalten
    und wird durch spaetere Aenderungen an Konzept/Kategorien nicht mehr
    ueberschrieben."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    at.text_input(key="draft_concept").input("Tragende Funktion").run(timeout=60)
    at.text_input(key="draft_categories").input("tragend, nicht tragend").run(timeout=60)
    assert not at.exception

    assert at.text_input(key="draft_concept_question").value == (
        "Welche der folgenden Kategorien trifft für Tragende Funktion zu: "
        "tragend oder nicht tragend?"
    )

    # Nutzer ueberschreibt die vorgeschlagene Frage von Hand.
    at.text_input(key="draft_concept_question").input("Eigene, praezisere Frage?").run(timeout=60)
    assert not at.exception

    # Kategorien danach aendern darf die von Hand gesetzte Frage NICHT
    # zuruecksetzen.
    at.text_input(key="draft_categories").input("tragend, nicht tragend, unsicher").run(timeout=60)
    assert not at.exception
    assert at.text_input(key="draft_concept_question").value == "Eigene, praezisere Frage?"


def test_single_edit_cycle():
    """Hinzufuegen -> Bearbeiten -> Speichern, genau ein Zyklus: Update
    erfolgt in-place (kein Duplikat, ID bleibt erhalten)."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    at.selectbox(key="preset_pick").select("Tragende Funktion (Wand)").run(timeout=60)
    add_button = next(b for b in at.button if b.label == "Vorlage hinzufügen")
    add_button.click().run(timeout=60)
    assert not at.exception
    uc_id = at.session_state["usecases"][0]["id"]

    edit_button = next(b for b in at.button if b.label == "Bearbeiten")
    edit_button.click().run(timeout=60)
    assert not at.exception, f"Exception beim Klick auf Bearbeiten: {at.exception}"
    assert at.session_state["editing_id"] == uc_id

    # Bearbeiten muss die vorhandenen Attributpfade als eigene, bereits
    # befuellte Zeilen vorbelegen (set_path_rows in gui_app.py).
    form_id = at.session_state["draft_form_id"]
    row_ids = at.session_state[f"draft_path_row_ids_{form_id}"]
    assert [at.session_state[f"draft_path_item_{form_id}_{rid}"] for rid in row_ids] == [
        "$.schema.Pset_WallCommon.LoadBearing"
    ]

    save_button = next(b for b in at.button if b.label == "Änderungen speichern")
    save_button.click().run(timeout=60)
    assert not at.exception, f"Exception beim Speichern der Bearbeitung: {at.exception}"

    usecases = at.session_state["usecases"]
    assert len(usecases) == 1, "Bearbeiten darf keinen Duplikat-Eintrag erzeugen"
    assert usecases[0]["id"] == uc_id, "ID muss beim Bearbeiten erhalten bleiben"
    assert usecases[0]["attribute_paths"] == ["$.schema.Pset_WallCommon.LoadBearing"]
    assert at.session_state["editing_id"] is None


def test_save_and_load_project_preset():
    """Deckt den vorgesehenen Projekt-Workflow ab: mehrere Anwendungsfaelle
    fuer ein Projekt zusammenstellen und ALS EIN PROJEKT speichern, spaeter
    (in einer neuen "Sitzung") wieder laden - danach koennen einzelne
    Anwendungsfaelle bearbeitet sowie welche hinzugefuegt/entfernt werden."""
    if os.path.isdir(USER_PRESETS_DIR):
        shutil.rmtree(USER_PRESETS_DIR)
    try:
        at = AppTest.from_file("src/gui_app.py")
        at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
        # Zwei Anwendungsfaelle werden direkt vorbelegt statt ueber zwei
        # aufeinanderfolgende "hinzufuegen"-Klicks erzeugt: AppTest wird nach
        # dem zweiten Formular-Reset innerhalb derselben Sitzung unzuverlaessig
        # (siehe Modul-Docstring) - das Vorbelegen umgeht das, ohne etwas an
        # der zu testenden Speichern/Laden-Logik zu aendern.
        at.session_state["usecases"] = [
            {
                "id": "test-uc-1", "seed_classes": ["IfcWall"], "concept": "Tragende Funktion",
                "concept_question": "Ist das Bauteil tragend oder nicht tragend?",
                "categories": ["tragend", "nicht tragend", "unbekannt"],
                "attribute_paths": ["$.schema.Pset_WallCommon.LoadBearing"],
            },
            {
                "id": "test-uc-2", "seed_classes": ["IfcBeam"], "concept": "Traegermaterial",
                "concept_question": "Ist der Traeger aus Stahl oder Stahlbeton?",
                "categories": ["Stahltraeger", "Stahlbetontraeger", "unbekannt"],
                "attribute_paths": ["$.schema.Tekla Common.Profile"],
            },
        ]
        at.run(timeout=60)
        assert not at.exception

        at.text_input(key="project_save_name").input("Testprojekt Kombi").run(timeout=60)
        assert not at.exception
        save_button = next(b for b in at.button if b.label == "Projekt speichern")
        save_button.click().run(timeout=60)
        assert not at.exception, f"Exception beim Projekt-Speichern: {at.exception}"

        proj_path = os.path.join(USER_PRESETS_DIR, "Testprojekt_Kombi.json")
        assert os.path.exists(proj_path)
        saved = json.load(open(proj_path, encoding="utf-8"))
        assert len(saved["usecases"]) == 2

        # Neue AppTest-Instanz = neue "Sitzung"; das gespeicherte Projekt muss
        # von der Festplatte geladen werden, nicht aus einem laufenden Prozess.
        at2 = AppTest.from_file("src/gui_app.py")
        at2.session_state["ifc_paths"] = ["data/test_walls.ifc"]
        at2.run(timeout=60)
        assert not at2.exception
        assert len(at2.session_state["usecases"]) == 0

        at2.selectbox(key="project_load_choice").select("Testprojekt Kombi").run(timeout=60)
        assert not at2.exception
        load_button = next(b for b in at2.button if b.label == "Projekt laden")
        load_button.click().run(timeout=60)
        assert not at2.exception, f"Exception beim Projekt-Laden: {at2.exception}"

        loaded = at2.session_state["usecases"]
        assert len(loaded) == 2
        concepts = sorted(u["concept"] for u in loaded)
        assert concepts == ["Traegermaterial", "Tragende Funktion"]
        # frische IDs, nicht identisch mit den urspruenglichen
        original_ids = {u["id"] for u in at.session_state["usecases"]}
        assert all(u["id"] not in original_ids for u in loaded)
    finally:
        if os.path.isdir(USER_PRESETS_DIR):
            shutil.rmtree(USER_PRESETS_DIR)


def test_project_load_dropdown_appears_right_after_saving():
    """Deckt einen Bug ab: "Projekt speichern" loeste bisher keinen Rerun
    aus, wodurch das "Projekt laden"-Dropdown (oben im selben Lauf bereits
    gerendert) das gerade gespeicherte Projekt nicht zeigte, bis irgendeine
    andere, unabhaengige Interaktion zufaellig einen Rerun anstiess. Das
    Dropdown selbst ist immer sichtbar (auch ohne gespeicherte Projekte,
    dann mit der Option "(noch keine gespeicherten Projekte)") - hier wird
    geprueft, dass seine OPTIONEN sich direkt nach dem Speichern
    aktualisieren."""
    if os.path.isdir(USER_PRESETS_DIR):
        shutil.rmtree(USER_PRESETS_DIR)
    try:
        at = AppTest.from_file("src/gui_app.py")
        at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
        at.session_state["usecases"] = [
            {
                "id": "x1", "seed_classes": ["IfcWall"], "concept": "Tragende Funktion",
                "concept_question": "Ist das Bauteil tragend oder nicht tragend?",
                "categories": ["tragend", "nicht tragend", "unbekannt"],
                "attribute_paths": ["$.schema.Pset_WallCommon.LoadBearing"],
            },
        ]
        at.run(timeout=60)
        assert not at.exception
        load_sb_before = next(sb for sb in at.selectbox if sb.key == "project_load_choice")
        assert load_sb_before.options == ["(noch keine gespeicherten Projekte)"]

        at.text_input(key="project_save_name").input("Frisch Gespeichert").run(timeout=60)
        save_button = next(b for b in at.button if b.label == "Projekt speichern")
        save_button.click().run(timeout=60)
        assert not at.exception, f"Exception beim Speichern: {at.exception}"

        load_selectboxes = [sb for sb in at.selectbox if sb.key == "project_load_choice"]
        assert load_selectboxes, "Laden-Dropdown fehlt direkt nach dem Speichern"
        assert "Frisch Gespeichert" in load_selectboxes[0].options
        assert any("gespeichert" in str(m.value) for m in at.success)
    finally:
        if os.path.isdir(USER_PRESETS_DIR):
            shutil.rmtree(USER_PRESETS_DIR)


def test_classification_across_multiple_seed_classes():
    """End-to-End mit echtem Ollama-Backend: EIN Anwendungsfall mit ZWEI
    Bauteilklassen (Wand + Traeger aus der kombinierten IFCNet-Testdatei)
    muss beide Klassen im Ergebnis liefern. Deckt nebenbei einen Bug ab:
    ein Attributpfad, der nur fuer einen Teil der gewaehlten Klassen gilt
    (z.B. LoadBearing existiert nicht fuer Traeger), fuehrte in der
    Ergebnistabelle zu einer Spalte mit gemischtem Typ (bool und ""), was
    die Arrow-Serialisierung fuer st.dataframe zum Scheitern brachte."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_combined_ifcnet.ifc"]
    at.session_state["usecases"] = [
        {
            "id": "multi1", "seed_classes": ["IfcWall", "IfcBeam"], "concept": "Test Mehrklassen",
            "concept_question": "Testfrage",
            "categories": ["tragend", "nicht tragend", "unbekannt"],
            "attribute_paths": ["$.schema.Pset_WallCommon.LoadBearing"],
        },
    ]
    at.run(timeout=60)
    assert not at.exception

    classify_button = next(b for b in at.button if b.label == "Alle Anwendungsfälle klassifizieren")
    # Grosszuegiges Timeout: einzelne lokale Ollama-Aufrufe koennen je nach
    # Systemlast auch mal 30-40s statt 5-10s dauern. Traeger haben fuer
    # LoadBearing durchgehend kein Signal - werden seit der Zweistufigkeit
    # (siehe zero_signal_offer-Test unten) OHNE LLM-Aufruf direkt als
    # "unbekannt" eingestuft, braucht also nur die 3 Wand-Kombinationen.
    classify_button.click().run(timeout=480)
    assert not at.exception, f"Exception: {at.exception}"

    rows = at.session_state["result_rows"]
    classes_seen = {r["IFC-Klasse"] for r in rows}
    assert "IfcWall" in classes_seen
    assert "IfcBeam" in classes_seen
    # Spalte muss durchgehend string-typisiert sein (kein Mix aus bool und "")
    assert all(isinstance(r.get("LoadBearing"), str) for r in rows)
    # Traeger ohne jegliches Pset-Signal: deterministisch "unbekannt"/"keine",
    # kein LLM-Aufruf noetig.
    beam_rows = [r for r in rows if r["IFC-Klasse"] == "IfcBeam"]
    assert beam_rows, "Es sollten Traeger-Zeilen im Ergebnis sein"
    assert all(r["Kategorie"] == "unbekannt" and r["Basis"] == "keine" for r in beam_rows)


def test_zero_signal_followup_namenssuche():
    """End-to-End mit echtem Ollama-Backend: nach der Stufe-1-Klassifikation
    (nur Pset-Attribute) muessen Instanzen ganz ohne Signal als expliziter,
    vom Nutzer bestaetigter Nachgang ("N Element(e) ... mit Namenssuche
    klassifizieren") per Namenssuche aufgeloest werden koennen - inkl.
    korrekter Aktualisierung der bestehenden Ergebniszeilen (nicht nur neue
    Zeilen anhaengen)."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_combined_ifcnet.ifc"]
    at.session_state["usecases"] = [
        {
            "id": "multi1", "seed_classes": ["IfcWall", "IfcBeam"], "concept": "Test Mehrklassen",
            "concept_question": "Testfrage",
            "categories": ["tragend", "nicht tragend", "unbekannt"],
            "attribute_paths": ["$.schema.Pset_WallCommon.LoadBearing"],
        },
    ]
    at.run(timeout=60)
    assert not at.exception

    classify_button = next(b for b in at.button if b.label == "Alle Anwendungsfälle klassifizieren")
    classify_button.click().run(timeout=480)
    assert not at.exception, f"Exception: {at.exception}"

    offer = at.session_state["zero_signal_offer"]
    # 7 Traeger (keiner hat Pset_WallCommon.LoadBearing, existiert nur fuer
    # Waende) + 1 Wand ("Trockenbauwand", der urspruengliche Bug-Fall vom
    # heutigen Tag, hat ebenfalls kein LoadBearing gepflegt) = 8.
    assert len(offer) == 8, f"Erwartet 8 Elemente ohne Pset-Signal, gefunden: {len(offer)}"
    n_before = len(offer)

    followup_button = next(
        b for b in at.button
        if b.label == f"Diese {n_before} Element(e) mit Namenssuche klassifizieren"
    )
    followup_button.click().run(timeout=480)
    assert not at.exception, f"Exception beim Namenssuche-Nachgang: {at.exception}"

    assert at.session_state["zero_signal_offer"] == []

    rows = at.session_state["result_rows"]
    beam_rows = [r for r in rows if r["IFC-Klasse"] == "IfcBeam"]
    assert len(beam_rows) == 7
    # Nach dem Nachgang muss die Klassifikation auf Bauteilname beruhen
    # (nicht mehr pauschal "unbekannt"/"keine" wie vor dem Nachgang) und
    # nicht mehr auf eine leere Begruendung zeigen.
    assert all(r["Basis"] in ("Bauteilname", "keine") for r in beam_rows)
    assert any(r["Basis"] == "Bauteilname" for r in beam_rows), (
        "Mindestens ein Traeger sollte nach der Namenssuche 'Bauteilname' als Basis haben"
    )
    assert all(r["Begründung"] for r in beam_rows)

    # usecase_results (Grundlage fuer den IFC-Export) muss ebenfalls
    # aktualisiert sein, nicht nur result_rows/das CSV.
    ucr = next(u for u in at.session_state["usecase_results"] if u["usecase"]["id"] == "multi1")
    beam_keys = [k for k, v in ucr["metadata"].items() if v.get("ifc_class") == "IfcBeam"]
    assert beam_keys
    assert all(ucr["classification"][k] != "" for k in beam_keys)


def test_form_expander_stays_open_while_adding_paths():
    """Bug gefunden bei der Zeilen-UI-Umstellung (2026-07-30): 'expanded'
    wurde bei JEDEM Skriptlauf neu ausgewertet (kein 'key' fuer st.expander
    in dieser Streamlit-Version verfuegbar). Das Zeilen-UI loest pro
    eingetipptem Pfad einen eigenen Lauf aus (anders als das fruehere
    Mehrzeilen-Textfeld, das erst beim Verlassen des GANZEN Felds
    committete) - ohne Fix waere das Formular nach dem ERSTEN Pfad schon
    wieder zugeklappt. Getestet mit einem bereits vorhandenen Anwendungsfall
    (sonst waere "+ nicht st.session_state.usecases" allein schon
    ausreichend und der Bug nicht sichtbar)."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    at.selectbox(key="preset_pick").select("Tragende Funktion (Wand)").run(timeout=60)
    add_button = next(b for b in at.button if b.label == "Vorlage hinzufügen")
    add_button.click().run(timeout=60)
    assert not at.exception

    own_expander = next(
        e for e in at.expander if "Anwendungsfall erstellen" in e.proto.label
    )
    assert not own_expander.proto.expanded, (
        "Sollte bei vorhandenem Anwendungsfall und leerem Entwurf eingeklappt starten"
    )

    _add_paths_via_ui(at, ["$.schema.Pset_WallCommon.LoadBearing"])

    own_expander = next(
        e for e in at.expander if "Anwendungsfall erstellen" in e.proto.label
    )
    assert own_expander.proto.expanded, (
        "Formular darf nach dem Eintragen eines Pfads nicht wieder zuklappen"
    )


def test_attribute_path_rows_add_remove_and_autoclear():
    """Zeilen-basiertes Attributpfad-UI (2026-07-30, ersetzt das fruehere
    einzelne Mehrzeilen-Textfeld): (1) Eintippen ins 'neuer Pfad'-Feld
    erzeugt eine eigene Zeile und leert das Feld fuer den naechsten Pfad;
    (2) der X-Button entfernt gezielt eine einzelne Zeile; (3) eine Zeile
    von Hand leeren (ohne X-Button) entfernt sie ebenso automatisch, sobald
    das Feld verlassen wird."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    form_id = at.session_state["draft_form_id"]
    _add_paths_via_ui(at, [
        "$.schema.Pset_WallCommon.LoadBearing",
        "$.schema.Pset_WallCommon.IsExternal",
        "$.schema.Material.Name",
    ])

    row_ids = at.session_state[f"draft_path_row_ids_{form_id}"]
    assert len(row_ids) == 3, f"Erwartet 3 Zeilen, gefunden: {row_ids}"
    assert [at.session_state[f"draft_path_item_{form_id}_{rid}"] for rid in row_ids] == [
        "$.schema.Pset_WallCommon.LoadBearing",
        "$.schema.Pset_WallCommon.IsExternal",
        "$.schema.Material.Name",
    ]

    # X-Button entfernt gezielt die MITTLERE Zeile.
    remove_button = next(
        b for b in at.button if b.key == f"draft_path_remove_{form_id}_{row_ids[1]}"
    )
    remove_button.click().run(timeout=60)
    assert not at.exception, f"Exception beim Entfernen: {at.exception}"

    row_ids = at.session_state[f"draft_path_row_ids_{form_id}"]
    assert [at.session_state[f"draft_path_item_{form_id}_{rid}"] for rid in row_ids] == [
        "$.schema.Pset_WallCommon.LoadBearing", "$.schema.Material.Name",
    ]

    # Verbleibende Zeile leeren (nicht ueber den X-Button) -> muss ebenso
    # automatisch verschwinden, sobald das Feld verlassen wird.
    last_rid = row_ids[1]
    at.text_input(key=f"draft_path_item_{form_id}_{last_rid}").input("").run(timeout=60)
    assert not at.exception, f"Exception beim Leeren: {at.exception}"

    row_ids = at.session_state[f"draft_path_row_ids_{form_id}"]
    assert [at.session_state[f"draft_path_item_{form_id}_{rid}"] for rid in row_ids] == [
        "$.schema.Pset_WallCommon.LoadBearing"
    ], f"Geleerte Zeile haette automatisch verschwinden muessen, verbleibende Zeilen: {row_ids}"


def test_automatisch_vorschlagen_does_not_duplicate_existing_paths():
    """Bug vom 2026-07-29: 'Automatisch vorschlagen' haengte vorgeschlagene
    Pfade blind an die Attributpfad-Liste an, auch wenn ein Pfad dort schon
    von Hand eingetragen war - Ergebnis war eine doppelte Zeile. Getestet
    wird hier direkt der gemeinsame Merge-Mechanismus (_pending_paths_append
    + add_path_rows, siehe gui_app.py), ueber den sowohl "Attributpfade
    vorschlagen lassen" als auch das Uebernehmen einzelner, nicht
    gekuerzter Vorschlaege laufen - ein echter LLM-Aufruf ist fuer diese
    Regression nicht noetig, da der Fehler rein im Merge selbst lag, nicht
    im LLM-Ergebnis. _pending_paths_append haelt seit der Zeilen-UI-
    Umstellung eine Liste (nicht mehr einen zeilenumbruchgetrennten Text)."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.run(timeout=60)
    assert not at.exception

    # Einer der beiden "vorgeschlagenen" Pfade ist hier bereits von Hand
    # eingetragen.
    _add_paths_via_ui(at, ["$.schema.Pset_WallCommon.LoadBearing"])

    at.session_state["_pending_paths_append"] = [
        "$.schema.Pset_WallCommon.LoadBearing", "$.schema.Pset_WallCommon.IsExternal",
    ]
    at.run(timeout=60)
    assert not at.exception

    form_id = at.session_state["draft_form_id"]
    row_ids = at.session_state[f"draft_path_row_ids_{form_id}"]
    values = [at.session_state[f"draft_path_item_{form_id}_{rid}"] for rid in row_ids]
    assert len(values) == len(set(values)) == 2, (
        f"Erwartet 2 eindeutige Pfade (keine Duplikate), gefunden: {values}"
    )


def test_suggest_button_trims_redundant_paths():
    """End-to-End mit echtem Ollama-Backend: 'Attributpfade vorschlagen
    lassen (LLM)' war bisher ueberhaupt nicht per AppTest abgedeckt. Prueft
    zugleich die neue Kuerzung (attribute_diagnostics.trim_redundant_paths,
    siehe dortiger Docstring) - das LLM schoepft das erlaubte Maximum an
    Pfaden in der Praxis durchgehend aus, auch wenn ein Teil davon keine
    neue Instanzabdeckung bringt; das wird hier deterministisch nachtraeglich
    gekuerzt, ohne weiteren LLM-Aufruf, und als eigene Zeilen mit
    "Übernehmen"-Button angezeigt (statt automatisch verworfen zu werden).
    Der genaue Vorschlag ist modellabhaengig, daher wird nicht auf exakte
    Pfade geprueft, sondern nur darauf, dass die Attributpfade danach nicht
    leer sind und - falls gekuerzt wurde - sich ein nicht uebernommener
    Vorschlag ueber den zugehoerigen Button nachtraeglich doch noch als
    echte Zeile hinzufuegen laesst."""
    import glob
    beam_files = []
    for pattern in [
        "../testdaten/ifcnet/extracted/IfcBeam/train/*.ifc",
        "../testdaten/ifcnet/extracted/IfcBeam/test/*.ifc",
    ]:
        beam_files.extend(glob.glob(pattern))
    assert beam_files, "IFCNet-Traegerdaten nicht gefunden"

    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = beam_files
    at.run(timeout=60)
    assert not at.exception

    seed_class_ms = next(m for m in at.multiselect if m.key == "draft_seed_class")
    seed_class_ms.set_value(["IfcBeam"]).run(timeout=120)
    at.text_input(key="draft_concept").input("Traegermaterial").run(timeout=60)
    at.text_input(key="draft_categories").input("Stahltraeger, Stahlbetontraeger").run(timeout=60)
    assert not at.exception

    suggest_button = next(b for b in at.button if b.label == "Attributpfade vorschlagen lassen (LLM)")
    suggest_button.click().run(timeout=480)
    assert not at.exception, f"Exception beim Vorschlagen: {at.exception}"

    form_id = at.session_state["draft_form_id"]
    row_ids = at.session_state[f"draft_path_row_ids_{form_id}"]
    paths_after = [at.session_state[f"draft_path_item_{form_id}_{rid}"] for rid in row_ids]
    assert paths_after, "Nach dem Vorschlag sollten Attributpfade eingetragen sein"

    dropped_key = f"draft_dropped_paths_{form_id}"
    dropped = at.session_state[dropped_key] if dropped_key in at.session_state else []
    if dropped:
        adopt_buttons = [
            b for b in at.button
            if b.key and b.key.startswith(f"draft_dropped_adopt_{form_id}_")
        ]
        assert len(adopt_buttons) == len(dropped), (
            f"Erwartet einen 'Übernehmen'-Button je nicht uebernommenem Vorschlag "
            f"({len(dropped)}), gefunden: {len(adopt_buttons)}"
        )
        to_adopt = dropped[0]
        adopt_buttons[0].click().run(timeout=60)
        assert not at.exception, f"Exception beim Uebernehmen: {at.exception}"

        row_ids_after = at.session_state[f"draft_path_row_ids_{form_id}"]
        adopted_values = {
            at.session_state[f"draft_path_item_{form_id}_{rid}"] for rid in row_ids_after
        }
        assert to_adopt in adopted_values, (
            f"{to_adopt!r} sollte nach 'Übernehmen' als eigene Zeile vorhanden sein"
        )
        assert to_adopt not in at.session_state[f"draft_dropped_paths_{form_id}"]


def test_cardinality_check_shows_combination_impact():
    """Kardinalitaets-Check (attribute_diagnostics.py) im 'Anwendungsfall
    hinzufuegen'-Formular: sobald Bauteilklasse(n) + Attributpfade gesetzt
    sind, muss eine Diagnose-Tabelle erscheinen, die fuer einen bekanntermassen
    hochkardinalen Pfad (Pset_BeamCommon.Reference - historisch aus
    usecase_traeger_filtered.json entfernt, siehe dortiger Kommentar) korrekt
    einen deutlichen Kombinationsanstieg zeigt, waehrend ein redundanter Pfad
    (Sonstige.Familie und Typ, deckt dieselbe Information wie die anderen
    Pfade bereits ab) keine zusaetzlichen Kombinationen verursacht. Reine
    Zahlenspalten, keine separate Warnung (bewusst entfernt)."""
    import glob
    beam_files = []
    for pattern in [
        "../testdaten/ifcnet/extracted/IfcBeam/train/*.ifc",
        "../testdaten/ifcnet/extracted/IfcBeam/test/*.ifc",
    ]:
        beam_files.extend(glob.glob(pattern))
    assert beam_files, "IFCNet-Traegerdaten nicht gefunden"

    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = beam_files
    at.run(timeout=60)
    assert not at.exception

    seed_class_ms = next(m for m in at.multiselect if m.key == "draft_seed_class")
    seed_class_ms.set_value(["IfcBeam"]).run(timeout=120)
    assert not at.exception, f"Exception nach Klassenauswahl: {at.exception}"

    _add_paths_via_ui(at, [
        "$.schema.Sonstige.Familie und Typ",
        "$.schema.Pset_BeamCommon.Reference",
        "$.schema.Tekla Common.Profile",
    ], timeout=120)
    assert not at.exception, f"Exception nach Pfadeingabe: {at.exception}"

    dataframes = at.dataframe
    assert dataframes, "Kardinalitaets-Tabelle wurde nicht angezeigt"
    diag_df = dataframes[0].value
    rows_by_path = {r["Attributpfad"]: r for r in diag_df.to_dict("records")}

    reference_row = rows_by_path["$.schema.Pset_BeamCommon.Reference"]
    familie_row = rows_by_path["$.schema.Sonstige.Familie und Typ"]

    # Reference ist fast instanzweise eindeutig und muss die Kombinationen
    # deutlich erhoehen (siehe Standalone-Verifikation: 32 -> 210).
    assert reference_row["Kombinationen mit allen Pfaden"] > reference_row["Kombinationen ohne diesen Pfad"] + 50
    # Familie und Typ ist redundant zu den anderen beiden Pfaden -> keine
    # zusaetzlichen Kombinationen.
    assert familie_row["Kombinationen mit allen Pfaden"] == familie_row["Kombinationen ohne diesen Pfad"]


def test_save_csv_to_chosen_folder():
    """CSV-Export in einen frei waehlbaren Zielordner statt nur ueber den
    Browser-Download - inkl. automatischem Anlegen eines noch nicht
    existierenden (auch verschachtelten) Zielordners."""
    test_dir = tempfile.mkdtemp(prefix="gui_export_test_")
    try:
        at = AppTest.from_file("src/gui_app.py")
        at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
        at.session_state["result_rows"] = [
            {"Anwendungsfall": "Tragende Funktion", "Quelldatei": "test_walls.ifc",
             "Name": "Wand_001", "IFC-Klasse": "IfcWall", "GUID": "abc123",
             "LoadBearing": "True", "Kategorie": "tragend", "Begründung": "Testzeile"},
        ]
        at.run(timeout=60)
        assert not at.exception

        at.text_input(key="export_folder").input(test_dir).run(timeout=60)
        assert not at.exception

        save_button = next(b for b in at.button if b.label == "CSV im Zielordner speichern")
        save_button.click().run(timeout=60)
        assert not at.exception, f"Exception beim Speichern: {at.exception}"

        expected_csv = os.path.join(test_dir, "klassifikation.csv")
        assert os.path.exists(expected_csv)
        content = open(expected_csv, encoding="utf-8-sig").read()
        assert "tragend" in content and "Wand_001" in content

        # noch nicht existierender (verschachtelter) Zielordner muss automatisch
        # angelegt werden
        nested_dir = os.path.join(test_dir, "neuer_unterordner")
        at.text_input(key="export_folder").input(nested_dir).run(timeout=60)
        save_button2 = next(b for b in at.button if b.label == "CSV im Zielordner speichern")
        save_button2.click().run(timeout=60)
        assert not at.exception
        assert os.path.exists(os.path.join(nested_dir, "klassifikation.csv"))
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_claude_backend_requires_api_key():
    """Der Backend-Umschalter in der Seitenleiste blendet bei Auswahl von
    'Claude API (Cloud)' ein API-Key-Feld ein; ohne eingetragenen Key muss
    ein Klassifikationsversuch eine klare Fehlermeldung zeigen statt
    abzustuerzen oder (schlimmer) unbemerkt gegen Ollama zu laufen. Braucht
    keinen echten Netzwerkaufruf (weder Ollama noch Claude), deshalb kurzes
    Timeout."""
    at = AppTest.from_file("src/gui_app.py")
    at.session_state["ifc_paths"] = ["data/test_walls.ifc"]
    at.session_state["usecases"] = [
        {
            "id": "uc1", "seed_classes": ["IfcWall"], "concept": "Tragende Funktion",
            "concept_question": "Ist das Bauteil tragend oder nicht tragend?",
            "categories": ["tragend", "nicht tragend", "unbekannt"],
            "attribute_paths": ["$.schema.Pset_WallCommon.LoadBearing"],
        },
    ]
    at.run(timeout=60)
    assert not at.exception

    # Vor der Umschaltung: Ollama ist per Default gewaehlt, kein Key-Feld.
    assert not any(t.key == "anthropic_api_key" for t in at.text_input)

    at.radio(key="llm_backend_choice").set_value("Claude API (Cloud)").run(timeout=60)
    assert not at.exception, f"Exception nach Backend-Umschaltung: {at.exception}"
    assert any(t.key == "anthropic_api_key" for t in at.text_input), (
        "API-Key-Feld muss nach Auswahl von Claude API erscheinen"
    )

    classify_button = next(b for b in at.button if b.label == "Alle Anwendungsfälle klassifizieren")
    classify_button.click().run(timeout=60)
    assert not at.exception, f"Exception statt kontrollierter Fehlermeldung: {at.exception}"
    assert any("API-Key" in e.value for e in at.error), (
        "Erwartete Fehlermeldung zum fehlenden API-Key nicht gefunden"
    )
    # Ohne Key darf keine Klassifikation stattgefunden haben (kein
    # unbemerkter Rueckfall auf Ollama).
    assert "result_rows" not in at.session_state or not at.session_state["result_rows"]


if __name__ == "__main__":
    test_class_check_button_actually_runs()
    test_add_multiple_usecases()
    test_custom_usecase_with_multiple_seed_classes()
    test_unbekannt_category_is_implicit()
    test_leitfrage_auto_generated_and_overridable()
    test_single_edit_cycle()
    test_save_and_load_project_preset()
    test_project_load_dropdown_appears_right_after_saving()
    test_classification_across_multiple_seed_classes()
    test_zero_signal_followup_namenssuche()
    test_form_expander_stays_open_while_adding_paths()
    test_attribute_path_rows_add_remove_and_autoclear()
    test_automatisch_vorschlagen_does_not_duplicate_existing_paths()
    test_suggest_button_trims_redundant_paths()
    test_cardinality_check_shows_combination_impact()
    test_save_csv_to_chosen_folder()
    test_claude_backend_requires_api_key()
    print("ALLE PRUEFUNGEN ERFOLGREICH")
