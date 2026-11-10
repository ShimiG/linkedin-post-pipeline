import pytest

from poster import config


def test_shipped_config_loads_with_a_real_timezone(monkeypatch):
    monkeypatch.delenv("TIMEZONE", raising=False)
    monkeypatch.delenv("PUBLISH_MODE", raising=False)
    cfg = config.load_config()
    assert config.today(cfg)  # the zone in config.yml exists


@pytest.mark.parametrize("bad", ["GMT+2", "gmt+2", "UTC+2", "Israel time"])
def test_offset_style_timezone_is_refused_with_a_clear_message(monkeypatch, bad):
    monkeypatch.setenv("TIMEZONE", bad)
    with pytest.raises(SystemExit) as e:
        config.load_config()
    assert "Asia/Jerusalem" in str(e.value) and bad in str(e.value)


def test_repo_variable_overrides_the_file(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "Europe/London")
    assert config.load_config()["timezone"] == "Europe/London"
