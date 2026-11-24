"""Lists USB descriptors for the Meade LPI camera (Sonix 0C45:602A).

Step 1 of the LPICam driver: shows interfaces, alternate settings and
endpoint max packet sizes so we know which isochronous alt setting to use.
Needs the camera bound to WinUSB (Zadig) to open it; descriptors of
unbound devices may be incomplete.
"""
import usb1

VID, PID = 0x0C45, 0x602A


def main():
    with usb1.USBContext() as ctx:
        found = False
        for dev in ctx.getDeviceIterator(skip_on_error=True):
            if dev.getVendorID() != VID or dev.getProductID() != PID:
                continue
            found = True
            print(f"Device {VID:04X}:{PID:04X}  bcdUSB={dev.getbcdUSB():04X}  "
                  f"bcdDevice={dev.getbcdDevice():04X}  class={dev.getDeviceClass()}")
            for cfg in dev.iterConfigurations():
                print(f" Config {cfg.getConfigurationValue()}  "
                      f"MaxPower={cfg.getMaxPower()}mA")
                for iface in cfg:
                    for alt in iface:
                        print(f"  Interface {alt.getNumber()} alt "
                              f"{alt.getAlternateSetting()}  class={alt.getClass()}")
                        for ep in alt:
                            addr = ep.getAddress()
                            attrs = ep.getAttributes() & 3
                            kind = {0: "control", 1: "isochronous",
                                    2: "bulk", 3: "interrupt"}[attrs]
                            print(f"    EP 0x{addr:02X} {kind:11s} "
                                  f"maxpacket={ep.getMaxPacketSize()}")
            try:
                h = dev.open()
                print(" Open OK, product:", h.getProduct())
                h.close()
            except usb1.USBError as e:
                print(" Open failed:", e, "(bind WinUSB with Zadig)")
        if not found:
            print("Camera not found")


if __name__ == "__main__":
    main()
