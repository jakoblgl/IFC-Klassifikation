"""
Reine Unit-Tests fuer classify_generic_v3.py (kein Streamlit/AppTest noetig,
im Gegensatz zu test_gui_app.py) - insbesondere fuer die Parallelisierung von
classify_combinations_v3.

Hintergrund: klassifiziert wurde bisher streng sequentiell, ein LLM-Aufruf
nach dem anderen, obwohl die Aufrufe fuer verschiedene Kombinationen
voneinander unabhaengig sind. Ein Benchmark gegen die echte lokale
Ollama-Instanz (2026-08-02, qwen2.5:7b-instruct, keine dedizierte GPU) zeigte
einen Faktor ~3.7x bei 2 gleichzeitigen Aufrufen gegenueber sequentiell, aber
KEINEN weiteren Gewinn bei 4 (Hardware saettigt sich) - daher
DEFAULT_MAX_WORKERS=2 statt eines beliebig hoch gegriffenen Werts.

Ausfuehren (aus prototype/, mit aktivierter .venv):
    PYTHONPATH=src python tests/test_classify_generic_v3.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from classify_generic_v3 import classify_combinations_v3


class _FakeConcurrencyProbeClient:
    """Simuliert einen langsamen LLM-Aufruf (0.3s) und zeichnet auf, wie
    viele Aufrufe tatsaechlich gleichzeitig liefen - so laesst sich echte
    Parallelitaet nachweisen, ohne einen echten (langsamen, kostenpflichtigen
    oder umgebungsabhaengigen) LLM-Aufruf zu brauchen."""

    def __init__(self, delay=0.3):
        self.delay = delay
        self.lock = threading.Lock()
        self.concurrent_now = 0
        self.max_concurrent_seen = 0

    def complete_json(self, prompt):
        with self.lock:
            self.concurrent_now += 1
            self.max_concurrent_seen = max(self.max_concurrent_seen, self.concurrent_now)
        time.sleep(self.delay)
        with self.lock:
            self.concurrent_now -= 1
        if "WERT_A" in prompt:
            category = "A"
        elif "WERT_B" in prompt:
            category = "B"
        else:
            category = "C"
        return f'{{"erkannte_hinweise": "x", "basis": "Pset-Attribute", "category": "{category}"}}'


def test_parallel_classification_overlaps_and_stays_correct():
    combos = {
        (("attr", "WERT_A"),): ["i1"],
        (("attr", "WERT_B"),): ["i2"],
        (("attr", "WERT_C"),): ["i3"],
        # Enthaelt "WERT_A" als Teilstring - prueft, dass die Ergebnis-
        # Zuordnung ueber das future_to_combo-Mapping laeuft (korrekt an den
        # jeweiligen Combo-Key gebunden) statt sich auf Aufrufreihenfolge zu
        # verlassen, die bei paralleler Ausfuehrung nicht deterministisch ist.
        (("attr", "WERT_A_VARIANTE"),): ["i4"],
    }
    progress_calls = []

    client = _FakeConcurrencyProbeClient(delay=0.3)
    t0 = time.monotonic()
    cat, evidence, basis = classify_combinations_v3(
        client, combos, "Test", "Frage?", ["A", "B", "C"],
        on_progress=lambda done, total: progress_calls.append((done, total)),
        max_workers=2,
    )
    dt = time.monotonic() - t0

    assert client.max_concurrent_seen == 2, (
        f"Erwartet echte Parallelitaet von 2 gleichzeitigen Aufrufen, "
        f"gemessen: {client.max_concurrent_seen}"
    )
    # 4 Kombinationen a 0.3s bei max_workers=2 -> zwei Wellen a ~0.3s statt
    # vier sequentiellen (~1.2s) - grosszuegige obere Grenze fuer Jitter.
    assert dt < 0.9, f"Erwartet spuerbar unter 1.2s (sequentiell) durch Parallelitaet, war {dt:.2f}s"

    assert cat[(("attr", "WERT_A"),)] == "A"
    assert cat[(("attr", "WERT_B"),)] == "B"
    assert cat[(("attr", "WERT_C"),)] == "C"
    assert cat[(("attr", "WERT_A_VARIANTE"),)] == "A"

    # on_progress wird trotz nicht-deterministischer Fertigstellungsreihenfolge
    # als stets aufsteigender Zaehler im Hauptthread aufgerufen (siehe
    # Docstring von classify_combinations_v3) - genau EIN Aufruf je fertiger
    # Kombination, in strikt aufsteigender Reihenfolge.
    assert progress_calls == [(1, 4), (2, 4), (3, 4), (4, 4)], progress_calls


def test_single_worker_still_works():
    """max_workers=1 (Randfall) muss weiterhin korrekt (nur eben ohne
    Parallelitaets-Vorteil) funktionieren."""
    combos = {(("attr", "WERT_A"),): ["i1"], (("attr", "WERT_B"),): ["i2"]}
    client = _FakeConcurrencyProbeClient(delay=0.05)
    cat, _, _ = classify_combinations_v3(
        client, combos, "Test", "Frage?", ["A", "B"], max_workers=1,
    )
    assert client.max_concurrent_seen == 1
    assert cat[(("attr", "WERT_A"),)] == "A"
    assert cat[(("attr", "WERT_B"),)] == "B"


if __name__ == "__main__":
    test_parallel_classification_overlaps_and_stays_correct()
    test_single_worker_still_works()
    print("ALLE PRUEFUNGEN ERFOLGREICH")
