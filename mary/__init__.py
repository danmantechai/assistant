"""MARY: a fully local voice assistant for Windows."""

import warnings

# pywinauto wants a multi-threaded COM apartment, finds COM already single-threaded (comtypes/pycaw),
# falls back to STA and says so. That fallback is fine for everything MARY does.
warnings.filterwarnings("ignore", message="Revert to STA COM threading mode")

__version__ = "0.1.5"
