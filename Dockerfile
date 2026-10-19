FROM debian:trixie-slim

# apt statt pip: Pakete kommen aus Debian trixie (Python 3.13).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-flask python3-waitress python3-requests ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Unprivilegierter Benutzer. /data gehört ihm, damit ein benanntes Volume beim
# ersten Start diese Rechte übernimmt.
RUN groupadd --gid 10001 zeitbuch \
    && useradd --uid 10001 --gid 10001 --home-dir /nonexistent \
       --no-create-home --shell /usr/sbin/nologin zeitbuch \
    && mkdir /data && chown zeitbuch:zeitbuch /data

WORKDIR /app
COPY app/ /app/app/
# Anonymisierte Beispieldaten für den Mock-Modus
COPY testdata/ /app/testdata/

ARG VERSION=dev
ENV ZEITBUCH_VERSION=${VERSION} \
    ZEITBUCH_PORT=8000 \
    ZEITBUCH_DB=/data/zeitbuch.db \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER zeitbuch
EXPOSE 8000
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('ZEITBUCH_PORT','8000'), timeout=3)" || exit 1

# Genau ein Prozess (waitress mit Threads).
CMD ["python3", "-m", "app"]
