"""Reading a window by layout (mary/page.py), on made-up layouts of apps it wasn't written for: a chat app, an email
client, a file manager, a settings dialog, a long document, a browser with its bars. No real window is read."""
import numpy as np

from mary.page import Item, dividers, read, segment

W, H = 1600, 1000


def it(text, x0, y0, x1, y1, role="text", clickable=False):
    return Item(text, role, (x0, y0, x1, y1), clickable)


def bar_and_panels(x_split=500, top=120):
    """Dividers as a picture would give them: a panel line at x_split under a bar `top` px high."""
    return [(x_split, top, H)], [top]


# ------------------------------------------------------------------ a chat app


def chat_app():
    items = [
        it("Chats", 20, 140, 120, 165), it("Search", 20, 180, 300, 205, "edit"),
        it("Kofi  6:19 PM  Meet me at the junction", 20, 230, 480, 290, "list item", True),
        it("Ama  5:28 PM  Thanks boss", 20, 300, 480, 360, "list item", True),
        it("Ama", 560, 140, 640, 165), it("Video call", 1400, 140, 1450, 165, "button", True),
        it("Pray ohh", 540, 200, 700, 230),                       # theirs, left
        it("I sent her 100 on Thursday", 1250, 200, 1580, 230),   # mine, right, at the same height
        it("Eei", 540, 260, 600, 290),
        it("Yh", 1500, 320, 1580, 350),
        it("Today", 1000, 380, 1100, 405),                        # a date in the middle
        it("Hmm", 1480, 430, 1580, 460),
        it("Thanks boss", 540, 500, 700, 530),
        it("Type a message", 560, 940, 1500, 975, "edit"),
    ]
    v, h = bar_and_panels()
    browser = [it("WhatsApp - Edge", 10, 10, 300, 40, "tab item", True), it("web.whatsapp.com", 200, 60, 700, 90, "edit")]
    return segment(items + browser, (W, H), v, h, title="WhatsApp", focus_point=(1000, 955))


def test_a_chat_app_is_read_by_area_with_who_wrote_what():
    page = chat_app()
    assert [r.place for r in page.regions] == ["top bar", "left panel", "main area"]
    main = page.main()
    assert page.focus is main and main.heading == "Ama" and main.chat_like
    assert main.lines[1:8] == ["← Pray ohh", "→ I sent her 100 on Thursday", "← Eei", "→ Yh", "· Today", "→ Hmm",
                               "← Thanks boss"]
    left = page.region("left panel")
    assert not left.chat_like and "Kofi 6:19 PM Meet me at the junction" in left.lines
    assert all("Kofi" not in line for line in main.lines)        # the list never mixes into the chat


def test_the_area_you_work_in_is_read_in_full_and_the_others_in_brief():
    text = chat_app().render()
    assert "[main area: Ama] in full:" in text and "→ I sent her 100 on Thursday" in text
    assert "[left panel: Chats] Chats · Search · Kofi" in text   # the list: one line
    assert "web.whatsapp.com" in text.split("Other areas")[1]   # the browser's bar: only in brief


def test_another_area_is_read_when_asked_for_by_place_or_by_its_words():
    page = chat_app()
    assert page.render("left panel").split("\n")[1].startswith("[left panel: Chats] in full:")
    assert page.region("Kofi") is page.region("left panel")
    assert "No area matches 'calendar'" in page.render("calendar")


# ------------------------------------------------------------------ apps it wasn't written for


def test_an_email_client_list_and_reading_pane():
    items = [it("Inbox", 20, 150, 200, 180), it("Sent", 20, 190, 200, 220),
             it("Bank  Your statement is ready  09:14", 260, 150, 760, 180, "list item", True),
             it("Mum  Sunday lunch?  Yesterday", 260, 190, 760, 220, "list item", True),
             it("Your statement is ready", 820, 150, 1300, 185), it("Dear customer,", 820, 220, 1000, 245),
             it("your October statement can now be viewed.", 820, 255, 1400, 280)]
    page = segment(items, (W, H), [(240, 120, H), (800, 120, H)], [120], focus_point=(900, 230))
    assert [r.place for r in page.regions] == ["left panel 1", "left panel 2", "main area"]
    assert page.focus.place == "main area" and page.focus.heading == "Your statement is ready"
    assert not page.focus.chat_like


def test_a_file_manager_table_keeps_its_rows_whole():
    rows = [it(name, 300, y, 600, y + 24, "list item", True) for name, y in
            (("report.pdf", 200), ("photo.jpg", 240), ("notes.txt", 280))]
    sizes = [it(size, 1450, y, 1560, y + 24) for size, y in (("2 MB", 200), ("3 MB", 240), ("1 KB", 280))]
    page = segment(rows + sizes, (W, H), [(260, 120, H)], [120])
    main = page.main()
    assert not main.chat_like and main.lines == ["report.pdf 2 MB", "photo.jpg 3 MB", "notes.txt 1 KB"]


def test_a_settings_dialog_without_any_dividers():
    items = [it("Bluetooth", 100, 100, 300, 130), it("On", 1300, 100, 1400, 130, "button", True),
             it("Wi-Fi", 100, 160, 300, 190), it("Off", 1300, 160, 1400, 190, "button", True),
             it("Airplane mode", 100, 220, 400, 250), it("Off", 1300, 220, 1400, 250, "button", True)]
    page = segment(items, (W, H))
    assert [r.place for r in page.regions] == ["main area"]
    assert page.regions[0].lines == ["Bluetooth On", "Wi-Fi Off", "Airplane mode Off"]


def test_a_long_document_keeps_its_start_and_its_end():
    lines = [it(f"Paragraph {n} of the report, with some words in it.", 100, 40 + n * 30, 1500, 60 + n * 30)
             for n in range(1, 31)]
    page = segment(lines, (W, H))
    text = read(page.regions[0], budget=600)
    assert text.startswith("Paragraph 1 of") and text.rstrip().endswith("Paragraph 30 of the report, with some "
                                                                        "words in it.")
    assert "lines skipped …" in text and len(text) <= 700


def test_without_dividers_panels_split_on_a_wide_gap_but_a_chat_does_not():
    panels = [it(f"Chat {n}", 20, 100 + n * 60, 380, 140 + n * 60) for n in range(12)] + \
             [it(f"Paragraph {n}", 700, 100 + n * 60, 1500, 140 + n * 60) for n in range(12)]
    assert [r.place for r in segment(panels, (W, H)).regions] == ["left panel", "main area"]
    chat = [it("hi", 40, 100, 200, 130), it("hello", 1300, 160, 1560, 190), it("Today", 700, 220, 900, 245),
            it("how are you", 40, 280, 300, 310), it("good", 1400, 340, 1560, 370)]
    one = segment(chat, (W, H)).regions
    assert len(one) == 1 and one[0].chat_like     # the date in the middle joins both sides


def test_noise_is_dropped_and_pictures_with_words_are_kept():
    items = [it("Kofi: hello there", 100, 100, 600, 160, "list item"), it("Kofi", 110, 105, 200, 125),
             it("hello there", 110, 130, 400, 155), it("msg-dblcheck", 610, 140, 630, 155, "graphic"),
             it("Photo of a beach", 100, 200, 400, 400, "image")]
    lines = segment(items, (W, H)).regions[0].lines
    assert lines == ["Kofi", "hello there", "[image: Photo of a beach]"]


# ------------------------------------------------------------------ dividers in a picture


def test_dividers_are_found_in_a_window_picture():
    pixels = np.full((H, W, 4), 30, np.uint8)
    pixels[:120] = 60                        # a lighter bar on top
    pixels[120:, 500:] = 45                  # the main panel a shade lighter than the list
    pixels[300:310, 600:900] = 200           # some text (not a divider)
    vertical, horizontal = dividers(pixels)
    assert [x for x, _, _ in vertical] == [500] and vertical[0][1] <= 125 and vertical[0][2] >= H - 5
    assert 120 in horizontal
