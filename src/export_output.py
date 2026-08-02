"""
Zwei Output-Formen fuer die Klassifikationsergebnisse:

1. CSV-Tabelle: einfache, sofort lesbare Uebersicht (GUID, Name, IFC-Klasse,
   erkannter Rohwert, zugewiesene Kategorie). Entspricht dem, was die
   Interviewpartnerin (Interview 4) aktuell manuell in Solibri/Excel pflegt.

2. Angereicherte IFC-Datei: die Kategorie wird als echte IfcClassification
   in eine Kopie der jeweiligen Quelldatei zurueckgeschrieben
   (IfcClassification + IfcClassificationReference +
   IfcRelAssociatesClassification). Damit ist die Klassifikation nicht nur
   eine externe Tabelle, sondern Teil des Modells selbst und in Solibri/
   Desite direkt als Klassifikationssystem importierbar/filterbar -
   methodisch eine Form von Semantic Enrichment (vgl. Kapitel 4 der Arbeit).
"""
import csv
import json
import os

import ifcopenshell
import ifcopenshell.guid as guid_util


def export_csv(classification, metadata, mapping, used_path, out_path):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            "Quelldatei", "Name", "IFC-Klasse", "GUID",
            "erkannter_Pfad", "Rohwert", "Kategorie",
        ])
        for key, category in classification.items():
            meta = metadata.get(key, {})
            source_file = meta.get("source_file", "")
            fname = os.path.basename(source_file)
            raw_value = None
            for raw, cat in mapping.items():
                if cat == category:
                    raw_value = raw
            writer.writerow([
                fname,
                meta.get("name", ""),
                meta.get("ifc_class", ""),
                meta.get("guid", ""),
                used_path,
                raw_value or "",
                category,
            ])
    print(f"CSV geschrieben: {out_path}")


def enrich_ifc_files(classification, metadata, classification_system_name, out_dir):
    """
    Schreibt pro Quelldatei eine Kopie mit eingebetteter IfcClassification.
    """
    os.makedirs(out_dir, exist_ok=True)

    by_source_file = {}
    for key, category in classification.items():
        meta = metadata.get(key)
        if not meta:
            continue
        by_source_file.setdefault(meta["source_file"], []).append((key, meta, category))

    written_files = []
    for source_file, entries in by_source_file.items():
        model = ifcopenshell.open(source_file)

        classification_entity = model.create_entity(
            "IfcClassification",
            Source="Prototyp Masterarbeit",
            Edition="1.0",
            Name=classification_system_name,
        )

        category_to_ref = {}
        for _, _, category in entries:
            if category in category_to_ref:
                continue
            ref = model.create_entity(
                "IfcClassificationReference",
                Identification=category,
                Name=category,
                ReferencedSource=classification_entity,
            )
            category_to_ref[category] = ref

        by_category = {}
        for key, meta, category in entries:
            elements = model.by_guid(meta["guid"])
            by_category.setdefault(category, []).append(elements)

        for category, elements in by_category.items():
            model.create_entity(
                "IfcRelAssociatesClassification",
                GlobalId=guid_util.new(),
                Name=f"Klassifikation: {category}",
                RelatedObjects=elements,
                RelatingClassification=category_to_ref[category],
            )

        base = os.path.basename(source_file)
        name, ext = os.path.splitext(base)
        out_path = os.path.join(out_dir, f"{name}_classified{ext}")
        model.write(out_path)
        written_files.append(out_path)
        print(f"Angereicherte IFC geschrieben: {out_path} ({len(entries)} Bauteile)")

    return written_files


def main(backend="ollama"):
    results = json.load(open(f"data/results_{backend}.json", encoding="utf-8"))
    classification = results["classification"]
    mapping = results["mapping"]
    used_path = results["used_path"]

    import sys
    sys.path.insert(0, "src")
    from schema_extraction import extract_instance_metadata_multi

    ifc_paths = [
        "../../beispielmodelle_buildingSMART/Building-Architecture.ifc",
        "../../beispielmodelle_buildingSMART/Building-Structural.ifc",
        "data/test_walls.ifc",
    ]
    metadata = extract_instance_metadata_multi(ifc_paths)

    export_csv(
        classification, metadata, mapping, used_path,
        out_path=f"data/klassifikation_{backend}.csv",
    )
    enrich_ifc_files(
        classification, metadata,
        classification_system_name="Wandtyp-Klassifikation (Prototyp)",
        out_dir="data/enriched",
    )


if __name__ == "__main__":
    import sys
    backend = sys.argv[1] if len(sys.argv) > 1 else "ollama"
    main(backend)
