import numpy as np
import pytest

from mary.audio.mic import level_of
from mary.audio.stt import prepare, strip_wake_phrase


def _bursts(amplitude):
    """Speech-like: 0.25 s tone bursts with gaps (a constant tone is 'background' to the denoiser)."""
    t = np.arange(32000)
    on = (t // 4000) % 2 == 0
    return np.clip(np.sin(t / 7) * amplitude * on, -32768, 32767).astype(np.int16)


def test_prepare_flags_clipping_and_lifts_quiet_audio():
    assert prepare(_bursts(60000))[1] == "clipping"
    fixed, warning = prepare(_bursts(800))
    assert warning == "quiet" and 5000 < np.abs(fixed).max() <= 32767
    assert prepare(_bursts(12000))[1] == ""


def test_denoise_keeps_speech_and_cuts_steady_noise():
    from mary.audio.stt import denoise

    rng = np.random.default_rng(0)
    speech = _bursts(12000).astype(np.float32)
    noise = rng.normal(0, 1500, len(speech))
    out = denoise(np.clip(speech + noise, -32768, 32767).astype(np.int16)).astype(np.float32)
    gaps = (np.arange(len(speech)) // 4000) % 2 == 1
    assert np.std(out[gaps]) < 0.5 * np.std(noise[gaps])        # background between bursts is reduced
    assert np.std(out[~gaps]) > 0.6 * np.std(speech[~gaps])     # the "speech" survives


@pytest.mark.parametrize("heard, wanted", [
    ("Hey Mary, open Spotify.", "open Spotify."), ("Mary, what time is it?", "what time is it?"),
    ("Hey Mary.", ""), ("Amen.", ""), ("Hail Mary, pause the music", "pause the music"),
    ("Open Notepad.", "Open Notepad."), ("a man called", "a man called"), ("Merry Christmas", "Merry Christmas"),
])
def test_strip_wake_phrase(heard, wanted):
    assert strip_wake_phrase(heard) == wanted
from mary.audio.tts import VoiceOutput, speakable
from mary.config import DEFAULT_CONFIG


def test_speakable():
    assert speakable(r"Opened C:\Users\me\Downloads\Social_Studies_2026_Paper2.pdf **now**") == \
        "Opened Social Studies 2026 Paper2 PDF now"
    assert speakable("See https://www.youtube.com/watch?v=abc for it") == "See youtube.com for it"
    assert speakable("Volume's at 40% 🎵") == "Volume's at 40 percent"


class FakeTTS:
    def __init__(self):
        self.said = []

    def synth(self, text):
        self.said.append(text)
        return np.zeros(240, dtype=np.float32)


class FakeSpeaker:
    playing = False

    def play(self, samples): pass
    def wait(self, timeout=0): pass
    def stop(self): pass


def test_sentences_stream_as_they_complete():
    tts = FakeTTS()
    out = VoiceOutput(tts, FakeSpeaker())
    out.reply_start()
    out.reply_delta("Hello there. How")
    out.wait_done(2)
    assert tts.said == ["Hello there."]          # the first sentence goes out before the reply ends
    out.reply_delta(" are you? I'm")
    out.reply_delta(" fine")
    out.reply_end()
    out.wait_done(2)
    assert tts.said == ["Hello there.", "How are you?", "I'm fine"]


def test_decimals_are_not_sentence_ends():
    tts = FakeTTS()
    out = VoiceOutput(tts, FakeSpeaker())
    out.reply_start()
    out.reply_delta("9.9 is bigger than 9.11. Yes.")
    out.reply_end()
    out.wait_done(2)
    assert tts.said == ["9 point 9 is bigger than 9 point 11.", "Yes."]  # said as "point", split only at the end


def test_level_of():
    assert level_of(np.zeros(1280, dtype=np.int16)) == 0.0
    loud = (np.sin(np.arange(1280) / 5) * 20000).astype(np.int16)
    assert 0.8 < level_of(loud) <= 1.0


@pytest.mark.parametrize("text, spoken", [
    ("It is 19:04.", "It is 7 oh four PM."),
    ("It costs $1,000,000.", "It costs 1,000,000 dollars."),
    ("It costs $4.99.", "It costs 4 dollars 99."),
    ("It's 28°C, feels like 32°C.", "It's 28 degrees, feels like 32 degrees."),
    ("On 2026-10-26.", "On 26 October 2026."),
    ("Opening summer-promo-30s.mp4.", "Opening summer promo 30 second video."),
    ("e.g. this vs that", "for example this versus that"),
    ("Step 1/3", "Step 1 of 3"),
    ("Follow-ups take 4–7 s and use 2.2GB.", "Follow-ups take 4 to 7 seconds and use 2 point 2 gigabytes."),
    ("music from the 1990s", "music from the nineteen nineties"),
    ("version 2.5.1", "version 2.5.1"),
])
def test_speakable_reads_like_a_person(text, spoken):
    """Each case is something the voice got wrong when checked by its phonemes ("dollar one million")."""
    assert speakable(text) == spoken


def test_long_answers_are_spoken_in_full():
    """He may not be looking at the screen: nothing is cut off ("The rest is on screen" is gone)."""
    tts = FakeTTS()
    out = VoiceOutput(tts, FakeSpeaker())
    out.user_text("what's new on the page?")
    out.say("One. Two. Three. Four. Five. Six.")
    out.wait_done(2)
    assert tts.said == ["One.", "Two.", "Three.", "Four.", "Five.", "Six."]


def test_first_clause_is_spoken_before_the_sentence_ends():
    tts = FakeTTS()
    out = VoiceOutput(tts, FakeSpeaker())
    out.reply_start()
    out.reply_delta("Yes, you should take an umbrella, because ")
    out.wait_done(2)
    assert tts.said == ["Yes, you should take an umbrella,"]  # already speaking; the rest follows
    out.reply_delta("there's patchy rain nearby.")
    out.reply_end()
    out.wait_done(2)
    assert tts.said[-1] == "because there's patchy rain nearby."


def test_wake_word_switch_goes_to_your_settings(tmp_path, monkeypatch):
    from mary import persona as personas
    from mary.config import load_config
    from mary.wakeword_training.train import good_enough, set_enabled

    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    personas._load.cache_clear()
    before = DEFAULT_CONFIG.read_bytes()
    set_enabled(True)
    assert load_config().wakeword.enabled is True
    set_enabled(False)  # the default: the line goes again
    assert load_config().wakeword.enabled is False and "wakeword" not in (tmp_path / "config.yaml").read_text()
    assert DEFAULT_CONFIG.read_bytes() == before
    assert good_enough({"recall_your_voice_heldout": 0.9, "false_triggers_per_hour_heldout": 0.3})[0]
    assert not good_enough({"recall_your_voice_heldout": None})[0]
    assert not good_enough({"recall_your_voice_heldout": 0.6, "false_triggers_per_hour_heldout": 0.1})[0]


def test_wake_phrases_are_stripped():
    from tests.conftest import mom_persona

    mom = mom_persona()  # the persona's wake phrases decide
    assert strip_wake_phrase("Hey Mom, open Spotify", mom) == "open Spotify"
    assert strip_wake_phrase("Hey mum.", mom) == ""
    assert strip_wake_phrase("Mum's birthday is on Friday", mom) == "Mum's birthday is on Friday"
    assert strip_wake_phrase("Hey Mom, open Spotify") == "Hey Mom, open Spotify"  # not the default persona's
