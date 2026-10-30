"""Brick: pause, resume or skip whatever is already playing (media_control), via the media keys."""
from __future__ import annotations

from mary import winutil
from mary.reflexes import Reflex
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = "Pause, resume or skip what's already playing: media_control. To play something specific, use play_music."
GROUNDING = [
    ("media_control", lambda a: True,
     r"\b(play|playing|pause|resume|unpause|stop|skip|next|previous|back|last|song|track|music|continue|hold)\b",
     "the user didn't ask about what's playing"),
]

_WHAT = r"(?: (?:the |this |that )?(?:music|song|video|track|playback|it|spotify))?"
REFLEXES = [
    # "stop" pauses: a paused song can be resumed, a stopped one can't on every player.
    Reflex(r"(?:pause|stop|hold)" + _WHAT, "media_control", lambda text: {"action": "pause"}),
    Reflex(r"(?:resume|unpause|continue)" + _WHAT + r"(?: playing)?|play(?: it)?(?: again)?|keep playing",
           "media_control", lambda text: {"action": "resume"}),
    Reflex(r"(?:next|skip)(?: (?:this |the )?(?:song|track|one|it))?|play the next (?:song|track|one)",
           "media_control", lambda text: {"action": "next"}),
    Reflex(r"(?:previous|last)(?: (?:song|track|one))?|go back(?: a (?:song|track))?|play the previous (?:song|track|one)",
           "media_control", lambda text: {"action": "previous"}),
]

MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
MEDIA_ACTIONS = ["pause", "resume", "next", "previous", "stop", "play_pause"]


def _spotify_playing() -> bool | None:
    """True/False from Spotify's window title ('Artist - Song' vs 'Spotify Premium'); None if not open."""
    idle = {"spotify", "spotify premium", "spotify free"}
    for w in winutil.list_windows():
        if w.exe == "spotify.exe":
            return w.title.strip().lower() not in idle
    return None


def tools(ctx: ToolContext) -> list[Tool]:
    def media_control(action: str) -> ToolResult:
        action = {"play": "resume", "unpause": "resume", "skip": "next", "prev": "previous",
                  "back": "previous"}.get(action, action)
        if action not in MEDIA_ACTIONS:
            return ToolResult(False, f"Unknown media action '{action}'. Use one of {MEDIA_ACTIONS}.")
        if action in ("pause", "resume"):
            # The media key only toggles, so "pause" could resume. Spotify's title says whether it plays.
            playing = _spotify_playing()
            if playing is None or playing == (action == "pause"):  # unknown, or a toggle does the job
                winutil.tap_vk(MEDIA_KEYS["play_pause"], extended=True)
                return ToolResult(True, f"{action.capitalize()}d.", say="Paused." if action == "pause" else "Resuming.")
            return ToolResult(True, "Nothing was playing." if action == "pause" else "Already playing.",
                              say="Nothing's playing." if action == "pause" else "It's already playing.")
        winutil.tap_vk(MEDIA_KEYS[action], extended=True)
        say = {"play_pause": "Done.", "next": "Skipping ahead.", "previous": "Going back one.", "stop": "Stopped."}
        return ToolResult(True, f"Sent media key {action}.", say=say[action])

    return [
        Tool("media_control", "Pause, resume or skip whatever is already playing. To play something "
                              "specific, use play_music instead.",
             {"action": {"type": "string", "enum": MEDIA_ACTIONS}},
             ["action"], media_control, speaks_for_itself=True, announce=lambda a: f"Media: {a.get('action', '')}…"),
    ]
