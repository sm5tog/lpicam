"""Command line test: grabs a few frames from the Meade LPI and saves them.

Usage: python lpicam_capture.py [--mode 0|1|2] [--alt 8] [--frames 3]
                                [--exposure 482] [--gain 31]
Frames are saved in the captures folder next to this script.
"""
import argparse
import os
import time

import cv2
import numpy as np

import lpicam_driver as drv
from lpicam_frames import FrameAssembler

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", type=int, default=0, choices=[0, 1, 2])
    ap.add_argument("--alt", type=int, default=8)
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--exposure", type=int, default=482)
    ap.add_argument("--gain", type=int, default=31)
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    width, height = drv.MODES[args.mode]
    need = width * height
    os.makedirs(OUT_DIR, exist_ok=True)

    asm = FrameAssembler(need)
    good = []
    lengths = []
    total = [0]

    def on_data(data):
        total[0] += len(data)
        for fr in asm.feed(data):
            lengths.append(len(fr.payload))
            if len(fr.payload) >= need:
                good.append(fr)

    cam = drv.LpiCamera()
    t0 = time.time()
    try:
        cam.open()
        print(f"Opened. Starting mode {args.mode} {width}x{height} raw, "
              f"alt {args.alt} (packet {cam.iso_packet_size(args.alt)} bytes)")
        cam.start(mode=args.mode, raw=True, alt=args.alt,
                  exposure=args.exposure, gain=args.gain)
        cam.run_stream(on_data,
                       lambda: len(good) >= args.frames
                       or time.time() - t0 > args.timeout)
    finally:
        cam.close()

    secs = time.time() - t0
    print(f"{total[0]} bytes in {secs:.1f} s, {len(lengths)} frames seen, "
          f"{len(good)} complete (need {need} bytes)")
    if lengths:
        print("Frame payload lengths:", sorted(set(lengths))[:10],
              "min", min(lengths), "max", max(lengths))
    for i, fr in enumerate(good[:args.frames]):
        img = np.frombuffer(fr.payload[:need], np.uint8).reshape(height, width)
        base = os.path.join(OUT_DIR, f"frame_m{args.mode}_{i}")
        cv2.imwrite(base + "_gray.png", img)
        cv2.imwrite(base + "_color.png",
                    cv2.cvtColor(img, cv2.COLOR_BAYER_RG2BGR))
        print(f"saved {base}_gray.png / _color.png  "
              f"lum in/out {fr.lum_inside}/{fr.lum_outside} "
              f"mean {img.mean():.1f}")


if __name__ == "__main__":
    main()
