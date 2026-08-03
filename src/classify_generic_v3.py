"""
Dritter Prompt-Versuch: Hypothese aus v2 war, dass "erkennen" funktioniert,
aber "entscheiden" nicht - moeglicherweise weil viele explizit angezeigte
"(nicht vorhanden)"-Felder das Modell zu uebertriebener Vorsicht verleiten,
selbst wenn das EINE vorhandene Attribut fuer sich genommen eindeutig ist.

Aenderung: Leere Attribute werden dem Modell gar nicht erst gezeigt (statt
als "(nicht vorhanden)" aufgelistet), und der Prompt stellt explizit klar,
dass ein einzelnes aussagekraeftiges Attribut ausreicht.

Spaetere Ergaenzung: der IFC-Basisattribut "Name" (z.B. "Trockenbauwand")
wird als Signal herangezogen, aber AUSSCHLIESSLICH als Fallback fuer
Instanzen OHNE jegliches bekannte Pset-Attribut - er ist kein genormtes
IFC-Attribut wie ein Pset-Wert, sondern frei vom Modellierer vergeben und
damit unzuverlaessiger. Instanzen MIT mindestens einem bekannten
Pset-Attribut werden ausschliesslich anhand dieser Pset-Attribute
klassifiziert; der Name spielt dort ueberhaupt keine Rolle (auch nicht als
Beispiel-/Zusatzkontext), da ein frueherer Testlauf zeigte, dass selbst ein
vager Namenshinweis ("LIGGER") ein Modell trotz eindeutigem, widersprechendem
Pset-Attribut ("Grade: S235JR", also Stahl) zu einer falschen Kategorie
("Stahlbetontraeger") verleiten konnte - die im Prompt formulierte
Vorrangregel fuer Pset-Attribute wird von einem kleinen lokalen Modell nicht
zuverlaessig genug befolgt, um dieses Risiko einzugehen.
Instanzen OHNE jegliches bekanntes Pset-Attribut wuerden sonst alle in einer
einzigen, bedeutungslosen "kein Signal"-Gruppe landen (eine "Trockenbauwand"
ohne Pset-Daten waere identisch zu einer "Tragwand" ohne Pset-Daten behandelt
worden). Diese werden daher zusaetzlich nach ihrem Namen aufgeteilt -
weiterhin ein Aufruf pro eindeutigem Namen, nicht pro Instanz.
"""
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

NAME_PATH = "$.name"

CLASSIFICATION_PROMPT_V3 = """Du bist Experte fuer die Klassifikation von Bauteilen im Bauwesen (IFC-Daten).

Konzept: {concept}
Frage: {concept_question}
Zielkategorien: {categories}

Fuer dieses Bauteil sind folgende Attribute BEKANNT (nicht aufgefuehrte Attribute
wurden im Modell schlicht nicht gepflegt, das ist normal und kein Unsicherheitssignal):
{attributes}

Hinweise:
- Fachbegriffe und Abkuerzungen tauchen haeufig nur als Teil eines laengeren Bezeichners auf
  (z.B. eingebettet in Familiennamen oder Profilbezeichnungen). Achte auch auf solche
  eingebetteten Teilbegriffe.{name_bullet}
- Wenn AUCH NUR EIN einziges bekanntes Attribut eine eindeutige fachliche Zuordnung
  erlaubt, entscheide danach -- das Fehlen anderer Attribute ist dabei irrelevant und
  KEIN Grund fuer "unbekannt".
- Waehle "unbekannt" ausschliesslich dann, wenn keines der bekannten Attribute einen
  erkennbaren fachlichen Hinweis liefert.

Antworte NUR mit validem JSON in genau dieser Reihenfolge:
{{"erkannte_hinweise": "kurze Nennung der textlichen Anhaltspunkte",
  "category": "..."}}
"""

# Nur an Kombinationen MIT einer Bauteilname-Zeile angehaengt (echter
# Phase-2-Fallback, siehe classify_combinations_v3) - fuer has-signal-Faelle
# OHNE Bauteilname-Zeile bleibt name_bullet komplett leer (nicht nur der
# Warnhinweis-Zusatz), da ein frueherer Test zeigte, dass schon das blosse
# Erwaehnen von "Bauteilname" als Konzept im gemeinsamen Prompt - selbst ohne
# passende Zeile im konkreten Fall - die Genauigkeit bei has-signal-
# Kombinationen messbar verschlechterte (24/24 -> 22/24 auf dem
# Traeger-Testset) und dazu verleitete, einen Pset-Wert faelschlich als
# "Bauteilname" zu labeln, nur weil sein Inhalt namensartig klingt (z.B.
# "Familie und Typ": "ARC_STB_Unterzug/Ueberzug: Unterzug_STB_40x100") - ein
# kleines lokales Modell reagiert offenbar empfindlich auf zusaetzlichen,
# fuer den konkreten Fall irrelevanten Prompt-Text.
_NAME_BULLET = (
    "\n- \"Bauteilname\" ist der frei vom Modellierer vergebene Name (kein genormtes"
    " IFC-Attribut wie die uebrigen Pset-Werte), oben an der Zeile \"Bauteilname:\""
    " erkennbar. Er kann ein nuetzliches Indiz sein (z.B. \"Trockenbauwand\" fuer nicht"
    " tragend). Er ist aber NUR dann ein Indiz, wenn er ueber die blosse Bauteilart"
    " hinausgeht (Werkstoff, Bauweise, Fachbegriff). Ein Name, der nur die Bauteilart"
    " selbst wiederholt oder umschreibt (z.B. \"Wand\", \"Wall\", \"Balken\", \"Beam\"),"
    " eine reine Nummerierung/ID ist (z.B. \"Wand 2.017\"), oder ein automatisch"
    " generierter Werkzeug-Bezeichner ohne fachlichen Gehalt ist (z.B."
    " \"Basic Wall:wall_105_270.00mm:2e6917f8\"), liefert KEINEN Hinweis -- in diesem Fall"
    " ist \"unbekannt\" die richtige Antwort, auch wenn der Bauteilname das einzige"
    " bekannte Attribut ist. Rate NICHT anhand eines inhaltsleeren Namens."
)

def _extract_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise


def extract_combinations_v3(per_instance, attribute_paths, names, missing_marker="(nicht vorhanden)"):
    """
    Wie extract_combinations (classify_generic.py), aber Instanzen OHNE
    jegliches bekanntes Pset-Attribut werden zusaetzlich nach ihrem
    Bauteilnamen aufgeteilt statt alle in einer Gruppe zu landen (siehe
    Modul-Docstring). Instanzen MIT mindestens einem bekannten Pset-Attribut
    bleiben wie bisher rein nach der Attribut-Kombination gruppiert.

    Args:
        names: dict[instance_key, name] - IFC-Basisattribut "Name" je Instanz
               (z.B. aus extract_instance_metadata_multi()[key]["name"]).

    Returns:
        dict[combo, list[instance_key]]
        combo ist ein Tupel aus (path, value)-Paaren; bei Instanzen ohne
        Pset-Signal zusaetzlich ein abschliessendes (NAME_PATH, name)-Paar.
    """
    groups = defaultdict(list)
    for key, attrs in per_instance.items():
        combo = tuple(
            (path, str(attrs.get(path, missing_marker))) for path in attribute_paths
        )
        has_pset_signal = any(value != missing_marker for _, value in combo)
        if not has_pset_signal:
            combo = combo + ((NAME_PATH, str(names.get(key) or missing_marker)),)
        groups[combo].append(key)
    return dict(groups)


def format_combo_known_only(combo, missing_marker="(nicht vorhanden)"):
    """Wie format_combo_for_prompt, aber leere Attribute werden weggelassen
    statt als 'nicht vorhanden' aufgefuehrt. Ein (NAME_PATH, ...)-Eintrag im
    Combo-Tupel (siehe extract_combinations_v3, nur bei fehlendem
    Pset-Signal vorhanden) wird als eigene "Bauteilname"-Zeile ausgegeben.
    Kombinationen MIT Pset-Signal enthalten nie einen NAME_PATH-Eintrag,
    der Bauteilname fliesst dort also bewusst gar nicht in den Prompt ein
    (siehe Modul-Docstring)."""
    pset_lines = [
        f"- {path}: {value}" for path, value in combo
        if path != NAME_PATH and value != missing_marker
    ]
    name_value = next((value for path, value in combo if path == NAME_PATH), None)

    if name_value is not None:
        # Kombination ohne jegliches Pset-Signal - der Bauteilname ist hier
        # das einzige verfuegbare Attribut.
        if name_value == missing_marker:
            return "(keines der betrachteten Attribute ist im Modell gepflegt, auch kein Bauteilname)"
        return f"(keines der betrachteten Pset-Attribute ist im Modell gepflegt)\n- Bauteilname: {name_value}"

    if not pset_lines:
        return "(keines der betrachteten Attribute ist im Modell gepflegt)"

    return "\n".join(pset_lines)


# Jede Kombination bekommt weiterhin ihren eigenen, unvermischten Prompt wie
# bisher (siehe Moduldocstring - Batching mehrerer Kombinationen in EINEN
# Prompt verschlechtert nachweislich die Genauigkeit) - hier wird nur die
# Ausfuehrung mehrerer voneinander unabhaengiger Einzel-Aufrufe parallelisiert,
# nicht deren Inhalt vermischt. Per Benchmark gegen die lokale Ollama-Instanz
# (CPU-gebunden, keine dedizierte GPU) empirisch ermittelt: 2 gleichzeitige
# Aufrufe brachten einen Faktor ~3.7x gegenueber sequentiell, 4 brachten
# GEGENUEBER 2 keinen weiteren Gewinn mehr (Hardware saettigt sich) - daher 2
# als Standardwert statt eines beliebig hoch gegriffenen.
DEFAULT_MAX_WORKERS = 2


def classify_combinations_v3(client, combinations, concept, concept_question, categories, on_progress=None,
                              max_workers=DEFAULT_MAX_WORKERS):
    """
    Args:
        combinations: dict[combo, list[instance_key]] (aus extract_combinations_v3
            oder, fuer reine Pset-Kombinationen ohne Namensfallback, aus
            classify_generic.extract_combinations).
        on_progress: optionales callable(done, total), nach jeder klassifizierten
            Kombination aufgerufen (z.B. fuer eine Fortschrittsanzeige in der GUI) -
            hat keinen Einfluss auf das Klassifikationsergebnis. Wird trotz
            paralleler Ausfuehrung IMMER im aufrufenden Thread aufgerufen (ueber
            as_completed() im Hauptthread, nicht aus dem Worker-Thread heraus) -
            wichtig, da z.B. Streamlit-Widget-Updates aus einem Nicht-Hauptthread
            nicht sicher funktionieren.
        max_workers: Anzahl gleichzeitiger LLM-Aufrufe, siehe DEFAULT_MAX_WORKERS.
    """
    combo_to_category = {}
    combo_to_evidence = {}
    combo_to_basis = {}
    total = len(combinations)

    def _classify_one(combo):
        has_name = any(path == NAME_PATH for path, _ in combo)
        prompt = CLASSIFICATION_PROMPT_V3.format(
            concept=concept,
            concept_question=concept_question,
            categories=categories,
            attributes=format_combo_known_only(combo),
            name_bullet=_NAME_BULLET if has_name else "",
        )
        return _extract_json(client.complete_json(prompt))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_combo = {executor.submit(_classify_one, combo): combo for combo in combinations}
        for i, future in enumerate(as_completed(future_to_combo), start=1):
            combo = future_to_combo[future]
            result = future.result()
            category = result["category"]
            combo_to_category[combo] = category
            combo_to_evidence[combo] = result.get("erkannte_hinweise", "")
            # Basis wird deterministisch aus dem Code abgeleitet, nicht mehr
            # vom Modell selbst erfragt: welcher Mechanismus verwendet
            # wurde, steht schon fest, sobald bekannt ist, ob dieser
            # Kombination ueberhaupt eine Bauteilname-Zeile beilag (siehe
            # extract_combinations_v3 - NAME_PATH wird NUR angehaengt, wenn
            # gar kein Pset-Signal vorhanden war, die beiden Faelle schliessen
            # sich also gegenseitig aus). Das Modell selbst hat sich dabei
            # nachweislich gelegentlich vertan (beobachtet am 2026-08-02:
            # ein Pset-Wert "Sonstige.Familie und Typ" mit dem namensartig
            # klingenden Inhalt "ARC_STB_Unterzug/Ueberzug: ..." wurde
            # faelschlich als "Bauteilname" gelabelt, obwohl kein
            # NAME_PATH-Fallback im Spiel war).
            has_name = any(path == NAME_PATH for path, _ in combo)
            if category == "unbekannt":
                combo_to_basis[combo] = "keine"
            else:
                combo_to_basis[combo] = "Bauteilname" if has_name else "Pset-Attribute"
            if on_progress is not None:
                on_progress(i, total)
    return combo_to_category, combo_to_evidence, combo_to_basis


def run_generic_classification_v3(client, per_instance, names, usecase_config):
    attribute_paths = usecase_config["attribute_paths"]
    combinations = extract_combinations_v3(per_instance, attribute_paths, names)

    combo_to_category, combo_to_evidence, combo_to_basis = classify_combinations_v3(
        client,
        combinations,
        usecase_config["concept"],
        usecase_config["concept_question"],
        usecase_config["categories"],
    )

    classification = {}
    for combo, keys in combinations.items():
        category = combo_to_category[combo]
        for key in keys:
            classification[key] = category

    return classification, combo_to_category, combo_to_evidence, combo_to_basis
