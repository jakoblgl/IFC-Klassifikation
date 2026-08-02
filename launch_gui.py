"""
Startet die Streamlit-Oberflaeche als lokalen Server und oeffnet sie in einem
eigenen, nativen Fenster (pywebview) statt im normalen Browser - es gibt also
keine sichtbare Adressleiste. Der Server laeuft ausschliesslich auf
127.0.0.1; es besteht keine Verbindung nach aussen.
"""
import atexit
import subprocess
import sys
import time
import urllib.request

import webview

PORT = 8501
URL = f"http://127.0.0.1:{PORT}"


def wait_until_ready(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    proc = subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "src/gui_app.py",
        "--server.headless", "true",
        "--server.port", str(PORT),
        "--server.address", "127.0.0.1",
        "--browser.gatherUsageStats", "false",
    ])
    atexit.register(proc.terminate)

    if not wait_until_ready():
        print("Server ist nicht rechtzeitig gestartet. Bitte Konsolen-Ausgabe pruefen.")
        proc.terminate()
        return

    webview.create_window("IFC-Attribut-Klassifikation", URL, width=1200, height=850)
    webview.start()

    proc.terminate()


if __name__ == "__main__":
    main()
