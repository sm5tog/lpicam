# LPICam

Live view and capture for the old **Meade LPI** (Lunar Planetary Imager) camera on Windows 10 and 11, plus ordinary webcams such as the Logitech C270. The Meade camera has had no working driver since Windows XP, so LPICam talks to it directly through its own userspace driver (WinUSB and libusb). No Meade software is needed.

By SM5K / SM5TOG.

## What it does

- Live view with exposure, gain, colour and a choice of image size.
- Records one or many video files of a chosen length, back to back, as **SER** files (the standard for planetary stacking, read by AutoStakkert, Registax, PIPP and Siril). Folder, file name, seconds and number of sequences are set in the window.
- Saves still frames as PNG.
- Borderless dark window in the style of the other SM5K programs. Drag in the picture, right-click for the menu.

## The cameras

| Camera | How it connects | Status |
|---|---|---|
| Meade LPI (USB `0C45:602A`, Sonix SN9C102 bridge with HV7131D sensor) | Own driver, needs the camera bound to WinUSB once (with Zadig) | 320x240 and 160x120 raw work on hardware. 640x480 does not work yet. |
| Webcam (UVC, e.g. Logitech C270) | OpenCV and DirectShow, no driver needed | Implemented and tested with a simulated source. **Not yet tested with a real C270.** |

The Bayer layout of the Meade sensor is assumed to be BGGR (as in the Linux driver). The menu lets you change it if red and blue look swapped.

## Run from source

Python 3.14 on Windows:

```
pip install libusb1 numpy opencv-python customtkinter pillow pygrabber
python lpicam.py
```

For the Meade camera: connect it, start [Zadig](https://zadig.akeo.ie/), choose Options, List All Devices, select "USB camera" (USB ID `0C45 602A`), select **WinUSB** and click Install Driver. This is done once per computer, and again if you change USB port.

Settings and captures are stored next to the program (`lpicam_settings.json` and `captures\`).

## Build the exe

```
pip install pyinstaller
python -m PyInstaller lpicam.spec --noconfirm
```

The result is `dist\LPICam.exe`. It is not code signed, so Windows SmartScreen may warn the first time.

## Files

| File | Purpose |
|---|---|
| `lpicam.py` | GUI |
| `lpicam_driver.py` | Meade LPI driver (control transfers, i2c, isochronous streaming) |
| `lpicam_frames.py` | Splits the isochronous stream into frames |
| `lpicam_worker.py`, `lpicam_uvc.py` | Camera threads (Meade LPI, webcam) |
| `lpicam_sources.py` | Per camera controls for the GUI |
| `lpicam_ser.py`, `lpicam_recorder.py` | SER file writer and the sequence recorder |
| `lpicam_bayer.py` | Bayer layouts, demosaicing and SER colour ids |
| `lpicam_capture.py`, `lpicam_probe.py` | Command line test tools for the Meade camera |

## Credits and license

LPICam is licensed under **GPL-2.0-or-later** (see `LICENSE`).

The Meade LPI protocol (register map, start sequence, frame format) is ported from the Linux **gspca sonixb** driver (`drivers/media/usb/gspca/sonixb.c`), Copyright (C) 2009-2011 Jean-Francois Moine, Copyright (C) 2003 2004 Michel Xhaard, GPL-2.0-or-later. The SER layout follows the format as implemented in Siril.
