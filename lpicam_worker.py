"""Background thread that runs the camera so the GUI never blocks.

All libusb calls happen in this one thread. The GUI reads the newest frame
and counters through the lock and posts exposure/gain changes with
set_params().
"""
import threading

import numpy as np

import lpicam_driver as drv
from lpicam_frames import FrameAssembler


class CaptureWorker:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self.lock = threading.Lock()
        self._params = (0, 0)
        self._applied = (0, 0)
        self.latest = None        # newest complete frame (height x width uint8)
        self.counter = 0          # complete frames since start
        self.lum = (0, 0)         # bridge brightness sums (inside, outside)
        self.error = None
        self.running = False

    def start(self, mode, exposure, gain):
        self.stop()
        self._stop.clear()
        with self.lock:
            self.latest = None
            self.counter = 0
            self.error = None
            self._params = self._applied = (exposure, gain)
        self._thread = threading.Thread(
            target=self._run, args=(mode, exposure, gain), daemon=True)
        self.running = True
        self._thread.start()

    def stop(self):
        if self._thread is not None and self._thread.is_alive():
            self._stop.set()
            self._thread.join(5)
        self._thread = None
        self.running = False

    def set_params(self, exposure, gain):
        with self.lock:
            self._params = (exposure, gain)

    def _run(self, mode, exposure, gain):
        cam = drv.LpiCamera()
        try:
            cam.open()
            cam.start(mode=mode, raw=True, alt=8, exposure=exposure, gain=gain)
            width, height = drv.MODES[mode]
            need = width * height
            asm = FrameAssembler(need)

            def on_data(data):
                for fr in asm.feed(data):
                    if len(fr.payload) < need:
                        continue
                    arr = np.frombuffer(fr.payload[:need], np.uint8)
                    arr = arr.reshape(height, width).copy()
                    with self.lock:
                        self.latest = arr
                        self.lum = (fr.lum_inside, fr.lum_outside)
                        self.counter += 1

            def should_stop():
                if self._stop.is_set():
                    return True
                with self.lock:
                    wanted = self._params
                if wanted != self._applied:
                    if wanted[0] != self._applied[0]:
                        cam.set_exposure(wanted[0])
                    if wanted[1] != self._applied[1]:
                        cam.set_gain(wanted[1])
                    self._applied = wanted
                return False

            cam.run_stream(on_data, should_stop)
        except Exception as exc:  # shown in the GUI status line
            self.error = str(exc) or exc.__class__.__name__
        finally:
            cam.close()
            self.running = False
