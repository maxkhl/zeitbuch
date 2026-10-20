import html
import logging
import re

log = logging.getLogger(__name__)

DETAIL_MAX = 300


class BackendError(Exception):
    """Fehler beim Zugriff auf YouTrack oder Dolibarr; Text geht so in die UI."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def response_detail(r, json_message):
    """Fehlertext einer HTTP-Antwort: Meldung aus dem JSON, sonst der gekürzte
    Antworttext (z. B. ein PHP Fatal als HTML). Leer, wenn nichts da ist."""
    try:
        data = r.json()
    except ValueError:
        data = None
    if data is not None:
        try:
            msg = json_message(data)
        except (AttributeError, TypeError, KeyError):
            msg = None
        if msg:
            return str(msg)
    text = getattr(r, "text", "") or ""
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
    text = re.sub(r" ([:;,.])", r"\1", text)  # Rest der entfernten Tags, z. B. „<b>Fatal error</b>:“
    return text[:DETAIL_MAX] + ("…" if len(text) > DETAIL_MAX else "")


def create_youtrack(cfg):
    from .youtrack import MockYouTrack, YouTrack

    if cfg.youtrack_real:
        return YouTrack(cfg.youtrack_url, cfg.youtrack_token, cfg.http_timeout)
    log.warning("MOCK-MODUS: YouTrack wird simuliert (ZEITBUCH_YOUTRACK_URL/-TOKEN fehlen "
                "oder ZEITBUCH_BACKEND=mock)")
    return MockYouTrack()



def create_dolibarr(cfg):
    from .dolibarr import Dolibarr, MockDolibarr

    if cfg.dolibarr_real:
        return Dolibarr(cfg.dolibarr_url, cfg.dolibarr_key, cfg.http_timeout)
    log.warning("MOCK-MODUS: Dolibarr wird simuliert (ZEITBUCH_DOLIBARR_URL/-KEY fehlen "
                "oder ZEITBUCH_BACKEND=mock)")
    return MockDolibarr()
