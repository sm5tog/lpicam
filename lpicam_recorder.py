"""Records a series of SER files of a fixed length, back to back.

Frames arrive from the capture thread through feed() (non-blocking). A
writer thread writes them and starts the next file at a frame boundary
when the wanted number of seconds has passed, so the camera keeps
streaming and no frames are lost between files.
"""
import os
import queue
import re
import threading
import time

from lpicam_ser import SerWriter

NO_FRAME_TIMEOUT = 5.0


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return name or "lpi"


class SequenceRecorder:
    def __init__(self):
        self._q = queue.Queue(maxsize=600)
        self._thread = None
        self._stop = threading.Event()
        self.active = False
        self.state = "idle"         # idle, recording, done, stopped, error
        self.message = ""
        self.seq = 0
        self.total = 0
        self.seq_seconds = 0.0
        self.frames_in_seq = 0
        self.dropped = 0
        self.files = []

    def feed(self, arr):
        """Called from the capture thread for every complete frame."""
        if not self.active:
            return
        try:
            self._q.put_nowait((arr, time.time()))
        except queue.Full:
            self.dropped += 1

    def start(self, folder, name, seconds, count, width, height, color_id,
              observer="", instrument="", telescope="", planes=1):
        os.makedirs(folder, exist_ok=True)
        # about 200 MB of queued frames, whatever the frame size
        self._q = queue.Queue(
            maxsize=max(20, int(200e6 // (width * height * planes))))
        self._stop.clear()
        self.state, self.message = "recording", ""
        self.seq, self.total = 0, count
        self.seq_seconds, self.frames_in_seq = 0.0, 0
        self.dropped = 0
        self.files = []
        self.active = True
        self._thread = threading.Thread(
            target=self._run,
            args=(folder, safe_name(name), seconds, count, width, height,
                  color_id, observer, instrument, telescope, planes),
            daemon=True)
        self._thread.start()

    def request_stop(self):
        """Asks the writer to finish the current file and end (non-blocking)."""
        self._stop.set()

    def stop(self):
        """Like request_stop(), but waits until the file is closed."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(10)

    def _next_frame(self):
        """Next queued frame, or None when asked to stop or the stream died."""
        waited = 0.0
        while True:
            try:
                return self._q.get(timeout=0.2)
            except queue.Empty:
                waited += 0.2
                if self._stop.is_set():
                    return None
                if waited >= NO_FRAME_TIMEOUT:
                    self.state = "error"
                    self.message = "No frames from the camera"
                    return None

    def _run(self, folder, name, seconds, count, width, height, color_id,
             observer, instrument, telescope, planes):
        digits = max(2, len(str(count)))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        carry = None
        try:
            for seq in range(1, count + 1):
                self.seq, self.frames_in_seq, self.seq_seconds = seq, 0, 0.0
                path = os.path.join(
                    folder, "%s_%s_%0*d.ser" % (name, stamp, digits, seq))
                writer = SerWriter(path, width, height, color_id,
                                   observer, instrument, telescope, planes)
                self.files.append(path)
                t_start = None
                try:
                    while True:
                        item = carry or self._next_frame()
                        carry = None
                        if item is None:
                            break
                        arr, t = item
                        if t_start is None:
                            t_start = t
                        elif t - t_start >= seconds:
                            carry = item
                            break
                        writer.write_frame(arr.tobytes(), t)
                        self.frames_in_seq = writer.frames
                        self.seq_seconds = t - t_start
                        if self._stop.is_set():
                            break
                finally:
                    writer.close()
                if self.state == "error" or self._stop.is_set():
                    break
            if self.state == "recording":
                self.state = "stopped" if self._stop.is_set() else "done"
        except Exception as exc:
            self.state, self.message = "error", str(exc) or exc.__class__.__name__
        finally:
            self.active = False
