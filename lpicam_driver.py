"""Userspace driver for the Meade LPI camera (Sonix SN9C102 + HV7131D).

Ported from the Linux gspca driver drivers/media/usb/gspca/sonixb.c
(GPL-2.0-or-later, Copyright Jean-Francois Moine, Michel Xhaard).
USB id 0C45:602A is "SB(HV7131D, 102)" in that driver.

Needs the camera bound to WinUSB (Zadig) and the python package libusb1.
This module only talks to the hardware. Frame parsing is in lpicam_frames.py.
"""
import time

import usb1

VID, PID = 0x0C45, 0x602A
ISO_EP = 0x81

REQ_WRITE = 0x41   # OUT | vendor | interface
REQ_READ = 0xC1    # IN  | vendor | interface
CTRL_TIMEOUT_MS = 500

# image modes: bridge down-scaling (reg 0x18 bits 4-5), width, height
MODES = {0: (640, 480), 1: (320, 240), 2: (160, 120)}

# Bridge registers 0x01..0x19 for HV7131D (initHv7131d in sonixb.c)
BRIDGE_INIT = bytes([
    0x04, 0x03, 0x00, 0x04, 0x00, 0x00, 0x00, 0x80, 0x11, 0x00, 0x00, 0x00,
    0x00, 0x00,
    0x00, 0x00, 0x00, 0x02, 0x02, 0x00,
    0x28, 0x1e, 0x60, 0x8e, 0x42,
])
# Auto exposure window registers 0x1a..0x1f for VGA sensors
BRIDGE_AE = bytes([0x1d, 0x10, 0x05, 0x03, 0x0f, 0x0c])

# hv7131d_sensor_init in sonixb.c: 8 byte i2c write packets
SENSOR_INIT = [
    bytes([0xa0, 0x11, 0x01, 0x04, 0x00, 0x00, 0x00, 0x17]),
    bytes([0xa0, 0x11, 0x02, 0x00, 0x00, 0x00, 0x00, 0x17]),
    bytes([0xa0, 0x11, 0x28, 0x00, 0x00, 0x00, 0x00, 0x17]),
    bytes([0xa0, 0x11, 0x30, 0x30, 0x00, 0x00, 0x00, 0x17]),  # reset level
    bytes([0xa0, 0x11, 0x34, 0x02, 0x00, 0x00, 0x00, 0x17]),  # pixel bias
]

EXPOSURE_MAX = 8191   # sensor line exposure, 0..8191 (gspca default 482)
GAIN_MAX = 63         # 0..63 (gspca default 31)


class LpiError(Exception):
    pass


class LpiCamera:
    def __init__(self):
        self.ctx = None
        self.handle = None
        self.alt = 0
        self._transfers = []
        self._running = False

    # -- connection -------------------------------------------------------
    def open(self):
        self.ctx = usb1.USBContext()
        self.handle = self.ctx.openByVendorIDAndProductID(
            VID, PID, skip_on_error=True)
        if self.handle is None:
            self.ctx.close()
            self.ctx = None
            raise LpiError("Camera 0C45:602A not found or not bound to WinUSB")
        self.handle.claimInterface(0)
        ident = self.reg_r(0x00)
        if ident != 0x10:
            raise LpiError(f"Unexpected bridge id 0x{ident:02X} (expected 0x10)")
        self.reg_w(0x01, [0x09])  # stream off, LED off (sd_init)

    def close(self):
        if self.handle is not None:
            try:
                self.stop()
                self.handle.releaseInterface(0)
            except usb1.USBError:
                pass
            self.handle.close()
            self.handle = None
        if self.ctx is not None:
            self.ctx.close()
            self.ctx = None

    # -- register access --------------------------------------------------
    def reg_w(self, reg, data):
        self.handle.controlWrite(REQ_WRITE, 0x08, reg, 0, bytes(data),
                                 CTRL_TIMEOUT_MS)

    def reg_r(self, reg):
        return self.handle.controlRead(REQ_READ, 0x00, reg, 0, 1,
                                       CTRL_TIMEOUT_MS)[0]

    def i2c_w(self, buf):
        self.reg_w(0x08, buf)
        for _ in range(60):
            time.sleep(0.001)
            status = self.reg_r(0x08)
            if status & 0x04:
                if status & 0x08:
                    raise LpiError(f"i2c error writing {bytes(buf).hex()}")
                return
        raise LpiError("i2c write timeout")

    # -- sensor controls --------------------------------------------------
    def set_exposure(self, value):
        value = max(0, min(EXPOSURE_MAX, int(value)))
        self.i2c_w([0xc0, 0x11, 0x25, value >> 8, value & 0xff, 0, 0, 0x17])

    def set_gain(self, value):
        value = max(0, min(GAIN_MAX, int(value)))
        g = 0x3f - value
        self.i2c_w([0xc0, 0x11, 0x31, g, g, g, 0, 0x17])

    # -- streaming --------------------------------------------------------
    def iso_packet_size(self, alt):
        dev = self.handle.getDevice()
        for cfg in dev.iterConfigurations():
            for iface in cfg:
                for setting in iface:
                    if setting.getAlternateSetting() != alt:
                        continue
                    for ep in setting:
                        if ep.getAddress() == ISO_EP:
                            return ep.getMaxPacketSize()
        raise LpiError(f"alt setting {alt} not found")

    def start(self, mode=0, raw=True, alt=8, exposure=482, gain=31):
        """Programs bridge + sensor and starts the video transfer.

        mode: 0 = 640x480, 1 = 320x240, 2 = 160x120 (bridge down-scaling).
        raw:  True disables the SN9C10x compression, so the frame is plain
              Bayer BGGR data of width*height bytes.
        """
        self.alt = alt
        self.handle.setInterfaceAltSetting(0, alt)

        regs = bytearray(0x32)
        regs[0x01:0x1a] = BRIDGE_INIT
        regs[0x1a:0x20] = BRIDGE_AE
        regs[0x18] |= mode << 4
        if raw:
            regs[0x18] &= ~0x80
        regs[0x10] = 0x00   # bridge gain 1.0 (red/blue)
        regs[0x11] = 0x00   # bridge gain 1.0 (green)

        self.reg_w(0x01, regs[0x01:0x02])
        self.reg_w(0x17, regs[0x17:0x18])
        self.reg_w(0x01, regs[0x01:0x20])
        for packet in SENSOR_INIT:
            self.i2c_w(packet)
        self.reg_w(0x15, regs[0x15:0x17])
        self.reg_w(0x18, regs[0x18:0x19])
        self.reg_w(0x12, regs[0x12:0x13])
        self.reg_w(0x13, regs[0x13:0x14])
        self.reg_w(0x17, regs[0x17:0x18])
        self.reg_w(0x19, regs[0x19:0x1a])
        self.reg_w(0x1c, regs[0x1c:0x20])
        self.reg_w(0x01, regs[0x01:0x02])   # enable video transfer
        self.reg_w(0x18, regs[0x18:0x1a])
        time.sleep(0.02)
        self.set_gain(gain)
        self.set_exposure(exposure)

    def stop(self):
        self._running = False
        for t in self._transfers:
            try:
                t.cancel()
            except usb1.USBError:
                pass
        deadline = time.time() + 1.0
        while any(t.isSubmitted() for t in self._transfers) \
                and time.time() < deadline:
            self.ctx.handleEventsTimeout(0.05)
        self._transfers = []
        if self.handle is not None:
            try:
                self.reg_w(0x01, [0x09])
                self.handle.setInterfaceAltSetting(0, 0)
            except usb1.USBError:
                pass

    def run_stream(self, on_data, should_stop, n_transfers=6, n_packets=32):
        """Reads isochronous data until should_stop() is true.

        on_data(bytes) is called with the payload of each good packet.
        """
        psize = self.iso_packet_size(self.alt)
        self._running = True

        def callback(transfer):
            if self._running:
                for status, data in transfer.iterISO():
                    if status == usb1.TRANSFER_COMPLETED and data:
                        on_data(bytes(data))
                try:
                    transfer.submit()
                except usb1.USBError:
                    pass

        for _ in range(n_transfers):
            t = self.handle.getTransfer(iso_packets=n_packets)
            t.setIsochronous(ISO_EP, n_packets * psize, callback=callback,
                             timeout=2000)
            t.submit()
            self._transfers.append(t)

        while self._running and not should_stop():
            self.ctx.handleEventsTimeout(0.1)
