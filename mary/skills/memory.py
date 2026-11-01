"""memory: remember and forget what the user tells her, look back at what they said before, set their name.

What they ask her to "remember that…" is kept in their profile (pinned in every request, as ever). Everything else she
learned, and what happened each day, is in the memory store (mary/memory/): the "remembered #12" lines in a request's
context come from there, and `recall` looks further. Forgetting ends a memory and keeps it with its date; only "delete it
for good" removes one, and it asks first."""
from __future__ import annotations

from mary.memory.store import clean, core_key
from mary.tools.registry import Tool, ToolContext, ToolResult

GUIDE = ('"Remember that…", "forget that…", "call me…", and what they told you before ("what did I say about X?", "what did '
         'we do yesterday?"): memory. Context lines marked "remembered #n" are things you knew: use them when they fit, '
         'never say the numbers. If neither they nor the conversation so far answer a question about the user or the '
         'past, call memory with recall, and if that finds nothing say you don\'t have it.')
GROUNDING = [
    ("memory", lambda a: a.get("action") == "forget",
     r"\b(forget|stop remembering|remove|delete|erase)\b", "the user didn't ask to forget anything"),
    ("memory", lambda a: a.get("action") == "delete",
     r"\b(for good|permanent(?:ly)?|forever|erase|wipe|delete (?:it|that|this)|delete everything)\b",
     "the user didn't ask to delete it for good"),
    ("memory", lambda a: a.get("action") == "set_name",
     r"\b(name|call me|called)\b", "the user didn't say what to call them"),
    ("memory", lambda a: a.get("action") == "remember",
     r"\b(remember|note|keep in mind|don'?t forget|memori[sz]e)\b", "the user didn't ask MARY to remember anything"),
]

ACTIONS = ["remember", "recall", "forget", "delete", "set_name", "list"]
KINDS = ("core", "fact", "diary", "reflection")


def tools(ctx: ToolContext) -> list[Tool]:
    def store():
        return ctx.memory

    def ask_which(candidates) -> str:
        return "; ".join(f"#{m.id} {m.text[:70]}" for m in candidates)

    def end_it(m, why: str) -> bool:
        """End one memory wherever it lives: a core fact is in the profile, anything else in the store."""
        if m.kind == "core":
            return bool(ctx.profile and ctx.profile.forget_exact(m.text, why))
        store().end(m.id, why)
        return True

    def remember(fact: str, replaces: str) -> ToolResult:
        profile = ctx.profile
        result = profile.remember(fact)
        s = store()
        if result == "already" and not (replaces and s is not None):
            return ToolResult(True, "That was already remembered.", say="I already knew that.")
        ended = None
        if s is not None:
            new = s.by_key("core", core_key(fact))
            old = s.resolve(replaces, kinds=("core", "fact"))[0] if replaces else None
            if old is not None and old.current and (new is None or old.id != new.id):
                ended = old
                if old.kind == "core":
                    end_it(old, "superseded")
                else:
                    s.end(old.id, "superseded", superseded_by=new.id if new else None)
            dup = s.find_duplicate(fact, kinds=("fact",))   # a fact she had learned, now said outright
            if dup is not None and (new is None or dup.id != new.id):
                s.end(dup.id, "superseded", superseded_by=new.id if new else None)
        text = "That was already remembered." if result == "already" else f"Remembered: {fact}."
        if ended is not None:
            text += f' It replaces "{ended.text[:70]}", which is now ended.'
        elif s is not None and result != "already":
            # something she already knew that this may have changed: the model decides, by number
            new = s.by_key("core", core_key(fact))
            related = [h.memory for h in s.search(fact, kinds=("core", "fact"), k=3, min_relevance=0.45, dates=False)
                       if new is None or h.memory.id != new.id]
            if related:
                text += (" You already had: " + "; ".join(f'#{m.id} "{m.text[:70]}"' for m in related) +
                         ". If one of those is now out of date, call memory again with action remember, the same "
                         "fact and replaces set to its number.")
        return ToolResult(True, text, say="Got it, I'll remember that.")

    def recall(query: str) -> ToolResult:
        from mary.memory import recall as recalling

        s = store()
        if s is None:
            return ToolResult(False, "Memory search isn't switched on.")
        if not clean(query):
            return ToolResult(False, "recall needs what to look for (a person, a thing, a day).")
        hits = s.search(query, k=s.setting("recall.max_items"), cue=True,
                        current=None if recalling.wants_history(query) else True)
        if not hits:
            return ToolResult(True, f"Nothing in memory matches '{query}'. Say you don't have it; don't guess.",
                              say="I don't have anything about that.")
        lines = [recalling.describe(h) for h in hits]
        return ToolResult(True, "\n".join(lines))

    def forget(fact: str) -> ToolResult:
        s, profile = store(), ctx.profile
        if s is not None:
            one, candidates = s.resolve(fact, kinds=("core", "fact"))
            if one is not None:
                end_it(one, "forgotten")
                return ToolResult(True, f"Forgot: {one.text}", say="Done, I've forgotten that.")
            if candidates:
                return ToolResult(False, f"More than one memory matches: {ask_which(candidates)}. Ask which one.")
        gone = profile.forget(fact)   # the plain way: a remembered line that shares a word with what they said
        if gone is None:
            return ToolResult(False, f"Nothing remembered matches '{fact}'.", say="I don't have anything like that remembered.")
        return ToolResult(True, f"Forgot: {gone}", say="Done, I've forgotten that.")

    def delete(fact: str) -> ToolResult:
        s = store()
        if s is None:
            return ToolResult(False, "Nothing to delete: memory isn't switched on.")
        one, candidates = s.resolve(fact, kinds=KINDS, current=None)
        if one is None:
            return ToolResult(False, "Nothing matches." if not candidates else
                              f"More than one memory matches: {ask_which(candidates)}. Ask which one.")
        if one.kind == "core" and ctx.profile is not None:
            ctx.profile.erase(one.text)
        s.delete(one.id)
        return ToolResult(True, f"Deleted for good: {one.text}", say="Done, it's gone for good.")

    def precheck_delete(args: dict) -> str | None:
        if args.get("action") != "delete":
            return None
        s = store()
        if s is None or not clean(args.get("fact", "")):
            return "there is nothing to delete, or no memory was named"
        one, candidates = s.resolve(args["fact"], kinds=KINDS, current=None)
        if one is not None:
            return None
        return ("no memory matches that" if not candidates else
                f"there is more than one memory like that ({ask_which(candidates)}): ask the user which one")

    def memory(action: str, fact: str = "", replaces: str = "") -> ToolResult:
        profile = ctx.profile
        if profile is None:
            return ToolResult(False, "No profile is loaded.")
        if action == "remember":
            if not fact.strip():
                return ToolResult(False, "remember needs the fact to keep.")
            return remember(fact, replaces)
        if action == "recall":
            return recall(fact)
        if action == "forget":
            return forget(fact)
        if action == "delete":
            return delete(fact)
        if action == "set_name":
            if not fact.strip():
                return ToolResult(False, "set_name needs the name.")
            profile.set_name(fact)
            return ToolResult(True, f"Name set to {profile.name}.", say=f"Nice to meet you, {profile.name}.")
        if action == "list":
            items = ([f"Name: {profile.name}"] if profile.name else []) + profile.facts
            s = store()
            if s is not None:
                learned = s.list(kinds=("fact",), current=True, limit=15)
                items += [f"{m.text} (learned {m.happened:%d %b})" for m in learned]
            return ToolResult(True, "Remembered: " + ("; ".join(items) if items else "nothing yet"))
        return ToolResult(False, f"action must be one of {ACTIONS}.")

    def is_delete(a: dict) -> int:
        return 2 if a.get("action") == "delete" else 1

    def named(a: dict) -> str:
        """What a delete is about, in the memory's own words (the model may name it by number)."""
        s, said = store(), str(a.get("fact", "that"))
        one = s.resolve(said, kinds=KINDS, current=None)[0] if s is not None else None
        return (one.text if one is not None else said)[:60]

    return [Tool(
        "memory",
        "Remember a fact or preference about the user (\"remember that I prefer Edge\"), look back at what they told "
        "you or what happened (recall: a person, a thing, a day), forget one (it is kept, ended), delete one for good "
        "(only when they say so), set the name they want to be called, or list what's remembered.",
        {"action": {"type": "string", "enum": ACTIONS},
         "fact": {"type": "string", "description": "the fact, what to look for or forget, the #number of a memory, "
                                                   "or the name"},
         "replaces": {"type": "string", "description": "remember only: the #number of the older memory this replaces"}},
        ["action"], memory, speaks_for_itself=True, private=True, watch=False,
        tier=is_delete, new_tier=is_delete, precheck=precheck_delete,
        confirm_text=lambda a: f"erase \"{named(a)}\" from my memory for good",
        ask=lambda a: f"That would erase \"{named(a)}\" for good, with no way back. Shall I go ahead?",
        announce=lambda a: {"remember": "Remembering…", "forget": "Forgetting…", "set_name": "Noting your name…",
                            "list": "Checking what I remember…", "recall": "Looking back…",
                            "delete": "Erasing…"}.get(a.get("action"), "Memory…"),
    )]
