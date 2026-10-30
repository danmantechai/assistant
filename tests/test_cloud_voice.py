"""Her ElevenLabs voice (mary/audio/cloud_tts.py) with ElevenLabs and Kokoro faked: no speech is made."""
from types import SimpleNamespace as NS

import numpy as np

from mary.audio import cloud_tts
from mary.audio.cloud_tts import ElevenLabsVoice, make_voice
from mary.audio.wake_reply import WakeReplies
from mary.config import Section


def cfg(**el):
    return Section.wrap({"tts": {"provider": "elevenlabs",
                                 "elevenlabs": {"voice_id": "mom", "model": "eleven_v4_turbo", **el}}})


class FakeEleven:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.text_to_speech = NS(convert=self.convert)

    def convert(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return iter(reply)


class FakeKokoro:
    def __init__(self):
        self.said = []

    def synth(self, text, voice=None):
        self.said.append((text, voice))
        return np.zeros(10, np.float32)


def voice(*replies):
    told = []
    v = ElevenLabsVoice(cfg(), FakeKokoro(), client=FakeEleven(*replies), on_problem=told.append)
    return v, told


def test_her_voice_comes_back_as_the_speakers_24_khz_audio():
    pcm = (np.array([0, 16384, -16384], np.int16)).tobytes()
    v, _ = voice([pcm[:3], pcm[3:]])  # chunks can split a sample
    audio = v.synth("Yes, dear?")
    assert audio.dtype == np.float32 and np.allclose(audio, [0, 0.5, -0.5])
    call = v.client.calls[0]
    assert call["voice_id"] == "mom" and call["model_id"] == "eleven_v4_turbo" and call["output_format"] == "pcm_24000"


def test_offline_falls_back_to_kokoro_and_waits_before_trying_again():
    v, told = voice(ConnectionError("no internet"), [b"\x00\x00"])
    v.synth("Hello.")
    v.synth("Again.")
    assert v.fallback.said == [("Hello.", None), ("Again.", None)] and len(v.client.calls) == 1
    assert told == ["Can't reach ElevenLabs, so I'm using my own voice for now."]


def test_out_of_credits_says_so_and_pauses_for_hours():
    v, told = voice(RuntimeError("status_code: 401, body: {'detail': {'status': 'quota_exceeded'}}"))
    v.synth("Hello.")
    assert "credits are used up" in told[0] and v.skip_until - cloud_tts.time.monotonic() > 3600


def test_a_named_kokoro_voice_is_kokoro():
    v, _ = voice()
    v.synth("Hello.", voice="af_heart")  # e.g. python -m mary.voices
    assert v.fallback.said == [("Hello.", "af_heart")] and v.client.calls == []


def test_no_key_or_no_voice_id_means_kokoro(monkeypatch):
    monkeypatch.setattr("mary.audio.cloud_stt.load_key", lambda: False)
    kokoro = FakeKokoro()
    assert make_voice(cfg(), kokoro) is kokoro
    no_id = Section.wrap({"tts": {"provider": "elevenlabs", "elevenlabs": {}}})
    assert make_voice(no_id, kokoro) is kokoro


def test_wake_replies_in_her_voice_say_the_text_and_never_cache_a_failure(tmp_path):
    pcm = np.full(240, 1000, np.int16).tobytes()
    v, _ = voice([pcm], ConnectionError("blip"), [pcm])
    replies = WakeReplies(v, ["Mm-hmm? /mˈhm?/", "Yes, dear?"], cache_dir=tmp_path)
    replies.render()
    assert [c["text"] for c in v.client.calls] == ["Mm-hmm?", "Yes, dear?"]  # the text, not the phonemes
    assert set(replies.audio) == {"Mm-hmm?"} and len(list(tmp_path.glob("*.npy"))) == 1
    WakeReplies(v, ["Yes, dear?"], cache_dir=tmp_path).render()  # next start: tried again
    assert len(list(tmp_path.glob("*.npy"))) == 2
