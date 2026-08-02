"""
Schema-Extraktion fuer die Klassifikations-Pipeline.

Angelehnt an SchemaRAG-IFC (Cerny & Schneider, 2026): statt pro Instanz
willkuerlich Attribute zu suchen, wird zunaechst pro Seed-Klasse (hier:
IfcWall) eine kompakte, aggregierte Sicht auf alle im Modell tatsaechlich
vorkommenden Attributpfade gebaut ("Schema-Kontext"). Diese aggregierte
Sicht wird spaeter EINMAL an das LLM zur Lokalisierung gegeben (nicht pro
Instanz), das Ergebnis (der beste Attributpfad) wird danach auf alle
Instanzen deterministisch angewendet.

Vereinfachung gegenueber dem Paper: wir traversieren nicht den vollen
Express-Schema-Graphen der IFC-Klassen, sondern nutzen ifcopenshell.util
direkt auf den Instanzen (Property Sets + Material-Zuweisung). Das ist fuer
den Prototyp-Umfang (Architektur-Bauteile, wenige tausend Instanzen)
ausreichend und spart Implementierungsaufwand, den Kernmechanismus
("lookup once, normalize everywhere") uebernimmt es aber vollstaendig.
"""
import json
import os
from collections import defaultdict

import ifcopenshell
import ifcopenshell.util.element as elu


def _material_paths(wall):
    """Liefert Pfad->Wert Paare fuer die Materialzuweisung einer Instanz."""
    paths = {}
    material = elu.get_material(wall)
    if material is None:
        return paths
    # IfcMaterial direkt zugewiesen
    if material.is_a("IfcMaterial"):
        paths["$.schema.Material.Name"] = material.Name
    # IfcMaterialLayerSetUsage / IfcMaterialLayerSet -> erste Schicht als Naeherung
    elif hasattr(material, "ForLayerSet") or material.is_a("IfcMaterialLayerSetUsage"):
        try:
            layer_set = material.ForLayerSet
            if layer_set.MaterialLayers:
                first_layer_material = layer_set.MaterialLayers[0].Material
                if first_layer_material:
                    paths["$.schema.Material.Name"] = first_layer_material.Name
        except AttributeError:
            pass
    return paths


def _make_key(fname, entity, seen_keys):
    """Baut einen Instanz-Key, der auch dann eindeutig bleibt, wenn mehrere
    Instanzen derselben Datei denselben (oder gar keinen) Namen tragen - z.B.
    generische Bezeichnungen wie 'LIGGER' fuer mehrere Traeger. Der
    haeufige Fall (eindeutiger Name) bleibt unveraendert lesbar; nur bei
    einer tatsaechlichen Kollision wird die GlobalId angehaengt."""
    base = f"{fname}::{entity.Name or entity.GlobalId}"
    if base not in seen_keys:
        seen_keys.add(base)
        return base
    key = f"{base}::{entity.GlobalId}"
    seen_keys.add(key)
    return key


def _by_type_safe(model, ifc_class):
    """Wie model.by_type(), aber tolerant gegenueber Klassen, die es im
    Schema dieser Datei gar nicht gibt (z.B. IfcPipeSegment existiert erst
    ab IFC4, nicht in IFC2X3) - bei mehreren hochgeladenen Dateien
    unterschiedlicher Schema-Version sonst ein harter Absturz."""
    try:
        return model.by_type(ifc_class)
    except RuntimeError:
        return []


def extract_instance_paths(wall):
    """Baut ein Pfad->Wert Dict fuer eine einzelne Wand-Instanz."""
    paths = {}
    psets = elu.get_psets(wall, psets_only=True)
    for pset_name, props in psets.items():
        for prop_name, value in props.items():
            if prop_name == "id":
                continue
            path = f"$.schema.{pset_name}.{prop_name}"
            paths[path] = value
    paths.update(_material_paths(wall))
    return paths


def extract_schema_context(ifc_path, seed_class="IfcWall"):
    """
    Extrahiert pro Instanz die Attributpfade und aggregiert sie zu einem
    Schema-Kontext (Pfad -> Beispielwerte) fuer die gesamte Seed-Klasse.

    Returns:
        per_instance: dict[wall_name/guid, dict[path, value]]
        schema_context: dict[path, list[example_values]]  (dedupliziert, max 8 Beispiele)
    """
    return extract_schema_context_multi([ifc_path], seed_class=seed_class)


def extract_schema_context_multi(ifc_paths, seed_class="IfcWall"):
    """
    Wie extract_schema_context, aber ueber mehrere IFC-Dateien hinweg
    (z.B. Tier 1 = mehrere bSI-Beispielmodelle, Tier 2 = synthetische Datei).
    Instanz-Keys werden mit dem Dateinamen praefixiert, um Kollisionen
    zwischen Dateien zu vermeiden; tragen mehrere Instanzen derselben Datei
    zusaetzlich denselben (oder keinen) Namen, wird die GlobalId angehaengt,
    damit keine Instanz stillschweigend eine andere ueberschreibt (siehe
    _make_key). Der Schema-Kontext wird ueber alle Dateien aggregiert, damit
    das LLM die Attributpfade unabhaengig von der Herkunfts-Software sieht
    ("lookup once, normalize everywhere" ueber den gesamten Datenbestand).

    Returns:
        per_instance: dict["<dateiname>::<wall_name>", dict[path, value]]
        schema_context: dict[path, list[example_values]]
    """
    per_instance = {}
    schema_context = defaultdict(set)
    seen_keys = set()

    for ifc_path in ifc_paths:
        model = ifcopenshell.open(ifc_path)
        fname = os.path.basename(ifc_path)
        for wall in _by_type_safe(model, seed_class):
            key = _make_key(fname, wall, seen_keys)
            instance_paths = extract_instance_paths(wall)
            per_instance[key] = instance_paths
            for path, value in instance_paths.items():
                schema_context[path].add(str(value))

    schema_context = {
        path: sorted(values)[:8] for path, values in schema_context.items()
    }
    return per_instance, schema_context


def extract_instance_metadata_multi(ifc_paths, seed_class="IfcWall"):
    """
    Liefert pro Instanz-Key (gleiches Schema wie extract_schema_context_multi)
    Metadaten, die fuer den Output (CSV-Export, IFC-Anreicherung) benoetigt
    werden, aber nicht Teil des Schema-Kontexts fuer das LLM sind.

    Returns:
        dict[instance_key, dict(guid=..., ifc_class=..., name=..., source_file=...)]
    """
    metadata = {}
    seen_keys = set()
    for ifc_path in ifc_paths:
        model = ifcopenshell.open(ifc_path)
        fname = os.path.basename(ifc_path)
        for wall in _by_type_safe(model, seed_class):
            key = _make_key(fname, wall, seen_keys)
            metadata[key] = {
                "guid": wall.GlobalId,
                "ifc_class": wall.is_a(),
                "name": wall.Name,
                "source_file": ifc_path,
            }
    return metadata


if __name__ == "__main__":
    per_instance, schema_context = extract_schema_context("data/test_walls.ifc")
    print("=== Schema-Kontext (aggregiert, geht ans LLM) ===")
    print(json.dumps(schema_context, indent=2, ensure_ascii=False))
    print("\n=== Pro Instanz (Beispiel: erste Wand) ===")
    first_key = next(iter(per_instance))
    print(first_key, "->", json.dumps(per_instance[first_key], indent=2, ensure_ascii=False))
