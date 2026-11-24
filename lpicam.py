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
from tkinter import filedialog

import ee_theme as th
import lpicam_bayer as bayer
import lpicam_driver as drv
from lpicam_recorder import SequenceRecorder
from lpicam_settings import load_settings, save_settings
from lpicam_worker import CaptureWorker

VERSION = "0.2.0"
APP_NAME = "LPICam"
CAPTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")

PREVIEW_W, PREVIEW_H = 640, 480
MODE_LABELS = {0: "640x480", 1: "320x240", 2: "160x120"}
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
        self.cfg["bayer"] = bayer.normalize(self.cfg["bayer"])
        self.worker = CaptureWorker()
        self.recorder = SequenceRecorder()
        self.worker.frame_sink = self.recorder.feed
        self._rec_locked = False
        self._rec_seconds = 0
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
                        "Layout of the colour filter on the sensor. If red and "
                        "blue look swapped, try another. Also stored in "
                        "recorded SER files. Default: BGGR."
                        ).grid(row=0, column=4, padx=(6, 2))
        self.bayer_menu = ctk.CTkOptionMenu(bar, values=list(bayer.LAYOUTS),
                                            width=85, command=self._on_bayer)
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

        rec = ctk.CTkFrame(root, fg_color=th.color("bg_surface"))
        rec.grid(row=2, column=0, padx=8, pady=(0, 8), sticky="ew")
        rec.grid_columnconfigure(1, weight=1)
        self._label_tip(rec, "Folder",
                        "Where the SER files are saved. Default: the captures "
                        "folder next to the program."
                        ).grid(row=0, column=0, padx=6, pady=(6, 2), sticky="w")
        self.rec_dir = ctk.CTkEntry(rec)
        self.rec_dir.insert(0, self.cfg["rec_dir"])
        self.rec_dir.grid(row=0, column=1, sticky="ew", padx=2, pady=(6, 2))
        self.btn_browse = ctk.CTkButton(rec, text="Browse", width=70,
                                        command=self._browse_dir)
        self.btn_browse.grid(row=0, column=2, padx=6, pady=(6, 2))

        row1 = ctk.CTkFrame(rec, fg_color="transparent")
        row1.grid(row=1, column=0, columnspan=3, sticky="ew", padx=6, pady=2)
        self._label_tip(row1, "Name",
                        "File name start. The files get a date, a time and a "
                        "number added, e.g. lpi_20261124_201330_01.ser. "
                        "Default: lpi."
                        ).pack(side="left", padx=(0, 4))
        self.rec_name = ctk.CTkEntry(row1, width=130)
        self.rec_name.insert(0, self.cfg["rec_name"])
        self.rec_name.pack(side="left", padx=(0, 10))
        self._label_tip(row1, "Seconds",
                        "Length of each file in seconds, 1 to 3600. "
                        "Default: 20."
                        ).pack(side="left", padx=(0, 4))
        self.rec_seconds_entry = ctk.CTkEntry(row1, width=60)
        self.rec_seconds_entry.insert(0, str(self.cfg["rec_seconds"]))
        self.rec_seconds_entry.pack(side="left", padx=(0, 10))
        self._label_tip(row1, "Sequences",
                        "Number of files recorded one after the other, 1 to "
                        "999. Default: 1."
                        ).pack(side="left", padx=(0, 4))
        self.rec_count_entry = ctk.CTkEntry(row1, width=60)
        self.rec_count_entry.insert(0, str(self.cfg["rec_count"]))
        self.rec_count_entry.pack(side="left", padx=(0, 10))
        self.btn_rec = ctk.CTkButton(row1, text="Record", width=80,
                                     fg_color=th.color("accent_red"),
                                     command=self._toggle_record)
        self.btn_rec.pack(side="right")
        self.rec_status = ctk.CTkLabel(rec, text="", anchor="w",
                                       text_color=th.color("text_secondary"))
        self.rec_status.grid(row=2, column=0, columnspan=3, sticky="w",
                             padx=6, pady=(2, 6))
        self._rec_inputs = (self.rec_dir, self.rec_name, self.rec_seconds_entry,
                            self.rec_count_entry, self.btn_browse)
        self._rec_locked_widgets = (self.exp_slider, self.gain_slider,
                                    self.mode_menu, self.bayer_menu,
                                    self.btn_run) + self._rec_inputs

        # drag in the picture and the free surfaces (not on sliders)
        for w in (self.preview, bar, rec):
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
            rgb = cv2.cvtColor(
                arr, bayer.demosaic_code(self.cfg["bayer"]))[:, :, ::-1]
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
        self._update_recording_status()
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
                        cv2.cvtColor(arr, bayer.demosaic_code(self.cfg["bayer"])))
        self.status.configure(text="Saved " + os.path.basename(base),
                              text_color=th.color("accent_green_bright"))

    # ----- recording -----------------------------------------------------------
    def _browse_dir(self):
        folder = filedialog.askdirectory(initialdir=self.rec_dir.get() or None,
                                         title="Folder for the SER files")
        if folder:
            self.rec_dir.delete(0, "end")
            self.rec_dir.insert(0, os.path.normpath(folder))

    def _rec_message(self, text, ok=False):
        self.rec_status.configure(
            text=text, text_color=th.color("accent_green_bright" if ok
                                           else "accent_orange_bright"))

    def _toggle_record(self):
        if self.recorder.active:
            self.recorder.request_stop()
            self.btn_rec.configure(state="disabled", text="Stopping")
            return
        if not self.worker.running or self.worker.counter == 0:
            self._rec_message("Start the camera first")
            return
        try:
            seconds = float(self.rec_seconds_entry.get().replace(",", "."))
            count = int(self.rec_count_entry.get())
        except ValueError:
            self._rec_message("Seconds and Sequences must be numbers")
            return
        if not 1 <= seconds <= 3600 or not 1 <= count <= 999:
            self._rec_message("Seconds must be 1 to 3600, Sequences 1 to 999")
            return
        folder = self.rec_dir.get().strip()
        if not folder:
            self._rec_message("Choose a folder")
            return
        self.cfg.update(rec_dir=folder, rec_name=self.rec_name.get(),
                        rec_seconds=seconds, rec_count=count)
        save_settings(self.cfg)
        width, height = drv.MODES[self.cfg["mode"]]
        try:
            self.recorder.start(
                folder, self.cfg["rec_name"], seconds, count, width, height,
                bayer.ser_color_id(self.cfg["bayer"]),
                self.cfg["observer"], "Meade LPI", self.cfg["telescope"])
        except OSError as exc:
            self._rec_message("Cannot use the folder: %s" % exc)
            return
        self._rec_seconds = seconds
        self._set_recording_ui(True)

    def _set_recording_ui(self, locked):
        self._rec_locked = locked
        state = "disabled" if locked else "normal"
        for widget in self._rec_locked_widgets:
            widget.configure(state=state)
        self.btn_rec.configure(state="normal",
                               text="Stop" if locked else "Record")

    def _update_recording_status(self):
        rec = self.recorder
        if rec.active:
            text = "Recording %d/%d   %.1f/%g s   %d frames" % (
                rec.seq, rec.total, rec.seq_seconds, self._rec_seconds,
                rec.frames_in_seq)
            if rec.dropped:
                text += "   %d dropped" % rec.dropped
            self._rec_message(text, ok=True)
        elif self._rec_locked:
            self._set_recording_ui(False)
            n = len(rec.files)
            if rec.state == "error":
                self._rec_message("Error: %s (%d files written)" % (rec.message, n))
            elif rec.state == "stopped":
                self._rec_message("Stopped, %d files saved" % n, ok=True)
            else:
                self._rec_message("Done, %d files saved in %s" % (
                    n, os.path.dirname(rec.files[-1])), ok=True)

    # ----- shutdown ------------------------------------------------------------
    def _on_close(self):
        self.recorder.stop()
        self.worker.stop()
        save_settings(self.cfg)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    LpiCamApp().run()


if __name__ == "__main__":
    main()
