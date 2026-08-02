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
