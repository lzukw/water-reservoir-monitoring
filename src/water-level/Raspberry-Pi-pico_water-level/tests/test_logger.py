import pytest

from logger import Logger


def test_level_filtering(capsys):
    log = Logger("T", level="INFO")
    log.debug("hidden debug")
    log.info("shown info")
    log.warning("shown warning")
    log.error("shown error")
    log.fatal("shown fatal")
    out = capsys.readouterr().out
    assert "hidden debug" not in out
    assert "shown info" in out
    assert "shown warning" in out
    assert "shown error" in out
    assert "shown fatal" in out
    assert "T" in out  # logger name is part of the line


def test_debug_level_shows_everything(capsys):
    log = Logger("T", level="DEBUG")
    log.debug("d")
    out = capsys.readouterr().out
    assert "[DEBUG  ]" in out


def test_case_insensitive_level(capsys):
    log = Logger("T", level="warning")
    log.info("hidden")
    log.error("shown")
    out = capsys.readouterr().out
    assert "hidden" not in out
    assert "shown" in out


def test_set_level(capsys):
    log = Logger("T", level="ERROR")
    log.warning("hidden")
    log.set_level("DEBUG")
    log.debug("shown")
    out = capsys.readouterr().out
    assert "hidden" not in out
    assert "shown" in out


def test_unknown_level_defaults_to_info(capsys):
    log = Logger("T", level="NONSENSE")
    log.debug("hidden")
    log.info("shown")
    out = capsys.readouterr().out
    assert "hidden" not in out
    assert "shown" in out
