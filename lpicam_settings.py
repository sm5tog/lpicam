"""Settings file (lpicam_settings.json) next to the program."""
import json
import os

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "lpicam_settings.json")

DEFAULTS = {
    "mode": 1,          # 0 = 640x480, 1 = 320x240, 2 = 160x120
    "exposure": 40,     # sensor line exposure 0..8191
    "gain": 20,         # 0..63
    "color": True,
    "bayer": "RG",      # RG, BG, GR, GB (OpenCV demosaic name)
    "ontop": False,
    "classic": False,   # normal OS window frame instead of borderless
}


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save_settings(d):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
