"""Reflexes (new mode): the few commands done instantly. Everything else must reach the brain whole."""
import pytest

from mary import skills
from mary.reflexes import Reflexes, plain

REFLEXES = Reflexes(skills.reflexes(skills.load()))


def hit(text):
    found = REFLEXES.match(text)
    return (found[0].tool, found[1]) if found else None


@pytest.mark.parametrize("text, tool, args", [
    ("Volume up.", "system_control", {"action": "volume_up", "value": 10}),
    ("Turn it down a bit.", "system_control", {"action": "volume_down", "value": 10}),
    ("Mom, could you turn the volume up, please?", "system_control", {"action": "volume_up", "value": 10}),
    ("turn it up a lot", "system_control", {"action": "volume_up", "value": 25}),
    ("Set the volume to 30.", "system_control", {"action": "volume_set", "value": 30}),
    ("louder", "system_control", {"action": "volume_up", "value": 10}),
    ("Mute.", "system_control", {"action": "mute"}),
    ("unmute the sound", "system_control", {"action": "unmute"}),
    ("Brightness up.", "system_control", {"action": "brightness_up", "value": 10}),
    ("make the screen brighter", "system_control", {"action": "brightness_up", "value": 10}),
    ("dim the screen", "system_control", {"action": "brightness_down", "value": 10}),
    ("brightness to 40", "system_control", {"action": "brightness_set", "value": 40}),
    ("Lock the PC.", "system_control", {"action": "lock"}),
    ("Pause.", "media_control", {"action": "pause"}),
    ("pause the music", "media_control", {"action": "pause"}),
    ("Resume.", "media_control", {"action": "resume"}),
    ("play", "media_control", {"action": "resume"}),
    ("next song", "media_control", {"action": "next"}),
    ("skip this song", "media_control", {"action": "next"}),
    ("previous track", "media_control", {"action": "previous"}),
    ("What time is it?", None, {}),
    ("what's the date today", None, {}),
    ("What day is it?", None, {}),
])
def test_plain_commands_are_instant(text, tool, args):
    assert hit(text) == (tool, args)


@pytest.mark.parametrize("text", [
    "Turn it down and open Spotify.",   # two things: the brain does both
    "pause after this song",
    "play some jazz",
    "What time is it in London?",
    "How are you doing?",
    "Yeah, how are you doing?",
    "volume up and lock the pc",
    "is it too loud in here",
    "what time does the shop close",
    "lock the door",
    "I'm not feeling well. What do you think I should do?",
    "Try and check my bank statement for me.",
    "next to the window",
])
def test_anything_more_goes_to_the_brain(text):
    assert hit(text) is None


def test_polite_wrapping_is_removed():
    assert plain("Hey Mom, could you please pause the music for me?") == "pause the music"
    assert plain("Mute, please.") == "mute"


def test_the_clock_answers_with_the_time_or_the_date():
    reflex, _ = REFLEXES.match("what time is it")
    assert reflex.answer("what time is it").startswith("It's ") and ("AM" in reflex.answer("what time is it")
                                                                    or "PM" in reflex.answer("what time is it"))
    reflex, _ = REFLEXES.match("what's the date today")
    assert any(month in reflex.answer("what's the date today") for month in
               ("January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"))
