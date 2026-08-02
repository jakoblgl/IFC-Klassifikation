"""
Dynamische Mehrfach-Pfad-Lokalisierung: kombiniert die urspruengliche
Lokalisierungsidee (LLM waehlt relevante Attributpfade aus dem gesamten
Schema-Kontext, statt dass der Nutzer sie fest vorgibt) mit der spaeteren
Mehrattribut-Klassifikation (classify_generic.py). Sinnvoll, wenn wie bei
Traegern/Stuetzen unklar ist, ob und wo im Schema ueberhaupt brauchbare
Signale existieren (z.B. Grade/Profile-Felder statt Standard-Material-
Zuordnung bei Tekla-Exporten).

Der Nutzer bekommt die vorgeschlagenen Pfade vor der eigentlichen
Klassifikation zur Bestaetigung/Korrektur - die Entscheidungshoheit bleibt
also erhalten, nur die Vorauswahl wird automatisiert statt dass der Nutzer
hunderte Schema-Pfade selbst durchsuchen muss.
"""
import json
import re


LOCALIZATION_MULTI_PROMPT = """Du bekommst den vollstaendigen Schema-Kontext (alle beobachteten
Attributpfade mit Beispielwerten) fuer Bauteile der Klasse {seed_class}.

Schema-Kontext (Pfad -> Beispielwerte, bei einigen Pfaden zusaetzlich die
autoritative Definition aus dem buildingSMART Data Dictionary - "bsdd_name"/
"bsdd_definition" - falls es sich um ein genormtes IFC-Pset-Attribut handelt;
Pfade ohne diese Felder sind projekt-/werkzeugspezifisch und nicht genormt):
{schema_context}

Konzept: {concept}
Frage: {concept_question}
Zielkategorien: {categories}

Aufgabe: Welche Attributpfade (maximal {max_paths}) sind gemeinsam am besten geeignet,
um die Frage zu beantworten?

Regeln:
- Nur Pfade aus dem gegebenen Schema-Kontext zurueckgeben, keine erfundenen Pfade.
- Beruecksichtige nicht nur Pfade, die woertlich zur Frage passen (z.B. ein Attribut namens
  "LoadBearing" bei einer Frage nach tragend/nicht tragend), sondern auch Pfade, die den
  Bauteiltyp, das Material, das Gewerk oder die Bauweise beschreiben (z.B. "Material",
  "Gewerk", "Bauteiltyp", "Familie und Typ", "Bezeichnung") - deren Beispielwerte koennen die
  Frage indirekt beantworten, auch ohne ein woertlich passendes Attribut. Beispiel: bei der
  Frage nach tragend/nicht tragend weist ein Gewerk-Attribut mit dem Wert
  "Trockenbauarbeiten" oder ein Material-Attribut mit dem Wert "Gipskarton" typischerweise auf
  ein nicht tragendes Bauteil hin.
- Ist fuer einen Pfad eine bsdd_definition angegeben, nutze sie als verlaesslichen Hinweis
  auf die fachliche Bedeutung des Attributs (staerker zu gewichten als eine Vermutung allein
  aus dem Pfadnamen oder den Beispielwerten).
- Bevorzuge Pfade mit aussagekraeftigen, nicht rein numerischen/generischen Werten.
- Wenn mehrere Pfade dieselbe Information redundant enthalten, wähle nur den besten davon.
- WICHTIG: Ordne die zurueckgegebenen Pfade nach absteigender Wichtigkeit/Verlaesslichkeit
  fuer die Frage - der erste Pfad in der Liste soll der fachlich aussagekraeftigste sein,
  nicht der Pfad mit den meisten Instanzen. Ein Pfad mit wenigen, aber eindeutigen Werten
  (z.B. ein genormtes Pset-Attribut) ist wichtiger als ein Pfad, der zwar fuer viele Instanzen
  befuellt ist, aber nur schwach mit der Frage zusammenhaengt.
- Antworte NUR mit validem JSON: {{"paths": ["wichtigster Pfad zuerst", "..."]}}
"""


def _extract_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise


def _looks_numeric(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def prefilter_schema_context(schema_context, max_candidates=40):
    """
    Billiger Vorfilter vor dem eigentlichen LLM-Aufruf (kein LLM-Call, nur
    Heuristik): entfernt Pfade, deren Beispielwerte durchgehend rein
    numerisch sind (Masse, Koordinaten, Gewichte etc.) - fuer
    Typ-/Kategorie-Konzepte unwahrscheinlich relevant. Reduziert grosse
    Schema-Kontexte (hier: 330 -> handhabbare Kandidatenmenge), damit der
    nachfolgende LLM-Aufruf mit vertretbarer Kontextgroesse laeuft.
    """
    candidates = {}
    for path, values in schema_context.items():
        non_numeric = [v for v in values if not _looks_numeric(v)]
        if non_numeric:
            candidates[path] = values

    if len(candidates) <= max_candidates:
        return candidates

    # Grobe Priorisierung: Pfade mit 2-20 unterschiedlichen Werten sind meist
    # informativer als solche mit nur 1 (konstant) oder sehr vielen (z.B.
    # GUID-artige Freitextfelder).
    def score(item):
        _, values = item
        n = len(values)
        return abs(n - 6)  # Praeferenz fuer ca. 6 unterschiedliche Werte

    ranked = sorted(candidates.items(), key=score)
    return dict(ranked[:max_candidates])


def suggest_attribute_paths(client, schema_context, seed_class, concept, concept_question, categories, max_paths=5,
                             prefilter_max=40, bsdd_properties=None):
    """
    bsdd_properties: optional dict[path, dict(name=..., definition=...)]
    (siehe bsdd_client.get_class_properties) - autoritative Definitionen fuer
    genormte IFC-Pset-Attribute, rein optionale Anreicherung der Pfade, die
    ohnehin schon im schema_context vorkommen (kein Ersatz fuer den
    tatsaechlich beobachteten Schema-Kontext, keine neuen Pfade).
    """
    filtered_context = prefilter_schema_context(schema_context, max_candidates=prefilter_max)
    bsdd_properties = bsdd_properties or {}

    enriched_context = {}
    for path, examples in filtered_context.items():
        entry = {"beispielwerte": examples}
        bsdd_info = bsdd_properties.get(path)
        if bsdd_info and bsdd_info.get("definition"):
            entry["bsdd_name"] = bsdd_info["name"]
            entry["bsdd_definition"] = bsdd_info["definition"]
        enriched_context[path] = entry

    prompt = LOCALIZATION_MULTI_PROMPT.format(
        seed_class=seed_class,
        schema_context=json.dumps(enriched_context, ensure_ascii=False, indent=2),
        concept=concept,
        concept_question=concept_question,
        categories=categories,
        max_paths=max_paths,
    )
    result = _extract_json(client.complete_json(prompt))
    paths = result["paths"]
    # Sicherheitsnetz: nur tatsaechlich vorhandene Pfade uebernehmen
    return [p for p in paths if p in schema_context]
