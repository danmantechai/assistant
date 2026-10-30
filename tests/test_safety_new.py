"""Friendlier safety (new mode): countdown instead of a question, undo instead of a question, specific questions.
Nothing here shuts down or sleeps the PC: the shutdown command and the power call are faked."""
import pytest

from mary.config import Section, load_config
from mary.output import Output
from mary.reflexes import Reflexes
from mary.tools.registry import ToolContext, ToolRegistry


class Asked(Output):
    def __init__(self):
        self.questions = []

    def confirm(self, question):
        self.questions.append(question)
        return True


def registry(mode):
    cfg = load_config()
    cfg["brain"] = Section.wrap({"provider": "ollama", "mode": mode})
    out = Asked()
    reg = ToolRegistry(ToolContext(cfg, None, out))
    return reg, out


@pytest.fixture
def commands(monkeypatch):
    """Records shutdown commands instead of running them."""
    from mary.skills import power

    ran = []
    monkeypatch.setattr(power.subprocess, "Popen", lambda args, **kw: ran.append(args))
    monkeypatch.setitem(power._pending, "until", 0.0)
    return ran


def test_a_shutdown_he_asked_for_counts_down_and_stop_cancels_it(commands):
    from mary import skills

    reg, out = registry("new")
    reg.ctx.request = "shut down the computer"
    result = reg.run("power", {"action": "shutdown"})
    assert result.ok and out.questions == [] and commands == [["shutdown", "/s", "/t", "10"]]
    assert "Say stop to cancel" in result.say
    reflexes = Reflexes(skills.reflexes(reg.bricks))
    reflex, args = reflexes.match("Stop!")
    assert reflex.tool == "cancel_power"           # not "pause the music" while a shutdown counts down
    reg.ctx.request = "stop"
    assert reg.run("cancel_power", args).ok and commands[-1] == ["shutdown", "/a"]
    assert reflexes.match("stop")[0].tool == "media_control"  # nothing pending any more: stop means pause


def test_the_old_mode_still_asks_before_shutting_down(commands):
    reg, out = registry("today")
    reg.ctx.request = "shut down the computer"
    reg.run("power", {"action": "shutdown"})
    assert out.questions and "Shall I go ahead" in out.questions[0] and "cancel_power" not in reg.tools


def test_signing_out_still_asks_with_the_reason(commands):
    reg, out = registry("new")
    reg.ctx.request = "sign me out"
    reg.run("power", {"action": "sign_out"})
    assert out.questions == ["Signing out closes all your apps, and anything unsaved is lost. Sign out now?"]


def test_a_moved_file_can_be_moved_back_with_undo(tmp_path):
    from mary import skills

    src, dest = tmp_path / "Documents", tmp_path / "Archive"
    src.mkdir(), dest.mkdir()
    (src / "budget.xlsx").write_text("numbers")
    reg, out = registry("new")
    reg.ctx.request = "move budget.xlsx to the archive folder"
    result = reg.run("file_action", {"action": "move", "path": str(src / "budget.xlsx"), "destination": str(dest)})
    assert result.ok and out.questions == [] and (dest / "budget.xlsx").exists()
    assert "undo" in result.text
    reflex, args = Reflexes(skills.reflexes(reg.bricks)).match("undo that")
    reg.ctx.request = "undo that"
    assert reg.run(reflex.tool, args).ok and (src / "budget.xlsx").exists() and not (dest / "budget.xlsx").exists()
    assert Reflexes(skills.reflexes(reg.bricks)).match("undo") is None  # nothing left to undo: Ctrl+Z is the app's


def test_closing_an_editor_asks_a_specific_question():
    reg, _ = registry("new")
    assert reg.tools["close_app"].ask({"name": "notepad"}) == "Notepad may have unsaved work. Close it anyway?"
    assert "lost" in reg.tools["close_app"].ask({"name": "notepad", "force": True})
