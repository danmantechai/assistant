import pytest

from mary.hotkey import parse_combo


@pytest.mark.parametrize("combo, expected", [
    ("ctrl+space", (0x2, 0x20)),
    ("Ctrl+Alt+M", (0x3, ord("M"))),
    ("win+shift+f9", (0xC, 0x78)),
    ("alt-`", (0x1, 0xC0)),
])
def test_parse_combo(combo, expected):
    assert parse_combo(combo) == expected


@pytest.mark.parametrize("combo", ["ctrl", "ctrl+banana", ""])
def test_parse_combo_rejects(combo):
    with pytest.raises(ValueError):
        parse_combo(combo)
