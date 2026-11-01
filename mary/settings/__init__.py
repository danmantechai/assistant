"""MARY's settings page: a local web page (http://127.0.0.1:8765) for shaping her without editing files.

It edits the same files you would by hand (user/config.yaml, her persona folder, user/profile.yaml), keeping their
comments. Open it from the tray menu (Settings…), or run `python -m mary.settings` (settings.bat).

    store.py    reads the layers and saves changes to the right file (the rules are in its docstring)
    schema.py   the settings each page shows, with their labels and help lines
    choices.py  live lists: installed models, voices, microphones, skills
    server.py   the local web server and its JSON API
    web/        the page itself: plain HTML, CSS and JavaScript
"""
