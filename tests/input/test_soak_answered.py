"""`soak._check_processes(writer, answered=...)` and `run_soak(..., answered=...)` (WALKFIX Task 10A, QB283 2b).

The key-timing check asks the MapleStory question once and then runs several soaks; each soak takes the text of that one
answer instead of asking again. Only the text `maple_check` returns for a `y` lets a soak go on; any other text is
recorded and refuses the run before the listener, the link or any key. No board, no radio, no window: the link and the
sink are fakes, and `_read_answer` is patched so `input()` is never reached.
"""
import json
import types

import pytest
from returns.result import Failure, Success

from src.input.harness import soak

ANSWERED_Y = "answered y: MapleStory closed"


def _never_ask():
    pytest.fail("the question was asked although an answer was given")


class Link:
    def __init__(self):
        self.events = []

    def take_pongs(self):
        return []

    def faults(self):
        return []

    def connect(self, timeout_s=30.0):
        self.events.append("connect")
        return Failure("no board in this test")

    def close(self):
        self.events.append("close")

    def release_all(self):
        self.events.append("release_all")
        return Failure("not connected")


class Sink:
    def __init__(self):
        self.stopped = False

    def sink_is_foreground(self):
        return True

    def bring_sink_to_foreground(self):
        return True

    def stop(self):
        self.stopped = True


def test_an_answered_y_text_asks_nothing_and_is_recorded(monkeypatch):
    monkeypatch.setattr(soak, "_read_answer", _never_ask)
    writer = types.SimpleNamespace(session={})
    assert soak._check_processes(writer, answered=ANSWERED_Y) is None
    assert writer.session["process_check"] == ANSWERED_Y


@pytest.mark.parametrize("text", ["refused: MapleStory not confirmed closed (answer: n)", "x", "", "answered y: MapleStory closed "])
def test_any_other_text_is_recorded_and_refuses(monkeypatch, text):
    monkeypatch.setattr(soak, "_read_answer", _never_ask)
    writer = types.SimpleNamespace(session={})
    with pytest.raises(soak._Refused) as refused:
        soak._check_processes(writer, answered=text)
    assert str(refused.value) == text
    assert writer.session["process_check"] == text


def test_the_text_that_maple_check_gives_for_y_is_the_text_that_is_accepted(monkeypatch):
    monkeypatch.setattr(soak, "_read_answer", lambda: Success("y"))
    ok, text = soak.maple_check()
    assert ok and text == ANSWERED_Y
    writer = types.SimpleNamespace(session={})
    assert soak._check_processes(writer, answered=text) is None


def test_without_the_keyword_it_asks_as_before(monkeypatch):
    calls = []
    monkeypatch.setattr(soak, "_read_answer", lambda: calls.append(1) or Success("y"))
    writer = types.SimpleNamespace(session={})
    soak._check_processes(writer)
    assert calls == [1] and writer.session["process_check"] == ANSWERED_Y
    monkeypatch.setattr(soak, "_read_answer", lambda: Success("n"))
    with pytest.raises(soak._Refused):
        soak._check_processes(writer)


def test_run_soak_with_a_refused_text_exits_refused_before_the_listener_and_the_link(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(soak, "_read_answer", _never_ask)
    link, started = Link(), []
    cfg = soak.SoakConfig(commands=3, out=tmp_path / "run", seed=1, short=True)
    code = soak.run_soak(cfg, link, lambda run_dir: started.append(run_dir), answered="x")
    assert code == soak.EXIT_REFUSED
    assert started == [] and "connect" not in link.events
    assert json.loads((tmp_path / "run" / "session.json").read_text(encoding="utf-8"))["process_check"] == "x"
    assert "x" in capsys.readouterr().err


def test_run_soak_with_the_y_text_goes_on_without_asking(tmp_path, monkeypatch):
    """It reaches the listener and then `connect` (which fails here, so no command runs); the question is not asked."""
    monkeypatch.setattr(soak, "_read_answer", _never_ask)
    link, sink = Link(), Sink()
    cfg = soak.SoakConfig(commands=3, out=tmp_path / "run", seed=1, short=True)
    code = soak.run_soak(cfg, link, lambda run_dir: sink, answered=ANSWERED_Y)
    assert code == soak.EXIT_ABORT  # connect failed
    assert link.events[0] == "connect" and sink.stopped
    assert json.loads((tmp_path / "run" / "session.json").read_text(encoding="utf-8"))["process_check"] == ANSWERED_Y
