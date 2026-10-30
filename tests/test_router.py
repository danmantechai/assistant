"""Router pieces that don't need the embedding model: level parsing, guards, slot extraction.
(The matching itself is checked with real phrasings by `python -m mary.calibrate`.)"""
import re

import pytest

from mary.app_index import App, AppMatch
from mary.config import load_config
from mary.parsers import parse_level
from mary.router.guards import guard_hits
from mary.router.router import normalize
from mary.router.slots import SlotFiller, remainder


@pytest.mark.parametrize("text, expected", [
    ("turn the volume down a bit", ("down", 10)),
    ("turn it up", ("up", 10)),
    ("turn it up a lot", ("up", 25)),
    ("volume up by 20", ("up", 20)),
    ("set volume to 30", ("set", 30)),
    ("volume 50 percent", ("set", 50)),
    ("put the volume at half", ("set", 50)),
    ("max volume", ("set", 100)),
    ("turn the volume all the way down", ("set", 0)),
    ("mute", ("mute", None)),
    ("unmute the sound", ("unmute", None)),
    ("it's too loud", ("down", 10)),
    ("I can't hear it, turn it up", ("up", 10)),
    ("dim the screen", ("down", 10)),
    ("make the screen brighter", ("up", 10)),
    ("set brightness to seventy", ("set", 70)),
    ("volume", None),
])
def test_parse_level(text, expected):
    assert parse_level(text) == expected


@pytest.mark.parametrize("text, guards", [
    ("open spotify", []),
    ("don't open spotify", ["negation"]),
    ("open notepad", []),                        # "not" inside a word doesn't count
    ("why is my laptop slow", ["reasoning"]),
    ("how do I take a screenshot", ["reasoning"]),
    ("do that again", ["reference"]),
    ("open the other one", ["reference"]),
    ("close it", ["reference"]),
    ("turn it up", []),
    ("open spotify then", ["compound"]),
    ("never mind", ["negation"]),
])
def test_guards(text, guards):
    assert guard_hits(text) == guards


def test_normalize():
    assert normalize("Hey Mary, could you open Spotify please?") == "open spotify"


class FakeApps:
    """Just enough of AppIndex for slot extraction."""
    known = {"spotify": "Spotify", "notepad": "Notepad", "calculator": "Calculator", "edge": "Microsoft Edge",
             "browser": "Microsoft Edge", "discord": "Discord"}

    def match(self, name):
        hit = self.known.get(name.lower().strip())
        return AppMatch(App(hit, exe=f"{hit.lower()}.exe") if hit else None, 100 if hit else 30, [])

    @staticmethod
    def normalize(name):
        return name.lower()


@pytest.fixture(scope="module")
def slots():
    return SlotFiller(load_config(), FakeApps())


OPEN_STRIP = [r"\bopen( up)?\b", r"\blaunch\b", r"\bstart( up)?\b", r"\bfire up\b", r"\bget\b", r"\bgoing\b",
              r"\bthe\b", r"\bmy\b", r"\bapp\b"]
PLAY_STRIP = [r"\bplay\b", r"\bput on\b", r"\bsome\b", r"\bfor me\b", r"\bme\b"]


@pytest.mark.parametrize("text, name", [
    ("open spotify", "spotify"), ("get notepad going", "notepad"), ("could you launch the calculator app", "calculator"),
    ("open my browser", "browser"),
])
def test_app_slot(slots, text, name):
    slot = slots.app(text, OPEN_STRIP)
    assert slot.ok and slot.args == {"name": name}


def test_app_slot_unknown(slots):
    assert not slots.app("open frobnicator", OPEN_STRIP).ok


def test_site_slot(slots):
    strip = [r"\bopen\b", r"\bgo to\b", r"\bthe\b", r"\bwebsite\b"]
    assert slots.site("open youtube", strip).args == {"action": "open_url", "value": "youtube"}
    assert slots.site("go to reddit.com", strip).args == {"action": "open_url", "value": "reddit.com"}
    assert slots.site("open whatsapp web", strip).args["value"] == "whatsapp"
    assert not slots.site("open my homework", strip).ok


@pytest.mark.parametrize("text, args", [
    ("turn the volume down a bit", {"action": "volume_down", "value": 10}),
    ("set volume to 30", {"action": "volume_set", "value": 30}),
    ("mute", {"action": "mute"}),
])
def test_volume_slot(slots, text, args):
    assert slots.volume(text, []).args == args


def test_brightness_cannot_mute(slots):
    assert not slots.brightness("mute the screen", []).ok


@pytest.mark.parametrize("text, action", [
    ("pause the music", "pause"), ("skip to the next track", "next"), ("go back to the previous song", "previous"),
    ("resume", "resume"), ("play", "resume"), ("stop the music", "stop"),
])
def test_media_slot(slots, text, action):
    assert slots.media(text, []).args == {"action": action}


def test_media_slot_rejects_music_requests(slots):
    assert not slots.media("play some jazz", []).ok


@pytest.mark.parametrize("text, args", [
    ("play some country music", {"query": "country music"}),
    ("put on Jim Reeves", {"query": "jim reeves"}),
    ("play some music", {}),
    ("play me something", {}),
])
def test_music_slot(slots, text, args):
    assert slots.music_query(text, PLAY_STRIP).args == args


def test_music_slot_rejects_playback_commands(slots):
    assert not slots.music_query("skip to the next track", PLAY_STRIP).ok


@pytest.mark.parametrize("text, args", [
    ("set a timer for 5 minutes", {"duration": "300 seconds"}),
    ("set a pasta timer for 12 minutes", {"duration": "720 seconds", "label": "pasta"}),
    ("set a 3 minute timer for the tea", {"duration": "180 seconds", "label": "tea"}),
    ("start a 30 second timer", {"duration": "30 seconds"}),
    ("give me a timer for a minute and a half", {"duration": "90 seconds"}),
])
def test_timer_slot(slots, text, args):
    assert slots.timer(text, []).args == args


def test_time_reply(slots):
    assert re.fullmatch(r"It's \d{1,2}:\d{2} [AP]M\.", slots.time_date("what time is it", []).reply)
    assert slots.time_date("what's the date today", []).reply.startswith("It's ")


@pytest.mark.parametrize("text, ok, query", [
    ("search for jollof rice recipe", True, "jollof rice recipe"),
    ("google the weather in paris", True, "the weather in paris"),
    ("jollof rice recipe", False, None),
])
def test_search_slot(slots, text, ok, query):
    strip = [r"\bsearch( the web| google| online)?( for)?\b", r"\bgoogle\b", r"\blook up\b"]
    slot = slots.search_query(text, strip)
    assert slot.ok is ok
    if ok:
        assert slot.args == {"action": "search", "value": query}


FIND_STRIP = [r"\bfind\b", r"\blook for\b", r"\bwhere (is|are)\b", r"\bthe\b", r"\bmy\b", r"\bfiles?\b",
              r"\bi (downloaded|saved)\b", r"\bon\b", r"\bin\b", r"\bfrom\b", r"\bcalled\b"]


@pytest.mark.parametrize("text, args", [
    ("find the PDF I downloaded this week",
     {"file_type": "pdf", "folder": "downloads", "modified_within": "this week"}),
    ("find the excel file I saved yesterday", {"file_type": "excel", "modified_within": "yesterday"}),
    ("look for photos on my desktop", {"file_type": "photo", "folder": "desktop"}),
    ("where is my resume", {"query": "resume"}),
    ("find files called budget in my documents", {"folder": "documents", "query": '"budget"'}),
    # How people actually ask: none of the asking words may become part of the name to search for.
    ("Can you find me a list of files in my downloads folder?", {"folder": "downloads"}),
    ("Check if I have any new file downloaded today.", {"folder": "downloads", "modified_within": "today"}),
    ("Can you give me a list of images that I have downloaded in my download folder?",
     {"folder": "downloads", "file_type": "image"}),
    ("check my download folder to see if I have any pdf file", {"folder": "downloads", "file_type": "pdf"}),
    ("what's in my downloads", {"folder": "downloads"}),
    ("what did I download yesterday", {"folder": "downloads", "modified_within": "yesterday"}),
    ("find the file called list", {"query": '"list"'}),
    ("find the invoice from amazon", {"query": "invoice amazon"}),
])
def test_files_slot(slots, text, args):
    assert slots.files(text, FIND_STRIP).args == args


def test_files_slot_needs_something_about_files(slots):
    assert not slots.files("what did I do today", FIND_STRIP).ok
    assert not slots.files("find stuff for me", FIND_STRIP).ok


def _after_search(slots, files):
    import time
    from types import SimpleNamespace
    filler = SlotFiller.__new__(SlotFiller)
    filler.__dict__.update(slots.__dict__)
    filler.ctx = SimpleNamespace(last_files=files, last_files_at=time.monotonic())
    return filler


@pytest.mark.parametrize("text, n", [
    ("open the newest file", "1"), ("open it", "1"), ("open the second one", "2"), ("open number three", "3"),
    ("open the oldest one", "3"), ("play the video", "1"), ("open that pdf", "2"), ("show me the picture", "3"),
])
def test_found_file_slot(slots, text, n):
    filler = _after_search(slots, [r"C:\x\promo.mp4", r"C:\x\report.pdf", r"C:\x\photo.jpg"])
    assert filler.found_file(text, []).args == {"action": "open", "path": n}


def test_found_file_needs_a_recent_search(slots):
    filler = _after_search(slots, [r"C:\x\promo.mp4"])
    assert not filler.found_file("open the other one", []).ok   # unclear: the LLM decides
    assert not filler.found_file("open the spreadsheet", []).ok  # none in the results
    filler.ctx.last_files_at -= 3600                              # an hour-old search: too old to mean
    assert not filler.found_file("open it", []).ok


def test_folder_slot(slots):
    assert slots.folder("open my download folder", []).args == {"action": "open", "path": "downloads"}


def test_clause_split_ignores_interjections():
    from mary.router.guards import split_clauses
    assert split_clauses("OK, can you open the newest file for me?") == ["can you open the newest file for me?"]
    assert split_clauses("so, um, what's the time") == ["what's the time"]
    assert split_clauses("ok") == ["ok"]


@pytest.mark.parametrize("text, words", [
    ("type hello world", "hello world"),
    ("Write the following text: Daniel is a good boy.", "Daniel is a good boy."),
    ("write the following text and it's Imano is a good boy", "Imano is a good boy"),
    ("type this: See you at five", "See you at five"),
    ("write Happy Birthday in it", "Happy Birthday"),
    ("Type out \"Good morning, everyone\"", "Good morning, everyone"),
    ("write the following text. Daniel is a good boy.", "Daniel is a good boy."),
])
def test_dictation_slot_keeps_case(slots, text, words):
    assert slots.dictation(text, []).args == {"action": "type", "text": words}


def test_dictation_slot_asks_when_empty(slots):
    slot = slots.dictation("write the following text in it", [])
    assert slot.ok and slot.reply == "What should I type?" and slot.fill["await"] == "dictation"


def test_status_part(slots):
    assert slots.status("how's my battery", []).fill["part"] == "Battery"
    assert slots.status("system status", []).fill["part"] is None


def test_remainder():
    assert remainder("Hey Mary, could you please fire up the calculator for me?", OPEN_STRIP) == "calculator"
