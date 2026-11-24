"""LPICam (by SM5K): live view and capture for the old Meade LPI camera.

Own userspace driver (WinUSB + libusb), see lpicam_driver.py.
Borderless window by default: drag in the picture, right-click for the menu.
"""
import os
import time
import tkinter as tk

import customtkinter as ctk
import cv2
from PIL import Image

import ee_theme as th
import lpicam_driver as drv
from lpicam_settings import load_settings, save_settings
from lpicam_worker import CaptureWorker

VERSION = "0.1.0"
APP_NAME = "LPICam"
CAPTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")

PREVIEW_W, PREVIEW_H = 640, 480
MODE_LABELS = {0: "640x480", 1: "320x240", 2: "160x120"}
BAYER = {"RG": cv2.COLOR_BAYER_RG2BGR, "BG": cv2.COLOR_BAYER_BG2BGR,
         "GR": cv2.COLOR_BAYER_GR2BGR, "GB": cv2.COLOR_BAYER_GB2BGR}
TICK_MS = 40


def exposure_from_slider(pos):
    """Slider 0..100 -> sensor exposure 0..8191 (cubic, fine steps when short)."""
    return int(round(drv.EXPOSURE_MAX * (pos / 100.0) ** 3))


def slider_from_exposure(value):
    return 100.0 * (max(0, value) / drv.EXPOSURE_MAX) ** (1.0 / 3.0)


class _Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event):
        if self.tip is not None:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (self.widget.winfo_rootx() + 8,
                                         self.widget.winfo_rooty()
                                         + self.widget.winfo_height() + 6))
        tk.Label(self.tip, text=self.text, justify="left", wraplength=320,
                 bg=th.color("bg_control"), fg=th.color("text_primary"),
                 relief="solid", borderwidth=1, padx=6, pady=3).pack()

    def _hide(self, _event):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class LpiCamApp:
    def __init__(self):
        th.setup()
        self.cfg = load_settings()
        self.worker = CaptureWorker()
        self.root = ctk.CTk(fg_color=th.color("bg_app"))
        self.root.title(f"{APP_NAME} v{VERSION}")
        self._seen = 0
        self._fps_t = time.time()
        self._fps_n = 0
        self._fps = 0.0
        self._photo = None
        self._about = None
        self._build_ui()
        self._apply_window_style()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_camera()
        self.root.after(TICK_MS, self._tick)

    # ----- UI ----------------------------------------------------------------
    def _label_tip(self, parent, text, tip):
        lbl = ctk.CTkLabel(parent, text=text + " ⓘ", cursor="hand2",
                           text_color=th.color("text_secondary"),
                           font=th.font("body"))
        _Tooltip(lbl, tip)
        return lbl

    def _build_ui(self):
        root = self.root
        self.preview = ctk.CTkLabel(root, text="Starting camera...",
                                    width=PREVIEW_W, height=PREVIEW_H,
                                    fg_color=th.color("bg_black"),
                                    text_color=th.color("text_secondary"))
        self.preview.grid(row=0, column=0, padx=8, pady=(8, 4))

        bar = ctk.CTkFrame(root, fg_color=th.color("bg_surface"))
        bar.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="ew")
        bar.grid_columnconfigure(2, weight=1)
        self.bar = bar

        self.btn_run = ctk.CTkButton(bar, text="Stop", width=70,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, padx=6, pady=6)

        self._label_tip(bar, "Mode",
                        "Image size. Default: 320x240. 640x480 does not work "
                        "yet (see TODO)."
                        ).grid(row=0, column=1, padx=(6, 2))
        self.mode_menu = ctk.CTkOptionMenu(
            bar, values=[MODE_LABELS[0], MODE_LABELS[1], MODE_LABELS[2]],
            width=100, command=self._on_mode)
        self.mode_menu.set(MODE_LABELS[self.cfg["mode"]])
        self.mode_menu.grid(row=0, column=2, sticky="w", padx=2)

        self.color_var = ctk.BooleanVar(value=self.cfg["color"])
        ctk.CTkCheckBox(bar, text="Color", variable=self.color_var,
                        command=self._on_color, width=60
                        ).grid(row=0, column=3, padx=6)
        self._label_tip(bar, "Bayer",
                        "Order of the colour filter on the sensor. If red and "
                        "blue look swapped, try another. Default: RG."
                        ).grid(row=0, column=4, padx=(6, 2))
        self.bayer_menu = ctk.CTkOptionMenu(bar, values=list(BAYER), width=70,
                                            command=self._on_bayer)
        self.bayer_menu.set(self.cfg["bayer"])
        self.bayer_menu.grid(row=0, column=5, padx=(2, 6))

        self._label_tip(bar, "Exposure",
                        "Sensor line exposure 0 to 8191, cubic scale. "
                        "Default: 40 (room light). Higher = brighter and "
                        "slower frames."
                        ).grid(row=1, column=0, columnspan=2, padx=6, sticky="w")
        self.exp_slider = ctk.CTkSlider(bar, from_=0, to=100, number_of_steps=200,
                                        command=self._on_exposure)
        self.exp_slider.set(slider_from_exposure(self.cfg["exposure"]))
        self.exp_slider.grid(row=1, column=2, columnspan=3, sticky="ew", padx=6)
        self.exp_val = ctk.CTkLabel(bar, text="", width=50)
        self.exp_val.grid(row=1, column=5, padx=6)

        self._label_tip(bar, "Gain",
                        "Sensor gain 0 to 63. Default: 20. Higher gain also "
                        "adds noise."
                        ).grid(row=2, column=0, columnspan=2, padx=6, sticky="w")
        self.gain_slider = ctk.CTkSlider(bar, from_=0, to=drv.GAIN_MAX,
                                         number_of_steps=drv.GAIN_MAX,
                                         command=self._on_gain)
        self.gain_slider.set(self.cfg["gain"])
        self.gain_slider.grid(row=2, column=2, columnspan=3, sticky="ew", padx=6)
        self.gain_val = ctk.CTkLabel(bar, text="", width=50)
        self.gain_val.grid(row=2, column=5, padx=6)

        ctk.CTkButton(bar, text="Save frame", width=90, command=self._save_frame
                      ).grid(row=3, column=0, columnspan=2, padx=6, pady=6)
        self.ontop_var = ctk.BooleanVar(value=self.cfg["ontop"])
        ctk.CTkCheckBox(bar, text="On top", variable=self.ontop_var,
                        command=self._on_ontop, width=70
                        ).grid(row=3, column=2, sticky="w", padx=6)
        self.status = ctk.CTkLabel(bar, text="", anchor="e",
                                   text_color=th.color("text_secondary"))
        self.status.grid(row=3, column=3, columnspan=3, sticky="e", padx=6)
        self._refresh_value_labels()

        # drag in the picture and the free surfaces (not on sliders)
        for w in (self.preview, bar):
            w.bind("<Button-1>", self._on_press, add="+")
            w.bind("<B1-Motion>", self._on_drag, add="+")
            w.bind("<ButtonRelease-1>", self._on_release, add="+")
        root.bind("<Button-3>", self._on_win_menu)

    def _apply_window_style(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        if not self.cfg["classic"]:
            self.root.overrideredirect(True)
        x, y = self.cfg.get("x"), self.cfg.get("y")
        geo = f"{w}x{h}" + (f"+{x}+{y}" if x is not None and y is not None else "")
        self.root.geometry(geo)
        self.root.attributes("-topmost", bool(self.cfg["ontop"]))

    # ----- borderless drag + menu --------------------------------------------
    def _on_press(self, event):
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _on_drag(self, event):
        if self.cfg["classic"]:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        if abs(dx) > 5 or abs(dy) > 5:
            self._moved = True
        if self._moved:
            self.root.geometry("+%d+%d" % (self._win_origin[0] + dx,
                                           self._win_origin[1] + dy))

    def _on_release(self, _event):
        if getattr(self, "_moved", False):
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            save_settings(self.cfg)

    def _on_win_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        classic = tk.BooleanVar(value=self.cfg["classic"])
        menu.add_checkbutton(label="Classic window (next start)",
                             variable=classic,
                             command=lambda: self._set_classic(classic.get()))
        menu.add_command(label="Open captures folder", command=self._open_captures)
        menu.add_separator()
        menu.add_command(label="About...", command=self._open_about)
        menu.add_separator()
        menu.add_command(label="Exit", command=self._on_close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_classic(self, value):
        self.cfg["classic"] = bool(value)
        save_settings(self.cfg)

    def _open_captures(self):
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        os.startfile(CAPTURE_DIR)

    def _open_about(self):
        if self._about is None:
            win = ctk.CTkToplevel(self.root, fg_color=th.color("bg_surface"))
            win.title("About")
            win.resizable(False, False)
            ctk.CTkLabel(win, text=f"{APP_NAME} v{VERSION}",
                         font=th.font("body", bold=True)).pack(padx=30, pady=(18, 4))
            ctk.CTkLabel(win, text="Meade LPI camera (Sonix SN9C102 + HV7131D)\n"
                         "Own WinUSB/libusb driver, ported from Linux gspca sonixb.",
                         text_color=th.color("text_secondary")
                         ).pack(padx=30, pady=4)
            ctk.CTkButton(win, text="Close", width=80,
                          command=win.withdraw).pack(pady=(8, 16))
            win.protocol("WM_DELETE_WINDOW", win.withdraw)
            self._about = win
        self._about.deiconify()
        self._about.lift()

    # ----- controls ------------------------------------------------------------
    def _refresh_value_labels(self):
        self.exp_val.configure(text=str(self.cfg["exposure"]))
        self.gain_val.configure(text=str(self.cfg["gain"]))

    def _on_exposure(self, pos):
        self.cfg["exposure"] = exposure_from_slider(pos)
        self._refresh_value_labels()
        self.worker.set_params(self.cfg["exposure"], self.cfg["gain"])

    def _on_gain(self, value):
        self.cfg["gain"] = int(round(value))
        self._refresh_value_labels()
        self.worker.set_params(self.cfg["exposure"], self.cfg["gain"])

    def _on_mode(self, label):
        for k, v in MODE_LABELS.items():
            if v == label:
                self.cfg["mode"] = k
        save_settings(self.cfg)
        self._start_camera()

    def _on_color(self):
        self.cfg["color"] = bool(self.color_var.get())
        save_settings(self.cfg)
        self._seen = -1   # force redraw of the current frame

    def _on_bayer(self, value):
        self.cfg["bayer"] = value
        save_settings(self.cfg)
        self._seen = -1

    def _on_ontop(self):
        self.cfg["ontop"] = bool(self.ontop_var.get())
        self.root.attributes("-topmost", self.cfg["ontop"])
        save_settings(self.cfg)

    def _start_camera(self):
        self._seen = 0
        self._fps_n = 0
        self.worker.start(self.cfg["mode"], self.cfg["exposure"], self.cfg["gain"])
        self.btn_run.configure(text="Stop")

    def _toggle_run(self):
        if self.worker.running:
            self.worker.stop()
            self.btn_run.configure(text="Start")
        else:
            self._start_camera()

    # ----- frames --------------------------------------------------------------
    def _render(self, arr):
        if self.cfg["color"]:
            rgb = cv2.cvtColor(arr, BAYER[self.cfg["bayer"]])[:, :, ::-1]
            img = Image.fromarray(rgb)
        else:
            img = Image.fromarray(arr)
        scale = max(1, PREVIEW_W // img.width)
        return img.resize((img.width * scale, img.height * scale), Image.NEAREST)

    def _tick(self):
        w = self.worker
        with w.lock:
            counter, arr, lum = w.counter, w.latest, w.lum
        if arr is not None and counter != self._seen:
            if counter > self._seen > 0:
                self._fps_n += counter - self._seen
            img = self._render(arr)
            self._photo = ctk.CTkImage(light_image=img, dark_image=img,
                                       size=(PREVIEW_W, PREVIEW_H))
            self.preview.configure(image=self._photo, text="")
            self._seen = counter
            self._mean = float(arr.mean())
        now = time.time()
        if now - self._fps_t >= 1.0:
            self._fps = self._fps_n / (now - self._fps_t)
            self._fps_n, self._fps_t = 0, now
        if w.error:
            self.status.configure(text="Error: " + w.error,
                                  text_color=th.color("accent_red"))
            self.btn_run.configure(text="Start")
        elif w.running and arr is not None:
            self.status.configure(
                text="%.1f fps   mean %.0f" % (self._fps, getattr(self, "_mean", 0)),
                text_color=th.color("text_secondary"))
        self.root.after(TICK_MS, self._tick)

    def _save_frame(self):
        with self.worker.lock:
            arr = None if self.worker.latest is None else self.worker.latest.copy()
        if arr is None:
            return
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        base = os.path.join(CAPTURE_DIR, time.strftime("lpi_%Y%m%d_%H%M%S"))
        cv2.imwrite(base + "_raw.png", arr)
        if self.cfg["color"]:
            cv2.imwrite(base + "_color.png",
                        cv2.cvtColor(arr, BAYER[self.cfg["bayer"]]))
        self.status.configure(text="Saved " + os.path.basename(base),
                              text_color=th.color("accent_green_bright"))

    # ----- shutdown ------------------------------------------------------------
    def _on_close(self):
        self.worker.stop()
        save_settings(self.cfg)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    LpiCamApp().run()


if __name__ == "__main__":
    main()
