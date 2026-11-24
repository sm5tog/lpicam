"""Bayer layouts: true sensor pattern -> OpenCV demosaic code + SER ColorID.

OpenCV names its codes after a shifted position, so a true BGGR sensor is
decoded with COLOR_BAYER_RG2BGR (checked with a synthetic test on
2026-11-24: RGGB->BG, GRBG->GB, GBRG->GR, BGGR->RG). SER ColorID values
(8 to 11) come from the SER format as implemented in Siril.
"""
import cv2

LAYOUTS = {
    "RGGB": (cv2.COLOR_BAYER_BG2BGR, 8),
    "GRBG": (cv2.COLOR_BAYER_GB2BGR, 9),
    "GBRG": (cv2.COLOR_BAYER_GR2BGR, 10),
    "BGGR": (cv2.COLOR_BAYER_RG2BGR, 11),   # what the gspca driver reports
}
DEFAULT_LAYOUT = "BGGR"

# Settings written by 0.1.0 stored the OpenCV name, which meant:
_LEGACY = {"RG": "BGGR", "BG": "RGGB", "GR": "GBRG", "GB": "GRBG"}


def normalize(name):
    if name in LAYOUTS:
        return name
    return _LEGACY.get(name, DEFAULT_LAYOUT)


def demosaic_code(name):
    return LAYOUTS[normalize(name)][0]


def ser_color_id(name):
    return LAYOUTS[normalize(name)][1]
