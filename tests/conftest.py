import pytest

from app.config import Config
from app.web import create_app


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
def app(cfg):
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()
