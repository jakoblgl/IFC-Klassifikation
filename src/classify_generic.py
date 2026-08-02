"""
Generische, nutzerkonfigurierte Klassifikation ueber MEHRERE Attribute
gleichzeitig, statt (wie im ersten Prototyp) auf einen einzigen "besten"
Attributpfad zu reduzieren.

Der Nutzer legt in einer Use-Case-Konfiguration (siehe data/usecase_*.json)
fest:
  - seed_class: welche IFC-Klasse betrachtet wird (z.B. IfcWall)
  - concept / concept_question: welche fachliche Frage beantwortet wird
  - categories: die Zielkategorien (frei waehlbar, nicht vorgegeben)
  - attribute_paths: welche Schema-Pfade dafuer herangezogen werden

Effizienzprinzip bleibt wie im ersten Prototyp erhalten ("lookup once,
normalize everywhere" aus SchemaRAG-IFC), nur dass jetzt nicht ein einzelner
Rohwert, sondern eine ganze WERTEKOMBINATION ueber die gewaehlten Attribute
einmalig klassifiziert und danach auf alle Instanzen mit derselben
Kombination angewendet wird. Das erlaubt es, widerspruechliche Einzelsignale
(z.B. LoadBearing=False, aber Workset="03_Waende_tragend") gemeinsam
abzuwaegen, statt sich blind auf ein einzelnes Attribut zu verlassen.
"""
import json
from collections import defaultdict

MISSING = "(nicht vorhanden)"


CLASSIFICATION_PROMPT = """Du bist Experte fuer die Klassifikation von Bauteilen im Bauwesen (IFC-Daten).

Konzept: {concept}
Frage: {concept_question}
Zielkategorien: {categories}

Ein Bauteil hat folgende Attribute:
{attributes}

Hinweis: Die Attribute koennen sich widersprechen, z.B. weil verschiedene Fachdisziplinen
dieselbe Art von Bauteil unterschiedlich modelliert haben, oder weil einzelne Attribute
fehlen. Waege alle vorhandenen Signale gemeinsam ab und triff eine Gesamtentscheidung.
Wenn die vorhandenen Signale nicht ausreichen, um sicher zu entscheiden, waehle "unbekannt".

Antworte NUR mit validem JSON: {{"category": "..."}}
"""


def extract_combinations(per_instance, attribute_paths):
    """
    Gruppiert Instanzen nach identischer Werte-Kombination ueber die
    gewaehlten attribute_paths (fehlende Attribute werden als MISSING
    gefuehrt, nicht einfach weggelassen, damit "Attribut X fehlt" selbst
    ein Signal ist).

    Returns:
        dict[combo, list[instance_key]]
        combo ist ein Tupel aus (path, value) sortiert nach path -> hashbar.
    """
    groups = defaultdict(list)
    for key, attrs in per_instance.items():
        combo = tuple(
            (path, str(attrs.get(path, MISSING))) for path in attribute_paths
        )
        groups[combo].append(key)
    return dict(groups)


def format_combo_for_prompt(combo):
    lines = [f"- {path}: {value}" for path, value in combo]
    return "\n".join(lines)


def _extract_json(text: str):
    import re
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise


def classify_combinations(client, combinations, concept, concept_question, categories):
    """Ein LLM-Call pro einzigartiger Attribut-Kombination."""
    combo_to_category = {}
    for combo in combinations:
        prompt = CLASSIFICATION_PROMPT.format(
            concept=concept,
            concept_question=concept_question,
            categories=categories,
            attributes=format_combo_for_prompt(combo),
        )
        result = _extract_json(client.complete_json(prompt))
        combo_to_category[combo] = result["category"]
    return combo_to_category


def run_generic_classification(client, per_instance, usecase_config):
    """
    Fuehrt die vollstaendige Pipeline fuer eine Use-Case-Konfiguration aus.

    Returns:
        classification: dict[instance_key, category]
        combo_to_category: dict[combo, category]  (fuer Transparenz/Debugging)
    """
    attribute_paths = usecase_config["attribute_paths"]
    combinations = extract_combinations(per_instance, attribute_paths)

    combo_to_category = classify_combinations(
        client,
        combinations.keys(),
        usecase_config["concept"],
        usecase_config["concept_question"],
        usecase_config["categories"],
    )

    classification = {}
    for combo, keys in combinations.items():
        category = combo_to_category[combo]
        for key in keys:
            classification[key] = category

    return classification, combo_to_category
