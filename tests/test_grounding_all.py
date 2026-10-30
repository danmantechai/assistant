"""Every action needs words of its kind: no real command may be refused, nonsense may not act."""
import pytest
import yaml

from mary.config import PROJECT_DIR, load_config
from mary.llm.bench import CASES
from mary.tools.safety import ungrounded_reason
from tests.conftest import needs_router


@pytest.fixture(scope="module")
def router():
    from mary.app_index import AppIndex
    from mary.router.router import Router
    cfg = load_config()
    return needs_router(lambda: Router(cfg, AppIndex(cfg)))


def _phrasings():
    spec = yaml.safe_load((PROJECT_DIR / "intents.yaml").read_text(encoding="utf-8"))["intents"]
    cases = yaml.safe_load((PROJECT_DIR / "tests" / "router_cases.yaml").read_text(encoding="utf-8"))["cases"]
    return [e for d in spec.values() for e in d["examples"]] + [c["text"] for c in cases]


def test_no_routed_command_is_refused(router):
    refused = []
    for text in _phrasings():
        d = router.route(text)
        if d.route == "direct" and d.tool:
            why = ungrounded_reason(d.tool, d.args, text)
            if why:
                refused.append((text, d.tool, d.args, why))
    assert refused == []


@pytest.mark.parametrize("case", [c for c in CASES if c.tool and not c.before], ids=lambda c: c.label)
def test_model_answers_to_real_requests_pass(case):
    """The calls a model should make for the benchmark's requests are all allowed."""
    examples = {"web": {"action": "search", "value": "x"}, "screen": {"action": "click"}}
    args = examples.get(case.tool, {"action": "volume_down"} if case.tool == "system_control" else {})
    if case.label == "type into box":
        args = {"action": "type"}
    assert ungrounded_reason(case.tool, args, case.text) is None


@pytest.mark.parametrize("tool, args", [
    ("system_control", {"action": "volume_up", "value": 100}),
    ("memory", {"action": "set_name", "fact": "Sam"}),
    ("memory", {"action": "remember", "fact": "x"}),
    ("play_music", {"query": "x"}),
    ("set_timer", {"duration": "5 minutes"}),
    ("take_screenshot", {}),
    ("open_app", {"name": "notepad"}),
    ("keyboard", {"action": "type", "text": "x"}),
])
def test_a_misheard_sentence_cannot_act(tool, args):
    assert ungrounded_reason(tool, args, "It's the current grader.") is not None
