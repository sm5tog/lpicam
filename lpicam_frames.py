"""Splits the SN9C10x isochronous byte stream into frames.

Each frame starts with the marker FF FF 00 C4 C4 96 followed by 6 header
bytes (unknown, size/compression, 2 bytes brightness sum inside the auto
exposure window, 2 bytes brightness sum outside it), 12 bytes in total.
Layout taken from find_sof() in the Linux gspca sonixb driver.
"""
SOF = b"\xff\xff\x00\xc4\xc4\x96"
HEADER_LEN = 12


class Frame:
    def __init__(self, payload, header):
        self.payload = payload
        self.header = header
        self.lum_inside = header[2] + (header[3] << 8)
        self.lum_outside = header[4] + (header[5] << 8)


class FrameAssembler:
    def __init__(self, max_frame_bytes):
        self.buf = bytearray()
        self.max_buf = max_frame_bytes * 3 + 4096

    def feed(self, data):
        """Adds stream bytes, returns the list of Frames completed by them."""
        self.buf += data
        frames = []
        while True:
            start = self.buf.find(SOF)
            if start < 0:
                del self.buf[:-(len(SOF) - 1)]
                break
            if start > 0:
                del self.buf[:start]
            nxt = self.buf.find(SOF, len(SOF))
            if nxt < 0:
                break
            raw = bytes(self.buf[:nxt])
            del self.buf[:nxt]
            if len(raw) >= HEADER_LEN:
                frames.append(Frame(raw[HEADER_LEN:], raw[len(SOF):HEADER_LEN]))
        if len(self.buf) > self.max_buf:
            self.buf.clear()
        return frames
