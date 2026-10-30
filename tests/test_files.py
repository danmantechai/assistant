"""find_files replies and file_action safety, on a temporary folder of real files."""
import os
import time
from pathlib import Path

import pytest

from mary.app_index import AppIndex
from mary.config import load_config
from mary.output import Output
from mary.tools.registry import ToolContext, ToolRegistry


@pytest.fixture
def tools(tmp_path):
    for i, name in enumerate(["old notes.txt", "holiday.jpg", "budget 2026.xlsx", "promo.mp4", "report.pdf"]):
        f = tmp_path / name
        f.write_text("x")
        os.utime(f, (time.time() - 3600 * (5 - i),) * 2)  # report.pdf is the newest
    cfg = load_config()
    ctx = ToolContext(cfg, AppIndex(cfg), Output())
    return ToolRegistry(ctx), tmp_path


def test_reply_names_the_newest_files(tools):
    reg, folder = tools
    result = reg.tools["find_files"].fn(folder=str(folder))
    newest = [Path(f).name for f in reg.ctx.last_files[:3]]  # fresh files all count as created "now"
    assert result.say == f"I found 5 files in {folder.name}. The newest are {newest[0]}, {newest[1]} and {newest[2]}."
    assert len(reg.ctx.last_files) == 5 and reg.ctx.last_files_at > 0


def test_reply_with_a_type_skips_extensions(tools):
    reg, folder = tools
    assert reg.tools["find_files"].fn(folder=str(folder), file_type="pdf").say == \
        f"I found one PDF file in {folder.name}: report."


def test_asking_words_never_filter_by_name(tools):
    reg, folder = tools  # what the LLM might pass: the whole request as the "name"
    result = reg.tools["find_files"].fn(query="can you give me a list of the files", folder=str(folder))
    assert len(reg.ctx.last_files) == 5, result.text


def test_nothing_found_says_what_was_looked_for(tools):
    reg, folder = tools
    assert reg.tools["find_files"].fn(folder=str(folder), file_type="audio", modified_within="today").say == \
        f"I couldn't find any audio files in {folder.name} from today."


def test_folders_are_never_recycled(tools):
    reg, folder = tools
    result = reg.tools["file_action"].fn(action="recycle", path=str(folder))
    assert not result.ok and folder.exists()


@pytest.fixture
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(os, "startfile", seen.append, raising=False)  # nothing really opens
    return seen


@pytest.mark.parametrize("shown", ["{folder}/report.pdf", "{folder}/…/report.pdf", "report.pdf", "1"])
def test_open_takes_a_result_as_find_files_showed_it(tools, opened, shown):
    reg, folder = tools
    reg.tools["find_files"].fn(folder=str(folder), file_type="pdf")
    assert reg.tools["file_action"].fn(action="open", path=shown.format(folder=folder.name)).ok
    assert opened == [folder / "report.pdf"]


def test_open_takes_a_path_under_a_known_folder(tools, opened, monkeypatch):
    reg, folder = tools  # "Pictures/MARY/shot.png", read back from a screenshot's reply, with no search first
    (folder / "MARY").mkdir()
    (folder / "MARY" / "shot.png").write_text("x")
    monkeypatch.setattr("mary.winutil.known_folder", lambda name: folder if name == "Pictures" else None)
    assert reg.tools["file_action"].fn(action="open", path="Pictures/MARY/shot.png").ok
    assert opened == [folder / "MARY" / "shot.png"]


def test_same_name_in_two_folders_follows_the_folder_given(tools, opened):
    reg, folder = tools
    for sub in ("work", "home"):
        (folder / sub).mkdir()
        (folder / sub / "plan.txt").write_text("x")
    reg.tools["find_files"].fn(folder=str(folder), query="plan")
    assert len(reg.ctx.last_files) == 2
    assert reg.tools["file_action"].fn(action="open", path=f"{folder.name}/…/home/plan.txt").ok
    assert opened == [folder / "home" / "plan.txt"]


def test_a_file_no_search_found_is_still_unknown(tools, opened):
    reg, _ = tools
    result = reg.tools["file_action"].fn(action="open", path="Pictures/MARY/nothing here.png")
    assert not result.ok and opened == []
