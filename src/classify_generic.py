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

# --- OEFFENTLICHER TEIL START ---
# (siehe sync_public_repo.py: fuer die geteilte App-Kopie wird NUR der
# Abschnitt zwischen diesen beiden Markern uebernommen - alles danach
# braucht ausschliesslich die nicht mitgelieferten Auswertungsskripte,
# siehe Kommentar dort)
MISSING = "(nicht vorhanden)"


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
# --- OEFFENTLICHER TEIL ENDE ---
