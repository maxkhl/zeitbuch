import pytest

from app.config import Config, FieldConfig


def test_defaults_aus_konzept(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("ZEITBUCH_"):
            monkeypatch.delenv(key)
    cfg = Config.from_env()
    assert cfg.port == 8000
    assert cfg.db == "/data/zeitbuch.db"
    assert cfg.yt == FieldConfig()
    assert cfg.yt.field_type == "Type"
    assert cfg.yt.work_nonbillable == "Nicht abrechenbar"
    assert cfg.yt.authors == ()
    # Ohne URLs läuft auto im Mock
    assert not cfg.youtrack_real and not cfg.dolibarr_real


def test_feldkonfiguration_aus_env(monkeypatch):
    monkeypatch.setenv("ZEITBUCH_YT_FIELD_TYPE", "Typ")
    monkeypatch.setenv("ZEITBUCH_YT_VALUE_YES", "Yes")
    monkeypatch.setenv("ZEITBUCH_YT_AUTHORS", " max , claude,")
    yt = FieldConfig.from_env()
    assert yt.field_type == "Typ"
    assert yt.value_yes == "Yes"
    assert yt.authors == ("max", "claude")


def test_auto_waehlt_je_system(monkeypatch):
    cfg = Config(youtrack_url="https://yt.example", youtrack_token="t")
    cfg.validate()
    assert cfg.youtrack_real and not cfg.dolibarr_real


def test_real_verlangt_alle_zugaenge():
    with pytest.raises(ValueError, match="ZEITBUCH_DOLIBARR_KEY"):
        Config(backend="real", youtrack_url="https://yt.example", youtrack_token="t",
               dolibarr_url="https://doli.example").validate()


@pytest.mark.parametrize("kwargs, msg", [
    ({"backend": "x"}, "ZEITBUCH_BACKEND"),
    ({"port": 0}, "ZEITBUCH_PORT"),
    ({"youtrack_url": "ftp://x"}, "ZEITBUCH_YOUTRACK_URL"),
    ({"yt": FieldConfig(field_type="")}, "ZEITBUCH_YT_FIELD_TYPE"),
])
def test_ungueltige_werte(kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        Config(**kwargs).validate()


def test_ganze_zahl(monkeypatch):
    monkeypatch.setenv("ZEITBUCH_PORT", "abc")
    with pytest.raises(ValueError, match="ganze Zahl"):
        Config.from_env()
