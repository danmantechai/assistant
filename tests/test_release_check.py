"""The release check (mary/release_check.py): what must never be published is caught; ordinary text isn't."""
from mary.release_check import check_files, check_text, private_words

FAKE_KEY = "sk_" + "a1b2c3d4e5" * 4   # built here so this file doesn't trip the check itself


def problems(text, words=(), secrets=(), username="sam"):
    return [p.what for p in check_text("x.py", text, list(words), list(secrets), username)]


def test_keys_and_tokens_are_caught():
    assert problems(f"key = {FAKE_KEY}")
    assert problems('api_key = "abcdefghijklmnop"')  # release-check: ignore
    assert problems("ELEVENLABS_API_KEY=abcdefghijklmnop")  # release-check: ignore
    assert problems("gh" + "p_" + "x" * 36)


def test_a_key_from_your_env_is_caught_without_printing_it():
    found = problems("token: hunter2hunter2", secrets=["hunter2hunter2"])
    assert found and not any("hunter2" in f for f in found)


def test_your_words_and_user_folder_are_caught():
    assert problems("Good morning, Doctor.", words=["Doctor"])
    assert not problems("a doctoral degree", words=["Doctor"])  # whole words only
    assert problems(r"Saved to C:\Users\sam\Pictures\x.png")
    assert not problems(r"Saved to C:\Users\you\Pictures\x.png")


def test_ordinary_code_passes_and_a_line_can_be_let_through():
    assert not problems("ELEVENLABS_API_KEY is read from .env; never put it in code.")
    assert not problems(f"example = {FAKE_KEY}  # release-check: ignore")


def test_private_folders_and_big_files(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"0" * (3 * 1024 * 1024))
    (tmp_path / "ok.py").write_text("print('hi')\n", encoding="utf-8")
    found = check_files(["user/config.yaml", "data/models/x.onnx", ".env", "big.bin", "ok.py", "logs/mary.log"],
                        root=tmp_path, words=[], secrets=[], username="")
    assert {p.path for p in found} == {"user/config.yaml", "data/models/x.onnx", ".env", "big.bin", "logs/mary.log"}


def test_private_words_come_from_your_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MARY_USER_DIR", str(tmp_path))
    (tmp_path / "profile.yaml").write_text("name: Kofi\nfacts: [\"My sister's name is Ama\"]\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("tts: {elevenlabs: {voice_id: abc123xyz}}\n", encoding="utf-8")
    (tmp_path / "private_words.txt").write_text("# people\nMy Street\n", encoding="utf-8")
    assert private_words() == ["Ama", "Kofi", "My Street", "abc123xyz"]
