"""Background threads that run a camera so the GUI never blocks.

BaseWorker holds the shared state (newest frame, counters, error, pending
control changes). CaptureWorker drives the Meade LPI through lpicam_driver,
lpicam_uvc.UvcWorker drives a webcam. The GUI reads frames and counters
through the lock and posts control changes with set_params().
"""
import threading

import numpy as np

import lpicam_driver as drv
from lpicam_frames import FrameAssembler


class BaseWorker:
    kind = "bayer"          # "bayer": 1 plane raw Bayer, "bgr": 3 planes colour

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self.lock = threading.Lock()
        self._params = ()
        self._applied = ()
        self.latest = None        # newest complete frame (numpy array)
        self.counter = 0          # complete frames since start
        self.lum = (0, 0)         # bridge brightness sums (LPI only)
        self.size = (0, 0)        # width, height of the frames
        self.error = None
        self.running = False
        self.frame_sink = None    # optional callable(arr), must not block

    def start(self, params, *args):
        self.stop()
        self._stop.clear()
        with self.lock:
            self.latest = None
            self.counter = 0
            self.error = None
            self._params = self._applied = tuple(params)
        self._thread = threading.Thread(
            target=self._guarded_run, args=(tuple(params),) + args, daemon=True)
        self.running = True
        self._thread.start()

    def stop(self):
        if self._thread is not None and self._thread.is_alive():
            self._stop.set()
            self._thread.join(5)
        self._thread = None
        self.running = False

    def set_params(self, *params):
        with self.lock:
            self._params = tuple(params)

    def pending_change(self):
        """Returns (old, new) once when the wanted parameters changed."""
        with self.lock:
            wanted = self._params
        if wanted == self._applied:
            return None
        old, self._applied = self._applied, wanted
        return old, wanted

    def publish(self, arr, lum=(0, 0)):
        with self.lock:
            self.latest = arr
            self.lum = lum
            self.counter += 1
        sink = self.frame_sink
        if sink is not None:
            sink(arr)

    def _guarded_run(self, *args):
        try:
            self._run(*args)
        except Exception as exc:  # shown in the GUI status line
            self.error = str(exc) or exc.__class__.__name__
        finally:
            self.running = False

    def _run(self, *args):
        raise NotImplementedError


class CaptureWorker(BaseWorker):
    """Meade LPI: params = (exposure, gain), args = (mode,)."""
    kind = "bayer"

    def _run(self, params, mode):
        exposure, gain = params
        cam = drv.LpiCamera()
        try:
            cam.open()
            cam.start(mode=mode, raw=True, alt=8, exposure=exposure, gain=gain)
            width, height = drv.MODES[mode]
            self.size = (width, height)
            need = width * height
            asm = FrameAssembler(need)

            def on_data(data):
                for fr in asm.feed(data):
                    if len(fr.payload) < need:
                        continue
                    arr = np.frombuffer(fr.payload[:need], np.uint8)
                    self.publish(arr.reshape(height, width).copy(),
                                 (fr.lum_inside, fr.lum_outside))

            def should_stop():
                if self._stop.is_set():
                    return True
                change = self.pending_change()
                if change:
                    old, new = change
                    if new[0] != old[0]:
                        cam.set_exposure(new[0])
                    if new[1] != old[1]:
                        cam.set_gain(new[1])
                return False

            cam.run_stream(on_data, should_stop)
        finally:
            cam.close()
