"""The cameras LPICam can use, and how their controls look to the GUI.

Each source owns its worker thread and translates between the GUI's slider
positions and the camera's own exposure/gain values, which are stored in the
settings dictionary. The GUI only talks to this common interface.
"""
import lpicam_bayer as bayer
import lpicam_driver as drv
from lpicam_uvc import UvcWorker, WEBCAM_SIZES, parse_size
from lpicam_worker import CaptureWorker

LPI_MODES = {0: "640x480", 1: "320x240", 2: "160x120"}
SER_BGR = 101       # SER ColorID for 3 plane BGR


class LpiSource:
    key = "lpi"
    label = "Meade LPI"
    kind = "bayer"
    has_auto = False
    mode_labels = list(LPI_MODES.values())

    def __init__(self):
        self.worker = CaptureWorker()

    def mode_label(self, cfg):
        return LPI_MODES[cfg["mode"]]

    def set_mode(self, cfg, label):
        cfg["mode"] = next(k for k, v in LPI_MODES.items() if v == label)

    def gain_max(self, cfg):
        return drv.GAIN_MAX

    def exp_range(self, cfg):
        return 0, 100, 200          # slider position, cubic scale

    def exp_to_pos(self, cfg):
        return 100.0 * (max(0, cfg["exposure"]) / drv.EXPOSURE_MAX) ** (1 / 3)

    def pos_to_exp(self, cfg, pos):
        cfg["exposure"] = int(round(drv.EXPOSURE_MAX * (pos / 100.0) ** 3))

    def exposure_text(self, cfg):
        return str(cfg["exposure"])

    def gain(self, cfg):
        return cfg["gain"]

    def set_gain(self, cfg, value):
        cfg["gain"] = int(round(value))

    def auto(self, cfg):
        return False

    def params(self, cfg):
        return cfg["exposure"], cfg["gain"]

    def start(self, cfg):
        self.worker.start(self.params(cfg), cfg["mode"])

    def rec_format(self, cfg):
        """width, height, SER ColorID, planes, instrument name."""
        w, h = drv.MODES[cfg["mode"]]
        return w, h, bayer.ser_color_id(cfg["bayer"]), 1, "Meade LPI"


class WebcamSource:
    key = "webcam"
    label = "Webcam"
    kind = "bgr"
    has_auto = True
    mode_labels = WEBCAM_SIZES

    def __init__(self):
        self.worker = UvcWorker()

    def mode_label(self, cfg):
        return cfg["web_size"]

    def set_mode(self, cfg, label):
        cfg["web_size"] = label

    def gain_max(self, cfg):
        return cfg["web_gain_max"]

    def exp_range(self, cfg):
        lo, hi = cfg["web_exp_min"], cfg["web_exp_max"]
        return lo, hi, hi - lo      # exposure steps of 1 (log2 seconds)

    def exp_to_pos(self, cfg):
        return cfg["web_exposure"]

    def pos_to_exp(self, cfg, pos):
        cfg["web_exposure"] = int(round(pos))

    def exposure_text(self, cfg):
        e = cfg["web_exposure"]
        return "%d (%.1f ms)" % (e, 1000.0 * 2.0 ** e)

    def gain(self, cfg):
        return cfg["web_gain"]

    def set_gain(self, cfg, value):
        cfg["web_gain"] = int(round(value))

    def auto(self, cfg):
        return cfg["web_auto"]

    def params(self, cfg):
        return cfg["web_exposure"], cfg["web_gain"], bool(cfg["web_auto"])

    def start(self, cfg):
        w, h = parse_size(cfg["web_size"])
        self.worker.start(self.params(cfg), cfg["web_index"], w, h,
                          cfg["web_format"])

    def rec_format(self, cfg):
        w, h = self.worker.size
        return w, h, SER_BGR, 3, cfg["web_name"] or "Webcam"


def make_sources():
    return {s.key: s for s in (LpiSource(), WebcamSource())}
