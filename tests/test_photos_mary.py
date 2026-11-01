"""The photo worker inside MARY: it exists when photos and memory are on, steps aside while she answers, and is off when switched off."""
import pytest

from mary import memory, photos
from mary.config import Section, load_config
from mary.output import Output


class Quiet(Output):
    def say(self, text):
        pass


@pytest.fixture
def mary(tmp_path, monkeypatch):
    from mary.core import Mary

    monkeypatch.setattr(memory, "ENABLED", True)
    monkeypatch.setattr(photos, "ENABLED", True)
    monkeypatch.setattr(memory, "FOLDER", tmp_path / "memory")
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    cfg = load_config()
    cfg["brain"] = Section.wrap({"provider": "ollama", "mode": "new"})
    m = Mary(cfg, Quiet())
    yield m
    m.ingest.stop()
    m.memory.close()


def test_she_has_a_photo_library_and_a_worker_that_does_nothing_until_a_folder_is_added(mary):
    assert mary.photos is not None and mary.ingest is not None and mary.photos.folders() == []
    mary.start_photos()
    assert mary.ingest._thread.is_alive() and mary.photos.status()["photos"] == 0


def test_the_worker_steps_aside_while_she_answers_a_request(mary, monkeypatch):
    seen = []
    assert mary.ingest.busy() is False
    monkeypatch.setattr(mary.agent, "run", lambda text, **kw: (seen.append(mary.ingest.busy()), "Okay.")[1])
    monkeypatch.setattr(mary.agent, "prefill_in_background", lambda: None)
    mary.handle("tell me a joke")
    assert seen == [True] and mary.ingest.busy() is False         # busy during the request, free after it (even if it fails)

    def boom(text, **kw):
        seen.append(mary.ingest.busy())
        raise RuntimeError("the model fell over")

    monkeypatch.setattr(mary.agent, "run", boom)
    mary.handle("and another")
    assert seen[-1] is True and mary.ingest.busy() is False


def test_with_photos_switched_off_there_is_no_library_and_no_worker(tmp_path, monkeypatch):
    from mary.core import Mary

    monkeypatch.setattr(memory, "ENABLED", True)
    monkeypatch.setattr(photos, "ENABLED", False)
    monkeypatch.setattr(memory, "FOLDER", tmp_path / "memory")
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path / "user"))
    m = Mary(load_config(), Quiet())
    assert m.photos is None and m.ingest is None
    m.start_photos()      # nothing to start, and nothing breaks
    m.memory.close()
