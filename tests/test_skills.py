import pytest

from mary.ocr import OcrLine
from mary.router.guards import is_multi_part, split_clauses
from mary.skills.messages import parse_chat_rows, unread_from_title
from mary.tools.safety import ground_args, ungrounded_reason


# ---------------------------------------------------------------- intent grounding

@pytest.mark.parametrize("tool, args, words, allowed", [
    ("close_app", {"name": "edge"}, "can you open my browser and open whatsapp web", False),
    ("close_app", {"name": "edge"}, "close edge", True),
    ("close_app", {"name": "spotify"}, "quit spotify please", True),
    ("close_app", {"name": "edge", "force": True}, "close edge", False),
    ("close_app", {"name": "edge", "force": True}, "force close edge, it's frozen", True),
    ("power", {"action": "shutdown"}, "what's my battery at", False),
    ("power", {"action": "shutdown"}, "shut down the pc", True),
    ("power", {"action": "restart"}, "reboot", True),
    ("file_action", {"action": "recycle", "path": "1"}, "open the first one", False),
    ("file_action", {"action": "recycle", "path": "1"}, "delete the first one", True),
    ("file_action", {"action": "move", "path": "1"}, "move it to documents", True),
    ("file_action", {"action": "open", "path": "1"}, "open the first one", True),
    ("open_app", {"name": "spotify"}, "play some music", True),  # non-destructive tools aren't checked
])
def test_grounding(tool, args, words, allowed):
    assert (ungrounded_reason(tool, args, words) is None) is allowed


def test_unasked_force_becomes_normal_close():
    assert ground_args("close_app", {"name": "edge", "force": True}, "close whatsapp") == {"name": "edge"}
    assert ground_args("close_app", {"name": "edge", "force": True}, "kill edge")["force"] is True


@pytest.mark.parametrize("text, expected", [
    ("open spotify", False),
    ("play me some country side music", False),
    ("open my browser and open whatsapp web and see if i have any message", True),
    ("open notepad then type hello", True),
    ("check if I have new messages", True),
])
def test_is_multi_part(text, expected):
    assert is_multi_part(text) is expected


@pytest.mark.parametrize("text, parts", [
    ("can you open my browser and open whatsapp web and see if i have any message",
     ["can you open my browser", "open whatsapp web", "see if i have any message"]),
    ("open notepad and then set a timer for 10 seconds", ["open notepad", "set a timer for 10 seconds"]),
    ("find the PDF I downloaded and open it", ["find the PDF I downloaded", "open it"]),
    ("play some jazz, then turn it up", ["play some jazz", "turn it up"]),
    ("play rock and roll", ["play rock and roll"]),
    ("set a timer for a minute and a half", ["set a timer for a minute and a half"]),
    ("search for salt and pepper chicken", ["search for salt and pepper chicken"]),
    ("turn the volume down a bit", ["turn the volume down a bit"]),
    ("how much battery do I have and what is the time right now",
     ["how much battery do I have", "what is the time right now"]),
    ("what time is it and how's my battery", ["what time is it", "how's my battery"]),
])
def test_split_clauses(text, parts):
    assert split_clauses(text) == parts


# ---------------------------------------------------------------- WhatsApp parsing

@pytest.mark.parametrize("title, count", [
    ("(31) WhatsApp and 2 more pages - Personal - Microsoft Edge", 31),
    ("WhatsApp - Personal - Microsoft Edge", 0),
    ("Google - Microsoft Edge", None),
])
def test_unread_from_title(title, count):
    assert unread_from_title(title) == count


def _line(words, y, h=16):
    """OCR line from [(text, x), ...]; each word is ~9 px per character wide."""
    boxes = [(t, x, x + 9 * len(t)) for t, x in words]
    return OcrLine(" ".join(w[0] for w in boxes), boxes[0][1], y, boxes[-1][2] - boxes[0][1], h, boxes)


def test_parse_chat_rows():
    lines = [
        _line([("Chats", 80)], 60),
        _line([("Search", 90)], 110),
        _line([("All", 80), ("Unread", 130), ("Favourites", 210), ("Groups", 320)], 150),
        # row 1: name + time on one OCR line, preview + badge on the next
        _line([("Alice", 150), ("Smith", 200), ("10:42", 560)], 200),
        _line([("See", 150), ("you", 185), ("at", 220), ("5", 245), ("3", 575)], 222),
        # row 2: no badge (already read)
        _line([("Book", 150), ("club", 195)], 270),
        _line([("Yesterday", 515)], 270),
        _line([("Chapter", 150), ("four", 220), ("is", 265), ("great", 285)], 292),
        # row 3: group with a big badge
        _line([("Work", 150), ("team", 198), ("Monday", 542)], 340),
        _line([("Bob:", 150), ("deadline", 195), ("moved", 275), ("12", 566)], 362),
        # the open conversation on the right also shows times; they are not chat rows
        _line([("Hello", 800), ("there", 855)], 300),
        _line([("09:15", 1200)], 318),
    ]
    rows = parse_chat_rows(lines)
    assert [(r.name, r.time, r.unread) for r in rows] == [
        ("Alice Smith", "10:42", 3), ("Book club", "Yesterday", 0), ("Work team", "Monday", 12)]
    assert rows[0].preview == "See you at 5"
    assert rows[1].preview == "Chapter four is great"
