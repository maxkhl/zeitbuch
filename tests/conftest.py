import pytest

from app.backends.youtrack import MockYouTrack
from app.config import Config
from app.web import create_app

MONTH = "2026-09"


def make_config(tmp_path, **overrides):
    values = dict(backend="mock", db=str(tmp_path / "test.db"))
    values.update(overrides)
    cfg = Config(**values)
    cfg.validate()
    return cfg


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
def youtrack():
    return MockYouTrack(shift_month=False)


@pytest.fixture
def app(cfg, youtrack):
    app = create_app(cfg, youtrack=youtrack)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def db(app):
    return app.extensions["zeitbuch"]["db"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mapped(db):
    db.set_project("CON", 1)
    db.set_project("XSYS", None)
    return db
