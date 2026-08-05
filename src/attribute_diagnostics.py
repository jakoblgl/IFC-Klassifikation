"""
Kardinalitaets-Diagnose fuer konfigurierte Attributpfade.

Hintergrund: schema_extraction.extract_schema_context_multi kappt die
Beispielwerte je Pfad auf 8 (siehe dortiger Docstring) - ein Pfad mit 9
unterschiedlichen Werten sieht danach identisch aus wie einer mit 9000.
path_cardinality() rechnet stattdessen direkt gegen die vollstaendigen,
ungekappten per_instance-Daten - kein LLM-Aufruf, nur Zaehlen. Wird sowohl
von classify_dynamic.prefilter_schema_context genutzt (Vorfilterung der
LLM-Kandidaten nach tatsaechlicher Abdeckung statt nach der gekappten
Beispielwerte-Liste, damit hochkardinale, aber wenig populierte Pfade
nicht bevorzugt werden) als auch hier direkt, um dem Nutzer vor der
Klassifikation zu zeigen, welche der konfigurierten Attributpfade das
Lookup-once-Prinzip (ein Aufruf pro einzigartiger Kombination statt pro
Instanz, siehe classify_generic.py) gefaehrden, weil sie die Anzahl der
Kombinationen unverhaeltnismaessig stark erhoehen.
"""
from classify_generic import extract_combinations, MISSING


def path_cardinality(per_instance, path, missing_marker=MISSING):
    """
    Liefert die tatsaechliche (ungekappte) Kardinalitaet eines einzelnen
    Attributpfads ueber alle uebergebenen Instanzen.

    Returns:
        dict(n_instances, n_populated, n_distinct, ratio) - ratio ist
        n_distinct / n_populated (0.0 falls keine Instanz den Pfad hat);
        hohe ratio (nahe 1.0) bedeutet: der Pfad hat fuer fast jede
        Instanz, die ihn ueberhaupt fuehrt, einen eigenen Wert (ID-artig).
    """
    populated = [
        str(attrs[path]) for attrs in per_instance.values()
        if attrs.get(path, missing_marker) != missing_marker
    ]
    n_distinct = len(set(populated))
    return {
        "n_instances": len(per_instance),
        "n_populated": len(populated),
        "n_distinct": n_distinct,
        "ratio": (n_distinct / len(populated)) if populated else 0.0,
    }


def marginal_combo_impact(per_instance, attribute_paths):
    """
    Fuer jeden konfigurierten Pfad: wie viele einzigartige Kombinationen
    (= LLM-Aufrufe im Klassifikationsschritt) entstuenden OHNE diesen einen
    Pfad, verglichen mit der Gesamtzahl bei ALLEN konfigurierten Pfaden.

    Wichtig: das ist die MARGINALE Kosten-Kennzahl, nicht nur die isolierte
    Kardinalitaet des Pfads - ein hochkardinaler Pfad, der stark mit einem
    bereits vorhandenen Pfad korreliert (z.B. zwei verschiedene ID-Felder
    fuer dieselbe Instanz), erhoeht die Kombinationsanzahl trotzdem kaum,
    weil Instanzen, die sich im einen Pfad unterscheiden, sich meist auch
    im anderen unterscheiden (und umgekehrt).

    Returns:
        dict[path, dict(n_without, n_with_all)]
    """
    n_with_all = len(extract_combinations(per_instance, attribute_paths))
    result = {}
    for path in attribute_paths:
        remaining = [p for p in attribute_paths if p != path]
        n_without = len(extract_combinations(per_instance, remaining)) if remaining else 1
        result[path] = {"n_without": n_without, "n_with_all": n_with_all}
    return result


def path_diagnostics_multi(per_instance_by_class, attribute_paths):
    """
    Wie path_cardinality + marginal_combo_impact, aber ueber mehrere
    Bauteilklassen hinweg aggregiert (ein Anwendungsfall kann mehrere
    seed_classes gleichzeitig betrachten, siehe gui_app.py Abschnitt 2/3) -
    Instanzzahlen/Kombinationszahlen werden je Klasse berechnet und dann
    aufsummiert, analog zur bestehenden Aufrufzaehlung in Abschnitt 3.

    Args:
        per_instance_by_class: list[dict] - ein per_instance-Dict je
            gewaehlter seed_class.

    Returns:
        dict[path, dict(n_instances, n_populated, n_distinct_max, ratio,
                         n_without, n_with_all)]
        n_distinct_max ist die groesste je Klasse beobachtete Kardinalitaet
        (aussagekraeftiger als eine Summe, da Werte zwischen Klassen
        durchaus uebereinstimmen koennen).
    """
    if not attribute_paths or not per_instance_by_class:
        return {}

    totals = {p: {"n_instances": 0, "n_populated": 0, "n_distinct_max": 0,
                   "n_without": 0, "n_with_all": 0} for p in attribute_paths}

    for per_instance in per_instance_by_class:
        if not per_instance:
            continue
        impact = marginal_combo_impact(per_instance, attribute_paths)
        for path in attribute_paths:
            card = path_cardinality(per_instance, path)
            t = totals[path]
            t["n_instances"] += card["n_instances"]
            t["n_populated"] += card["n_populated"]
            t["n_distinct_max"] = max(t["n_distinct_max"], card["n_distinct"])
            t["n_without"] += impact[path]["n_without"]
            t["n_with_all"] += impact[path]["n_with_all"]

    for path, t in totals.items():
        t["ratio"] = (t["n_distinct_max"] / t["n_populated"]) if t["n_populated"] else 0.0

    return totals


def trim_redundant_paths(candidate_paths, per_instance_by_class, min_gain=1):
    """
    Entfernt aus einer Liste vorgeschlagener Attributpfade (z.B. von
    classify_dynamic.suggest_attribute_paths) diejenigen, die gegenueber den
    bereits ausgewaehlten Pfaden keine einzige zusaetzliche Instanz
    abdecken - kein weiterer LLM-Aufruf, nur Zaehlen.

    Hintergrund: das vorschlagende LLM schoepft das erlaubte Maximum an
    Pfaden in der Praxis durchgehend aus (beobachtet: nie weniger als
    max_paths), auch wenn ein Teil der Pfade keine neue Instanzabdeckung
    bringt. Jeder solche Pfad erhoeht trotzdem unnoetig die Anzahl der
    einzigartigen Kombinationen (siehe path_diagnostics_multi) und damit
    die LLM-Aufrufe im spaeteren Klassifikationsschritt.

    WICHTIG (Reihenfolge): dies ist bewusst KEIN klassischer Greedy-Set-
    Cover, der nach maximalem Abdeckungsgewinn umsortiert - die vom LLM
    zurueckgegebene Reihenfolge wird stattdessen als dessen fachliche
    Einschaetzung der Relevanz respektiert. Ein Pfad mit qualitativ
    besseren/verlaesslicheren Werten soll nicht einem Pfad weichen, der
    zufaellig mehr Instanzen roh abdeckt (gute Attributwerte sind wichtiger
    als reine Abdeckung). Es wird daher strikt in der GEGEBENEN Reihenfolge
    durchgegangen und ein Pfad nur verworfen, wenn er GEGENUEBER DEN BEREITS
    AUSGEWAEHLTEN Pfaden keine einzige zusaetzliche Instanz abdeckt.
    Diese Annahme (Reihenfolge = Wichtigkeit) ist NUR gerechtfertigt, weil
    LOCALIZATION_MULTI_PROMPT (classify_dynamic.py) das LLM explizit
    anweist, absteigend nach Wichtigkeit zu sortieren - ohne diese
    Prompt-Anweisung waere die Rueckgabereihenfolge bedeutungslos. Wie
    zuverlaessig ein kleines lokales Modell diese Anweisung tatsaechlich
    befolgt, ist NICHT verifiziert (nur die Coverage-Eigenschaft dieser
    Funktion selbst ist getestet, nicht die Qualitaet der LLM-Sortierung) -
    im Zweifel bleibt der Kardinalitaets-Check die transparente
    Gegenprobe.

    Abdeckung wird UEBER DEN GESAMTEN Datenbestand (ggf. mehrere
    Bauteilklassen/Dateien) gemessen, nicht je Instanz - der Grund, warum
    ueberhaupt mehrere Pfade noetig sind, ist typischerweise
    Werkzeug-Heterogenitaet (Pfad A deckt Revit-Instanzen ab, Pfad B die
    Tekla-Instanzen), nicht dass eine einzelne Instanz mehrere Signale
    braeuchte. Bewusst KONSERVATIV: ob ein verworfener Pfad zusaetzlich zur
    reinen Abdeckung auch noch beim Unterscheiden zwischen Zielkategorien
    geholfen haette (z.B. Stahl vs. Beton bei bereits abgedeckten
    Instanzen), wird hier NICHT bewertet; dafuer bleibt der Kardinalitaets-
    Check (path_diagnostics_multi) die richtige, transparente (nicht
    automatisch entscheidende) Anlaufstelle.

    Args:
        candidate_paths: vorgeschlagene Pfade IN DER REIHENFOLGE DES LLM -
            diese Reihenfolge bestimmt, welcher Pfad bei Ueberschneidung
            Vorrang hat (siehe oben).
        per_instance_by_class: list[dict] - ein per_instance-Dict je
            betrachteter Bauteilklasse (wie bei path_diagnostics_multi).
        min_gain: ein Pfad wird nur behalten, wenn er mindestens so viele
            NEU abgedeckte Instanzen beitraegt (Default 1 - jeder Pfad ohne
            jeglichen Abdeckungsgewinn wird verworfen).

    Returns:
        dict(kept, dropped) - beides Teillisten von candidate_paths, "kept"
        in der urspruenglichen Reihenfolge.
    """
    if not candidate_paths:
        return {"kept": [], "dropped": []}

    kept = []
    covered_so_far = set()
    for path in candidate_paths:
        keys = set()
        for per_instance in per_instance_by_class:
            for key, attrs in per_instance.items():
                if attrs.get(path, MISSING) != MISSING:
                    keys.add(key)
        gain = len(keys - covered_so_far)
        if gain >= min_gain:
            kept.append(path)
            covered_so_far |= keys

    dropped = [p for p in candidate_paths if p not in kept]
    return {"kept": kept, "dropped": dropped}
