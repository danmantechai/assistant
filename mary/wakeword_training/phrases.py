"""Wake phrases for training: what Kokoro says to teach the phrase, the sound-alikes that must not wake MARY, what to
record, and how.

"Hey Mary" and "Hey Mom" keep their hand-picked sound-alikes (tuned on the author's voice). Any other phrase gets a
generated set: the name alone, the name in a sentence, and the greeting with other names. MARY's second opinion
(Whisper confirming the phrase, wakeword.verify) covers what a generated set misses.
"""
from __future__ import annotations

import re

GREETERS = ("hey", "hi", "hello", "ok", "okay", "yo")
OTHER_NAMES = ["Tom", "Ron", "Pam", "John", "Mark", "Bob", "Gary", "Larry", "Harry", "Molly", "Maria", "Jimmy",
               "Sam", "Anna", "Dan", "Ben"]

KNOWN = {
    "mary": {"name": "Hey Mary", "file": "hey_mary",
             "pos": ["Hey Mary", "Hey, Mary.", "Hey Mary!", "hey Mary?"],
             "neg": ["Mary", "Merry Christmas", "Marry me", "Mary had a little lamb", "Hey Larry", "Hey Barry",
                     "Hey Harry", "Hey Gary", "Hey Maria", "Hey Marley", "Hey Mayor", "Hey Molly", "Hey Murray",
                     "Hey Mommy", "Hairy", "Very merry"],
             "near": ["Mary", "Hey", "Hey Larry", "Hey Barry", "Marry me", "Hey Siri", "Hey Maria", "Merry Christmas",
                      "Hey Mark", "Very nice", "Hey man", "Okay Mary", "Hey, may I ask", "Hairy", "Hey Gary"]},
    # "Mom"/"Mum" alone or in a sentence must not wake her ("I'll call my mum later").
    "mom": {"name": "Hey Mom", "file": "hey_mom",
            "pos": ["Hey Mom", "Hey, Mom.", "Hey Mum", "Hey mum!"],
            "neg": ["Mom", "Mum", "I'll call my mum later", "my mum said", "Hey Tom", "Hey Ron", "Hey Don",
                    "Hey Pam", "Hey, come here", "Hey John", "Hey, come on", "Come on", "Hey Mark", "Hey man",
                    "Hello", "Hey Bob", "Hey Mo", "Hey, what's up"],
            "near": ["Mum", "I'll call my mum later", "Hey Tom", "Hey", "Hey man", "Hey Mark"]},
}

# Said by Kokoro for every phrase: other wake words and everyday requests.
OTHER_NEG = ["Hey", "Hey there", "Hey Mark", "Hey man", "Hey Siri", "Hey Google", "Hey Jarvis", "Alexa",
             "Very good", "Hey, may I", "Hey Jimmy", "Hey Mavis", "What time is it", "Open Spotify",
             "Turn it up", "Play some music", "I'm going to the market", "See you tomorrow",
             "That's very nice", "Hey, wait for me"]

READ_ALOUD = ("Yesterday I went to the market with my sister. We bought tomatoes, onions, fresh fish and a bag of "
              "rice, then stopped for tea before the rain started. In the evening we watched the news, talked "
              "about work, and planned the weekend.")


def slug(text: str) -> str:
    """'Hey Grandma!' -> 'hey_grandma' (the model's file name)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "wake"


def split(phrase: str) -> tuple[str, str]:
    """('Hey', 'Grandma') for 'hey grandma'; ('', 'Alexa') for a phrase with no greeting."""
    words = re.sub(r"[^\w' -]+", " ", phrase).split()
    if len(words) > 1 and words[0].lower() in GREETERS:
        return words[0].capitalize(), " ".join(w[:1].upper() + w[1:] for w in words[1:])
    return "", " ".join(w[:1].upper() + w[1:] for w in words)


def styles(phrase: str) -> list[tuple[str, int]]:
    """The ways to say it while recording, and how many takes of each (about 32 in all)."""
    greet, name = split(phrase)
    slowly = f"{greet}... {name}" if greet else f"{name}... slowly"
    return [("in your normal voice", 10), ("a bit quieter, like it's late", 5),
            ("a bit louder, as if from the next room", 5), ("quickly, all in one breath", 4),
            (f"slowly: {slowly}", 4), ("from 2 or 3 metres away", 4)]


def spec(phrase_or_key: str) -> dict:
    """Everything training and recording need for a phrase: its name, file, takes prefix, what Kokoro says
    (pos), sound-alikes Kokoro says (neg), sound-alikes you record (near) and the recording styles."""
    key = phrase_or_key.strip().lower()
    known = KNOWN.get(key) or next((k for k in KNOWN.values() if k["name"].lower() == key), None)
    if known:
        out = dict(known)
    else:
        greet, name = split(phrase_or_key)
        if not name or name.lower() in GREETERS:
            raise ValueError("A wake phrase needs a name in it, like \"Hey Grandma\".")
        phrase = f"{greet} {name}".strip()
        others = [n for n in OTHER_NAMES if n.lower() not in name.lower()]
        g = greet or "Hey"
        pos = ([phrase, f"{greet}, {name}.", f"{phrase}!", f"{greet.lower()} {name}?"] if greet else
               [name, f"{name}.", f"{name}!", f"{name.lower()}?"])
        # The name alone, or in a sentence, mustn't wake it, unless the name *is* the phrase ("Alexa").
        alone = [name, f"I'll tell {name} later", f"{name} said so", f"Where is {name}?"] if greet else []
        neg = alone + [f"{g} there"] + [f"{g} {o}" for o in others[:10]]
        near = alone[:2] + ["Hey", f"{g} {others[0]}", f"{g} {others[1]}", f"{g} {others[2]}", "Hey, wait for me",
                            "Okay", f"{g}, come here"]
        out = {"name": phrase, "file": slug(phrase), "pos": pos, "neg": list(dict.fromkeys(neg)),
               "near": list(dict.fromkeys(near))}
    out["takes"] = out["file"]
    out["styles"] = styles(out["name"])
    return out
