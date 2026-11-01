"""The tests run on MARY's shipped defaults, never on your own settings, profile or memory: MARY_USER_DIR points
at an empty folder for the whole run (set before any test module loads the config). Nor do they watch your real
desktop: the feedback loop (mary/ready.py) sees a desktop where nothing changes, unless a test brings its own.
Nor do they start MARY's own browser: browser tests start their own, invisible, with a throwaway profile."""
import os
import tempfile

import pytest

from mary import memory, photos, ready, web

os.environ["MARY_USER_DIR"] = tempfile.mkdtemp(prefix="mary-test-user-")
ready.DESK = ready.StillDesk
ready.WRITE_LOG = False
web.ENABLED = False   # never MARY's real browser (browser tests start their own, invisible)
memory.ENABLED = False   # no memory store in the other tests (memory tests make their own, in a scratch folder)
photos.ENABLED = False   # and no photo worker


def mom_persona():
    """A persona woken by "Hey Mom", like the author's own (which is private): for the wake-phrase tests."""
    from mary.persona import Persona, Wake

    return Persona("mom", None, nicknames=["Mom", "Mum"], wake=[
        Wake("Hey Mom", "hey_mom", names=["mom", "mum", "mommy", "mummy", "mam", "ma"],
             alone=["mom", "mum", "mommy", "mummy"]),
        Wake("Hey Mary", "hey_mary", names=["mary", "marie", "mari", "mery"], alone=["mary", "amen"],
             misheard=["hate mary", "hail mary", "amen"], bare=True)])


def needs_router(make):
    """Build something that loads the router's embedding model (downloaded on first use, 67 MB), or skip the
    test when it isn't there and can't be downloaded (a fresh clone offline)."""
    try:
        return make()
    except ValueError as e:
        if "Could not load model" not in str(e):
            raise
        pytest.skip("the router's embedding model isn't downloaded and couldn't be")
