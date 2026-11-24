"""Webcam (UVC, e.g. Logitech C270) through OpenCV and DirectShow.

Frames are BGR, 8 bit. Exposure uses the DirectShow convention (log2 of
seconds, e.g. -6 = 1/64 s). The property values for manual/auto exposure
follow the usual OpenCV DirectShow convention and have not been checked
against a C270 yet (see DOCUMENTATION.md).
"""
import time

import cv2

from lpicam_worker import BaseWorker

try:    # keep OpenCV's backend warnings out of the console
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
except Exception:
    pass

WEBCAM_SIZES = ["640x480", "1280x720", "960x720", "320x240"]
WEBCAM_FORMATS = ["YUY2", "MJPG"]   # YUY2 is uncompressed, MJPG is faster
AUTO_EXPOSURE_ON = 0.75             # DirectShow convention
AUTO_EXPOSURE_OFF = 0.25


def parse_size(label):
    w, h = label.lower().split("x")
    return int(w), int(h)


def list_webcams():
    """Names of the DirectShow video devices, in OpenCV index order.

    Runs in a background thread (COM). Falls back to probing indices 0 to 3.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        if names:
            return list(names)
    except Exception:
        pass
    found = []
    for index in range(4):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            found.append("Webcam %d" % index)
        cap.release()
    return found


class UvcWorker(BaseWorker):
    """params = (exposure, gain, auto), args = (index, width, height, fourcc)."""
    kind = "bgr"

    @staticmethod
    def _apply(cap, params):
        exposure, gain, auto = params
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE,
                AUTO_EXPOSURE_ON if auto else AUTO_EXPOSURE_OFF)
        if not auto:
            cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
        cap.set(cv2.CAP_PROP_GAIN, gain)

    @staticmethod
    def _open(index):
        """DirectShow first, Media Foundation as a fallback."""
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                return cap
            cap.release()
        return None

    def _run(self, params, index, width, height, fourcc):
        cap = self._open(index)
        if cap is None:
            raise RuntimeError("Cannot open webcam %d" % index)
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self._apply(cap, params)
            bad = 0
            while not self._stop.is_set():
                change = self.pending_change()
                if change:
                    self._apply(cap, change[1])
                ok, frame = cap.read()
                if not ok or frame is None:
                    bad += 1
                    if bad > 100:
                        raise RuntimeError("Webcam stopped sending frames")
                    time.sleep(0.02)
                    continue
                bad = 0
                self.size = (frame.shape[1], frame.shape[0])
                self.publish(frame)
        finally:
            cap.release()
