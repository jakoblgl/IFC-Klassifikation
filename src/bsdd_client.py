"""
Anbindung an die buildingSMART Data Dictionary (bSDD) API - liefert
autoritative, mehrsprachige Definitionen fuer die Standard-Psets/Properties
einer IFC-Klasse (z.B. "Statisch tragend" / "Gibt an, ob das Objekt Lasten
aufnehmen soll..." fuer Pset_WallCommon.LoadBearing).

Wird NICHT fuer die eigentliche Klassifikation verwendet (die bleibt
vollstaendig lokal/offline) - nur optional als Unterstuetzung bei der
Auswahl relevanter Attributpfade (siehe classify_dynamic.py). Einzige an
bSDD uebertragene Information ist der generische IFC-Klassenname (z.B.
"IfcBeam"), niemals projektspezifische Daten (Namen, GUIDs, Attributwerte
etc.) - im Unterschied zum Rest der App braucht dieser eine, optionale
Schritt eine Internetverbindung; bei Nichterreichbarkeit wird das toleriert
(leeres Ergebnis statt Absturz), da es eine reine Anreicherung ist.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BSDD_BASE = "https://api.bsdd.buildingsmart.org"
IFC_DICTIONARY_URI = "https://identifier.buildingsmart.org/uri/buildingsmart/ifc/4.3"


def get_class_properties(ifc_class, language_code="de", timeout=10):
    """
    Fragt bSDD nach den Standard-Pset-Eigenschaften einer IFC-Klasse ab und
    liefert sie im selben Pfadformat wie
    schema_extraction.extract_instance_paths ("$.schema.<Pset>.<Property>"),
    damit sie sich direkt gegen einen Schema-Kontext abgleichen lassen.

    Returns:
        dict[path, dict(name=..., definition=...)] - leeres dict bei
        Netzwerkfehler oder wenn die Klasse in bSDD nicht existiert (kein
        Absturz, da dies eine rein unterstuetzende, optionale Anreicherung
        ist und der Rest der App auch ohne Internetverbindung funktionieren
        muss).
    """
    class_uri = f"{IFC_DICTIONARY_URI}/class/{ifc_class}"
    params = urllib.parse.urlencode({
        "Uri": class_uri,
        "IncludeClassProperties": "true",
        "languageCode": language_code,
    })
    url = f"{BSDD_BASE}/api/Class/v1?{params}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        # OSError statt einzelner Subklassen: deckt u.a. socket.timeout ab,
        # das in Python 3.9 NICHT von urllib.error.URLError/TimeoutError
        # abgedeckt wird (verursachte einen echten Absturz bei einem
        # tatsaechlichen Netzwerk-Timeout waehrend der Verifikation).
        # Da dies eine rein optionale Anreicherung ist, soll JEDER
        # Netzwerkfehler still zu einem leeren Ergebnis fuehren.
        return {}

    result = {}
    for prop in data.get("classProperties", []):
        pset = prop.get("propertySet")
        code = prop.get("propertyCode")
        if not pset or not code:
            continue
        path = f"$.schema.{pset}.{code}"
        result[path] = {
            "name": prop.get("name", ""),
            "definition": prop.get("definition", ""),
        }
    return result
