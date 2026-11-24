"""Settings file (lpicam_settings.json) next to the program."""
import json
import os
import sys

# Next to LPICam.exe when frozen (a onefile exe unpacks to a temp folder),
# otherwise next to the source files.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(APP_DIR, "lpicam_settings.json")

DEFAULTS = {
    "mode": 1,          # 0 = 640x480, 1 = 320x240, 2 = 160x120
    "exposure": 40,     # sensor line exposure 0..8191
    "gain": 20,         # 0..63
    "color": True,
    "bayer": "BGGR",    # true sensor layout: RGGB, GRBG, GBRG, BGGR
    "ontop": False,
    "classic": False,   # normal OS window frame instead of borderless
    "rec_dir": os.path.join(APP_DIR, "captures"),
    "rec_name": "lpi",
    "rec_seconds": 20,
    "rec_count": 1,
    "source": "lpi",    # "lpi" or "webcam"
    "web_index": 0,     # DirectShow device index
    "web_name": "",
    "web_size": "640x480",
    "web_format": "YUY2",   # YUY2 (uncompressed) or MJPG
    "web_exposure": -6,     # log2 of seconds (DirectShow), -6 = 1/64 s
    "web_gain": 0,
    "web_auto": True,       # automatic exposure
    "web_exp_min": -13,     # slider range, unchecked against a C270
    "web_exp_max": -1,
    "web_gain_max": 255,
    "observer": "",     # written into the SER header
    "telescope": "",    # written into the SER header
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
