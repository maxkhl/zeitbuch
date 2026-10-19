import logging

log = logging.getLogger(__name__)


class BackendError(Exception):
    """Fehler beim Zugriff auf YouTrack oder Dolibarr; Text geht so in die UI."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def create_youtrack(cfg):
    from .youtrack import MockYouTrack, YouTrack

    if cfg.youtrack_real:
        return YouTrack(cfg.youtrack_url, cfg.youtrack_token, cfg.http_timeout)
    log.warning("MOCK-MODUS: YouTrack wird simuliert (ZEITBUCH_YOUTRACK_URL/-TOKEN fehlen "
                "oder ZEITBUCH_BACKEND=mock)")
    return MockYouTrack()

