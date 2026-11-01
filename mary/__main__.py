"""Entry point.

    python -m mary                 voice mode: HUD, press Ctrl+Space (or say the wake word) and speak
    python -m mary --text          type requests in the terminal
    python -m mary --text --hud    type, with the HUD showing what MARY does
    add --debug for per-stage latency
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from types import SimpleNamespace

HELP = """\
  Type a request, e.g. "open spotify", "turn the volume down a bit", "find the PDF I downloaded this week".
  /think <request>  answer with deep thinking on
  /stats            RAM used by MARY, Ollama and the HUD
  /apps <name>      show how an app name matches the Start Menu index
  /route <request>  show how the router would handle a request (runs nothing)
  /reset            forget the conversation so far
  /quit             exit"""


def main() -> None:
    parser = argparse.ArgumentParser(prog="mary", description="MARY, a local voice assistant")
    parser.add_argument("--text", action="store_true", help="type commands instead of speaking")
    parser.add_argument("--hud", action="store_true", help="show the HUD; the hotkey (Ctrl+Space) summons it")
    parser.add_argument("--debug", action="store_true", help="print per-stage latency and thinking")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--background", action="store_true",
                        help="no console: tray icon only (what the start-at-sign-in shortcut runs)")
    parser.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS)  # by the settings page
    args = parser.parse_args()
    # run.bat starts MARY again when she exits for a restart, and says so in MARY_RESTARTED.
    args.restarted = args.restarted or os.environ.get("MARY_RESTARTED") == "1"

    if sys.stdout is None or sys.stderr is None:  # pythonw: there is no console to write to
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
        args.background = True
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from mary import winutil
    from mary.config import expand_path, load_config
    from mary.logs import setup_logging

    winutil.enable_dpi_awareness()
    winutil.enable_console_colors()
    cfg = load_config(args.config)
    debug = args.debug or cfg.logging.debug
    setup_logging(expand_path(cfg.paths.logs), cfg.logging.level)
    if not args.text:
        # After a restart the old MARY may still be closing: wait for her to go.
        if not winutil.single_instance("MARY-voice", wait_s=15 if args.restarted else 0):
            print("MARY is already running: look for her icon in the notification area (by the clock).")
            return
        run_voice_mode(cfg, debug, background=args.background, restarted=args.restarted)
    elif args.hud:
        run_with_hud(cfg, debug)
    else:
        run_text_mode(cfg, debug)


def run_voice_mode(cfg, debug: bool, background: bool = False, restarted: bool = False) -> None:
    """HUD and tray icon on the main thread; voice loop, hotkey and the console (for /commands) on
    their own threads. background: no console REPL (pythonw at sign-in); quit from the tray."""
    import signal

    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtWidgets import QApplication

    from mary.config import expand_path
    from mary.hud.output import HudOutput
    from mary.hud.tray import Tray
    from mary.hud.window import HudWindow
    from mary.settings.server import Hooks

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    hud = HudWindow(cfg)
    hud_out = HudOutput(hud)
    session = SimpleNamespace(loop=None, stop=threading.Event(), restart=False)

    def restart() -> None:  # from the settings page's Restart button
        session.restart = True
        QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)

    # What the settings page may use while she runs. Restarting needs someone to start her again: run.bat, or
    # herself when she runs in the background.
    session.hooks = Hooks(running=True, restart=restart if background or _from_run_bat() else None)
    tray = Tray(app, cfg, on_talk=lambda: session.loop and session.loop.summon(), on_quit=app.quit,
                log_file=expand_path(cfg.paths.logs) / "mary.log",
                on_settings=lambda: open_settings(cfg, session.hooks))
    if restarted:  # the page that restarted her keeps working
        open_settings(cfg, session.hooks, browser=False)

    def worker() -> None:
        try:
            voice_session(cfg, debug, hud_out, session, tray, background)
        except Exception as e:
            logging.getLogger("mary").exception("voice mode failed")
            print(f"  Voice mode stopped: {e}")
            tray.notice.emit(f"MARY stopped: {e}. Details are in the log.")
        finally:
            QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)

    thread = threading.Thread(target=worker, name="voice-setup", daemon=True)
    thread.start()
    app.exec()
    session.stop.set()
    thread.join(timeout=3 if background else 0.5)  # a console session may be blocked reading input
    if session.restart:
        _restart(cfg, background, debug)
    elif cfg.llm.get("unload_on_quit", True):
        _unload_model(cfg)


def _from_run_bat() -> bool:
    return os.environ.get("MARY_LAUNCHER") == "run.bat"


def open_settings(cfg, hooks, browser: bool = True) -> None:
    """Start the settings page inside MARY (once) and open it in the default browser."""
    import webbrowser

    from mary.settings.server import DEFAULT_PORT, start

    def go() -> None:
        try:
            srv = start(cfg.get("settings", {}).get("port", DEFAULT_PORT), hooks)
            if browser:
                webbrowser.open(srv.url)
        except Exception:
            logging.getLogger("mary").exception("couldn't open the settings page")

    threading.Thread(target=go, name="settings-open", daemon=True).start()


def _restart(cfg, background: bool, debug: bool) -> None:
    """Start MARY again with the settings just saved. The model stays loaded unless it was changed, so she's
    back in seconds; then she reads her (maybe new) instructions once."""
    import subprocess

    from mary.config import PROJECT_DIR, load_config

    provider = lambda c: (c.get("brain") or {}).get("provider", "ollama")  # noqa: E731
    try:
        new = load_config()
        changed = new.llm.model != cfg.llm.model or provider(new) != provider(cfg)
    except Exception:
        changed = False
    if changed:
        _unload_model(cfg)  # free the old one's memory before the new one loads
    logging.getLogger("mary").info("restarting for the settings page%s", " (new model)" if changed else "")
    if background:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([pythonw, "-m", "mary", "--background", "--restarted", *(["--debug"] if debug else [])],
                         cwd=PROJECT_DIR, creationflags=flags, close_fds=True)
    else:
        print("  Restarting MARY…", flush=True)
        sys.exit(3)  # run.bat starts her again


def _unload_model(cfg) -> None:
    """Free the model's RAM (~2-4 GB) when MARY quits; it would otherwise stay loaded (keep_alive -1)."""
    from mary.brain import make_brain

    try:
        make_brain(cfg).unload()
    except Exception:
        pass


def _report_credits(console, client) -> None:
    """One line at startup: how much of the month's ElevenLabs credit is left (her voice and hearing use it)."""
    from datetime import datetime

    from mary.audio.cloud_tts import credits_left

    left = credits_left(client)
    if left:
        used, limit, reset = left
        console.status(f"ElevenLabs: {max(0, limit - used):,} of {limit:,} credits left this month "
                       f"(renews {datetime.fromtimestamp(reset):%d %b}).")


def voice_session(cfg, debug: bool, hud_out, session, tray, background: bool = False) -> None:
    import time

    from mary.audio.download import missing
    from mary.audio.mic import Microphone
    from mary.audio.clean import make_cleaner
    from mary.audio.cloud_tts import make_voice
    from mary.audio.cloud_stt import make_ear
    from mary.audio.tts import Speaker, TextToSpeech, VoiceOutput
    from mary.audio.vad import SileroVAD
    from mary.config import expand_path
    from mary.core import Mary
    from mary.hotkey import GlobalHotkey
    from mary.hud.tray import TrayOutput
    from mary.output import ConsoleOutput, MultiOutput
    from mary.stats import memory_report
    from mary.voice import VoiceLoop

    console = ConsoleOutput(debug=debug, echo_user=True)
    print(f"{console.CYAN}MARY{console.RESET} · voice mode · {cfg.llm.model} · {cfg.tts.voice}")
    gone = missing(cfg)
    if gone:
        print(f"{console.RED}  Voice models missing: {', '.join(gone)}.\n  Download them with:  "
              f"python -m mary.audio.download   (or use text mode: python -m mary --text){console.RESET}")
        tray.notice.emit("Voice models are missing. Run: python -m mary.audio.download")
        return
    models = expand_path(cfg.paths.data) / "models"
    start = time.perf_counter()
    tray_out = TrayOutput(tray)
    kokoro = TextToSpeech(cfg, models)
    session.hooks.kokoro = kokoro  # the settings page's voice samples use her Kokoro instead of loading another
    tts = make_voice(cfg, kokoro, on_problem=lambda m: (console.status(f"⚠ {m}"), tray_out.status(m)))
    speaker = Speaker(cfg.audio.output_device, cfg.tts.volume, on_level=hud_out.level)
    voice_out = VoiceOutput(tts, speaker)
    voice_out.on_problem = lambda message: (console.status(f"⚠ {message}"), tray_out.status(message))
    if hasattr(tts, "client"):
        print(f"{console.DIM}  Voice: {tts.name}, the voice in tts.elevenlabs.voice_id (Kokoro if it can't be "
              f"reached).{console.RESET}")
        threading.Thread(target=_report_credits, args=(console, tts.client), daemon=True).start()
    # The voice sink comes first: it is the one that asks "Shall I go ahead?" (out loud).
    mary = Mary(cfg, MultiOutput(voice_out, hud_out, console, tray_out), debug)
    if mary.memory is not None:
        mary.memory.start()   # loads the meaning model on a background thread; the first request doesn't wait
        mary.start_photos()
    session.hooks.profile = mary.profile  # the settings page edits her own profile: it applies at once
    session.hooks.memory = mary.memory    # and her memory: the Memory page changes the store she is using
    session.hooks.photos, session.hooks.ingest = mary.photos, mary.ingest   # and her photos
    stt = make_ear(cfg, models, apps=mary.apps, on_problem=lambda m: (console.status(f"⚠ {m}"), tray_out.status(m)))
    if hasattr(stt, "client"):
        print(f"{console.DIM}  Hearing: {stt.name} (your local Whisper if it can't be reached).{console.RESET}")
    vad = SileroVAD(models / "silero" / "silero_vad.onnx")
    wakeword, phrases, wake_check = None, [], None
    if cfg.wakeword.enabled:
        try:
            from mary.audio.wakeword import WakeCheck, WakeWord, threshold_for, usable_models

            # "Hey Mary" and "Hey Mom" are separate models, each loaded once training has passed it on your voice.
            listed = [cfg.wakeword.model, *(cfg.wakeword.get("also") or [])]
            phrases = usable_models([str(expand_path(m)) if m.endswith(".onnx") else m for m in listed],
                                    checked=bool(cfg.wakeword.get("verify")))
            if not phrases:
                raise RuntimeError("no wake word has passed training on your voice yet")
            wakeword = WakeWord(phrases, [threshold_for(m, cfg.wakeword.threshold) for m in phrases],
                                cfg.wakeword.cooldown_s)
            if cfg.wakeword.get("verify"):
                wake_check = WakeCheck(cfg.wakeword.verify, models, cfg.stt.cpu_threads, mary.agent.persona)
        except Exception as e:
            print(f"  Wake word off ({e}). Use the hotkey.")
    wake_replies = None
    if wakeword and cfg.wakeword.get("replies"):
        from mary.audio.wake_reply import WakeReplies

        wake_replies = WakeReplies(tts, cfg.wakeword.replies, cfg.wakeword.get("morning_replies") or [],
                                   expand_path(cfg.paths.data) / "voice_cache" / "wake_replies")
        wake_replies.render_in_background()  # a few seconds the first time, then from the cache
    print(f"{console.DIM}  voices, speech and router loaded in {time.perf_counter() - start:.1f} s. "
          f"Loading the language model (up to ~2 minutes after a restart)…{console.RESET}", flush=True)
    tray.tooltip.emit("MARY · loading the language model…")
    # Everything is ready before MARY says she is: listening while the model still loads made the first
    # requests slow (speech recognition shared the CPU with it) or answered "still starting up".
    stt_warm = threading.Thread(target=stt.warm_up, name="stt-warm-up", daemon=True)
    stt_warm.start()
    seconds = mary.warm_up()
    stt_warm.join(timeout=30)
    if seconds is None:
        print(f"{console.RED}  {mary.agent.offline_reason}\n  Starting without it: simple commands work, and "
              f"I'll keep trying to reach it.{console.RESET}")
        tray_out.status(f"My language model isn't available ({mary.agent.offline_reason}). Simple "
                        "commands still work; I'll keep trying.")
    else:
        logging.getLogger("mary").info("language model ready in %.1f s", seconds)
        print(f"{console.DIM}  Language model ready in {seconds:.0f} s.{console.RESET}")

    hotkey = GlobalHotkey(cfg.hotkey.combo, lambda: loop.summon())
    loop = VoiceLoop(cfg, mary, Microphone(cfg.audio.input_device), vad, stt, voice_out, hud_out,
                     MultiOutput(console, tray_out), wakeword, hotkey_keys=(hotkey.mods, hotkey.vk),
                     wake_check=wake_check, wake_replies=wake_replies, cleaner=make_cleaner(cfg))
    session.loop = loop
    session.hooks.pause_wake = loop.pause_wake  # while the settings page records wake-word takes
    hud_out.w.busy = lambda: loop.busy or voice_out.speaking  # the HUD stays until the conversation is over
    loop.start()
    hotkey.start()
    hotkey.registered.wait(2)
    from mary.audio.wakeword import spoken_name

    how = [f"press {cfg.hotkey.combo}" if not hotkey.error else None,
           "say " + " or ".join(f'"{spoken_name(m)}"' for m in phrases) if wakeword else None]
    how = " or ".join(h for h in how if h) or "type below"
    if hotkey.error:
        print(f"  Couldn't register {cfg.hotkey.combo} ({hotkey.error}); change hotkey.combo in config.yaml.")
    print(f"  Ready: {how}, then speak. Hold the hotkey to talk as long as you like, or tap it again")
    print("  to finish. After answering, MARY keeps listening a few seconds for a follow-up.")
    tray.tooltip.emit(f"MARY · {how}")
    session.hooks.ready = True
    logging.getLogger("mary").info("voice mode ready%s: %s", " (background)" if background else "", how)
    if background:
        tray.notice.emit(f"MARY is ready: {how}.")
    else:
        print("  You can also type a request here; /stats, /quit.")
    greeting = mary.greeting()
    console.say(greeting)
    hud_out.notify(greeting)
    voice_out.notify(greeting)
    try:
        while not background:
            try:
                line = input().lstrip("﻿").strip()
            except EOFError:
                break
            if line in ("/quit", "/exit"):
                break
            if line == "/stats":
                print(memory_report())
            elif line == "/listen":
                loop.summon()
            elif line:
                loop.submit(line)
        if background:
            session.stop.wait()  # until Quit in the tray menu
    finally:
        loop.running = False
        hotkey.stop()
        voice_out.wait_done(5)
        speaker.close()


def run_with_hud(cfg, debug: bool) -> None:
    """Qt owns the main thread (the HUD); the text REPL runs on a worker thread; the hotkey on its own."""
    import signal

    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtWidgets import QApplication

    from mary import winutil
    from mary.hotkey import GlobalHotkey
    from mary.hud.output import HudOutput
    from mary.hud.window import HudWindow

    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Qt's loop can't see Ctrl+C, so let it end MARY directly
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    hud = HudWindow(cfg)
    hud_out = HudOutput(hud)

    def on_hotkey() -> None:
        hud.command.emit("toggle")
        if cfg.hotkey.focus_console:  # text mode: bring the terminal forward so you can type
            hwnd = winutil.console_window()
            if hwnd:
                winutil.focus_window(hwnd)

    hotkey = GlobalHotkey(cfg.hotkey.combo, on_hotkey)
    hotkey.start()
    hotkey.registered.wait(2)
    if hotkey.error:
        print(f"  Couldn't register the {cfg.hotkey.combo} hotkey ({hotkey.error}). "
              "Pick another one under hotkey.combo in config.yaml.")
    else:
        print(f"  HUD on. Press {cfg.hotkey.combo} anywhere to summon or dismiss it.")

    def repl() -> None:
        try:
            run_text_mode(cfg, debug, extra=[hud_out], on_ready=lambda: _hello(hud_out))
        finally:
            hotkey.stop()
            QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)  # thread-safe quit

    threading.Thread(target=repl, name="repl", daemon=True).start()
    app.exec()


def _hello(hud_out) -> None:
    """Show the HUD briefly once MARY is ready, so you know it works."""
    hud_out.user_text("")
    hud_out.reply_start()
    hud_out.reply_delta("Online.")
    hud_out.reply_end()
    hud_out.state("idle")


def run_text_mode(cfg, debug: bool, extra: list | None = None, on_ready=None) -> None:
    from mary.core import Mary
    from mary.output import ConsoleOutput, MultiOutput
    from mary.router.guards import split_clauses
    from mary.stats import memory_report

    out = ConsoleOutput(debug=debug)
    sink = MultiOutput(out, *extra) if extra else out  # the console stays first: it asks confirmations
    print(f"{out.CYAN}MARY{out.RESET} · text mode · {cfg.llm.model} · /help for commands")
    mary = Mary(cfg, sink, debug)
    if mary.memory is not None:
        mary.memory.start()
        mary.start_photos()
    router = f"router ready in {mary.router_load_s:.1f} s" if mary.router else "router off"
    print(f"{out.DIM}  {len(mary.apps.apps)} apps indexed, {router}. Warming up the model…{out.RESET}",
          flush=True)
    seconds = mary.warm_up()
    if seconds is None:
        print(f"{out.RED}  {mary.agent.offline_reason}\n  Starting without it: simple commands work, and I'll "
              f"keep trying to reach it.{out.RESET}")
    else:
        print(f"{out.DIM}  Ready in {seconds:.1f} s.{out.RESET}")
    if on_ready:
        on_ready()

    while True:
        try:
            line = input("You › ").lstrip("﻿").strip()  # piped input can start with a byte-order mark
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            if cmd in ("/quit", "/exit"):
                break
            if cmd == "/help":
                print(HELP)
            elif cmd == "/stats":
                print(memory_report())
            elif cmd == "/reset":
                mary.agent.reset()
                print("  (conversation cleared)")
            elif cmd == "/apps":
                m = mary.apps.match(rest)
                print(f"  -> {m.app.name if m.app else None}  score={m.score:.0f}  candidates={m.candidates}")
            elif cmd == "/route" and rest and mary.router:
                for clause in split_clauses(rest):
                    d = mary.router.route(clause)
                    print(f"  {clause!r}: {d.route} -> {d.intent} {d.score:.2f} (margin {d.margin:.2f}) "
                          f"{d.tool or ''} {d.args or ''}\n    {d.reason}; top: "
                          + ", ".join(f"{n} {s:.2f}" for n, s in d.top))
            elif cmd == "/think" and rest:
                report(mary.handle(rest, think=True), debug)
            else:
                print(HELP)
            continue
        report(mary.handle(line), debug)


def report(latency, debug: bool) -> None:
    if debug:
        print(latency.table())


if __name__ == "__main__":
    main()
