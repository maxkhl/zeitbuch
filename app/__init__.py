"""zeitbuch – Zeiterfassung über YouTrack → Zeitdaten für externe Systeme und Dolibarr."""

import os

__version__ = os.environ.get("ZEITBUCH_VERSION", "dev")
