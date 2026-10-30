"""The tests run on MARY's shipped defaults, never on your own settings, profile or memory: MARY_USER_DIR points
at an empty folder for the whole run (set before any test module loads the config)."""
import os
import tempfile

import pytest

os.environ["MARY_USER_DIR"] = tempfile.mkdtemp(prefix="mary-test-user-")


def needs_router(make):
    """Build something that loads the router's embedding model (downloaded on first use, 67 MB), or skip the
    test when it isn't there and can't be downloaded (a fresh clone offline)."""
    try:
        return make()
    except ValueError as e:
        if "Could not load model" not in str(e):
            raise
        pytest.skip("the router's embedding model isn't downloaded and couldn't be")
