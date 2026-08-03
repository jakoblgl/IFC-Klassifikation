"""
Austauschbares LLM-Client-Interface fuer die Schema-Mapping-Pipeline.

Zwei Backends:
- OllamaClient: lokal, kostenlos, self-hosted (Datenschutz-Anforderung aus
  Interview 1).
- ClaudeClient: Cloud-API, schneller, hoehere Qualitaet erwartbar.

Beide implementieren dieselbe Methode complete_json(prompt) -> str, damit
Lokalisierung/Normalisierung backend-unabhaengig bleiben.
"""
import json
import os
import time
import urllib.request


class OllamaClient:
    # Wie viele Kombinationen classify_combinations_v3 gleichzeitig an dieses
    # Backend schickt (siehe dortiger Aufruf in gui_app.py). Empirisch
    # ermittelt (2026-08-02, Testsystem ohne dedizierte GPU, 8 CPU-Kerne):
    # 2 gleichzeitige Aufrufe brachten einen Faktor von ~3.7x gegenueber
    # sequentiell, 4 brachten GEGENUEBER 2 keinen weiteren Gewinn mehr (die
    # Hardware war dann bereits ausgelastet). Dieser Wert ist NICHT
    # allgemeingueltig, sondern maschinenspezifisch - bei staerkerer lokaler
    # Hardware (v.a. mit dedizierter GPU) kann ein hoeherer Wert schneller
    # sein, muss aber genauso empirisch neu ermittelt werden (sequentiell
    # vs. parallel mit unterschiedlichen Werten timen), nicht geraten
    # werden - zu hoch gewaehlt kann bei begrenztem Arbeitsspeicher/VRAM den
    # Rechner ausbremsen statt beschleunigen.
    RECOMMENDED_MAX_WORKERS = 2

    def __init__(self, model="qwen2.5:7b-instruct", host="http://localhost:11434", num_ctx=16384):
        self.model = model
        self.host = host
        self.num_ctx = num_ctx

    def complete_json(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["response"]


class ClaudeClient:
    # Bei Ollama entfaellt das (lokal, kein Limit) - die Cloud-API kann bei der
    # sequentiellen Aufruf-pro-Kombination-Schleife (siehe classify_generic_v3)
    # durchaus an Rate-Limits stossen; ohne Retry wuerde ein einzelner 429/5xx-
    # Fehler mitten in einem Anwendungsfall alle bereits erfolgreich
    # klassifizierten Kombinationen dieses Laufs verwerfen (siehe gui_app.py,
    # der try/except um classify_combinations_v3).
    MAX_RETRIES = 5

    # Wie bei OllamaClient.RECOMMENDED_MAX_WORKERS, aber noch NICHT
    # empirisch getestet - anders als bei lokaler Hardware gibt es hier kein
    # Ressourcen-Erschoepfungsrisiko (die Last liegt bei Anthropics Servern,
    # nicht auf diesem Rechner; ein zu hoher Wert fuehrt hoechstens zu
    # 429-Fehlern, die MAX_RETRIES oben bereits abfaengt) - ein hoeherer
    # Wert als bei Ollama ist daher plausibel, aber ohne echten Test mit
    # einem eigenen API-Key/Tier reine Vermutung. Bei Bedarf genau wie beim
    # Ollama-Benchmark testen (sequentiell vs. parallel mit z.B. 2/4/8/16
    # Workern timen) und den ermittelten Wert hier eintragen.
    RECOMMENDED_MAX_WORKERS = 2

    def __init__(self, model="claude-sonnet-5", api_key=None):
        import anthropic
        self._anthropic = anthropic
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY ist nicht gesetzt.")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete_json(self, prompt: str) -> str:
        retryable = (
            self._anthropic.RateLimitError,
            self._anthropic.APIConnectionError,
            self._anthropic.APITimeoutError,
            self._anthropic.InternalServerError,
        )
        for attempt in range(self.MAX_RETRIES):
            try:
                msg = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    temperature=0,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text
            except retryable:
                if attempt == self.MAX_RETRIES - 1:
                    raise
                time.sleep(2 ** attempt)  # 1, 2, 4, 8s


def get_client(backend="ollama", api_key=None):
    if backend == "ollama":
        return OllamaClient()
    elif backend == "claude":
        return ClaudeClient(api_key=api_key)
    raise ValueError(f"Unbekanntes Backend: {backend}")
