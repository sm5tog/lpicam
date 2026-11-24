"""Writer for SER video files (the standard for planetary stacking).

Layout as implemented in Siril: 178 byte header, uncompressed frames,
then one 8 byte little-endian timestamp per frame (100 ns ticks since
0001-01-01, UTC). Used by AutoStakkert, Registax, PIPP and Siril.
"""
import datetime
import struct
import time

TICKS_1970 = 621355968000000000     # 100 ns ticks from 0001-01-01 to 1970-01-01
HEADER = struct.Struct("<14s7i40s40s40sQQ")
assert HEADER.size == 178
FRAMECOUNT_OFFSET = 38


def _ticks(epoch_seconds):
    return TICKS_1970 + int(round(epoch_seconds * 10_000_000))


def _text(s):
    return s.encode("ascii", "replace")


class SerWriter:
    def __init__(self, path, width, height, color_id,
                 observer="", instrument="", telescope="", planes=1):
        self._f = open(path, "wb")
        self._stamps = []
        self.frame_bytes = width * height * planes
        now = time.time()
        offset = datetime.datetime.now().astimezone().utcoffset().total_seconds()
        self._f.write(HEADER.pack(
            b"LUCAM-RECORDER", 0, color_id, 0, width, height, 8, 0,
            _text(observer), _text(instrument), _text(telescope),
            _ticks(now + offset), _ticks(now)))

    @property
    def frames(self):
        return len(self._stamps)

    def write_frame(self, data, epoch_seconds):
        if len(data) != self.frame_bytes:
            raise ValueError("frame size mismatch")
        self._f.write(data)
        self._stamps.append(_ticks(epoch_seconds))

    def close(self):
        n = len(self._stamps)
        self._f.write(struct.pack("<%dQ" % n, *self._stamps))
        self._f.seek(FRAMECOUNT_OFFSET)
        self._f.write(struct.pack("<I", n))
        self._f.close()
