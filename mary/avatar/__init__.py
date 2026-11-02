"""A persona's face (Phase 6): a still photo, checked, turned into a small set of prepared images (eyes open and closed,
a few slight head turns, nine mouth shapes) that the HUD shows instead of its rings while that persona is active. There is
no neural video: everything is prepared once, on this PC, and at run time the HUD only swaps and blends prepared images.

    store.py     where the face lives (user/personas/<persona>/face/), on or off, erased for good, packed for a card
    landmarks.py finding the face and its dense landmarks (MediaPipe Face Landmarker, Apache-2.0)
    check.py     is this photo good enough, and if not, why not (one plain sentence per reason)
    prepare.py   the prepared images, made by warping the photo's own pixels
    visemes.py   the mouth shapes of a stretch of speech, from its audio (the same for every voice)
    director.py  the animation script: blinks, head, attentive listening, mouth cues; the page only plays it

Everything private stays in the persona's own folder under user/ (never published). Nothing here sends anything anywhere.
"""
