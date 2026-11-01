# Writing a skill brick

A brick is one Python file in `mary/skills/` that gives MARY one ability. Copy `mary/skills/_template.py` to
start. Only `tools(ctx)` is required.

```python
GUIDE = "Greeting someone by name: greet."     # when to use it: a line in her system prompt
GROUNDING = [                                  # safety words, for tools that change something
    ("greet", lambda args: True, r"\b(greet|say hello|say hi)\b", "the user didn't ask to greet anyone"),
]
REFLEXES = [Reflex(r"say hello to (?P<name>\w+)", "greet", lambda text: {"name": text.split()[-1]})]
SLOW = {"greet": ["One moment."]}              # waiting lines for tools that take a few seconds

def tools(ctx: ToolContext) -> list[Tool]:
    def greet(name: str) -> ToolResult:
        return ToolResult(True, f"Greeted {name}.", say=f"Hello, {name}!")
    return [Tool("greet", "Say hello to someone by name.", {"name": {"type": "string"}}, ["name"], greet)]
```

## The rule: code first, the screen only when code can't

Do the task the most exact way there is, and only fall back down the list when you must:
1. a direct request (`mary/net.py`), Windows' own APIs, a command (`mary/terminal.py`), exact maths
2. a web page with no API: MARY's browser (`mary/web/`), through roles and visible text, never a site's selectors
3. a desktop app with no interface: UI Automation, read by layout (`mary/page.py`)
4. something that has to be seen: `mary/vision.py`; OCR only fills gaps

Exact data is also shorter to report, so it costs fewer tokens.

## The parts

- **`tools(ctx)`** returns `Tool`s (`mary/tools/registry.py`): a name, a description for the model, JSON-schema
  parameters, and the function. Useful options:
  - `tier=2`: ask "Shall I go ahead?" first; `ask=`: say *why* (new mode)
  - `announce=`: the line shown while it runs ("Launching Spotify…")
  - `private=True`: keep its results out of the log (messages, screen text)
  - `ctx` gives the settings (`ctx.cfg`), the app index, the output, the profile, the memory store
    (`ctx.memory`: search, add, end; see `mary/memory/store.py`), and what was found last
- **`ToolResult(ok, text, say=…)`**: `text` is the fact the brain turns into her words, so keep it factual and
  short (every token costs time on a CPU). `say` is the short line used when it runs as a reflex.
- **Never sleep to wait for an app or a page.** Every tool call runs inside the feedback loop (`mary/ready.py`),
  so a brick only reports facts:
  - **The window it works on is ready first.** It waits for the one in front, or the one an argument names, when
    you set `acts_on="front"` or `acts_on="window"` (the argument's name).
  - **Afterwards it waits for what changed to settle:** a new window, a page loading, a dialog. The result ends
    with what's really in front ("Now in front: Spotify, ready.").
  - **`waits_for=`** on a result names what your action started: an app's `.exe` or name, or `"front"` for
    whatever comes to the front (a web page, an opened file). The loop waits for it within that app's learned
    limit and brings it to the front.
  - **`status="not_yet"`:** what you need isn't there yet (a search box, a result, any text). The loop waits and
    calls you again while that can help: until the app you started is ready, or while the screen is still
    changing. Use it only when nothing was done yet, or when calling again is safe.
  - **`check=`:** a function for work that doesn't show on screen (a download): "is it done yet?". The loop polls
    it.
  - **`watch=False`:** for tools that never touch a window (the time, volume, timers). They skip the loop.
- **`GUIDE`**: one line, "what it's for: tool name". It's added to the system prompt once at startup.
- **`GROUNDING`**: an action only runs when the user's own words ask for that kind of thing. This is what stops
  a small model acting on a misheard sentence. Looking things up needs none.
- **`REFLEXES`**: instant commands, matched against the *whole* request. Use them sparingly: anything that
  could mean more than one thing belongs to the brain.
- **`SLOW`**: tools that take a few seconds get one short waiting line, said at once.

## Switching it on

- **A brick you're adding to MARY itself:** add its name to `ALL` in `mary/skills/__init__.py` and to
  `skills:` in `config.yaml`.
- **Just for you:** copy the `skills:` list from `config.yaml` into `user/config.yaml` and add the name there
  (a list in your settings replaces the default list).

Restart MARY.

## Testing it

- **Unit tests** in `tests/` (`pytest`): call your tool's function directly with a fake `ctx`.
- **The replay test:** add a conversation to `tests/conversations/` (what you'd say, what the tool should
  return, what to expect) and run `python -m mary.replay tests/conversations/yours.yaml`. Tools are faked,
  so nothing happens on the PC; the report in `logs/replay/` shows which tools she tried and what she said.
- `python -m mary.release_check` before you commit.
