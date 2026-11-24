"""LPICam (by SM5K): live view and capture for the Meade LPI and webcams.

Meade LPI through an own userspace driver (WinUSB + libusb, lpicam_driver.py),
webcams such as the Logitech C270 through OpenCV (lpicam_uvc.py).
Borderless window by default: drag in the picture, right-click for the menu.
"""
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
import cv2
from PIL import Image

import ee_theme as th
import lpicam_bayer as bayer
from lpicam_recorder import SequenceRecorder
from lpicam_settings import APP_DIR, load_settings, save_settings
from lpicam_sources import make_sources
from lpicam_uvc import WEBCAM_FORMATS, list_webcams
from lpicam_widgets import label_tip

VERSION = "0.3.0"
APP_NAME = "LPICam"
CAPTURE_DIR = os.path.join(APP_DIR, "captures")

PREVIEW_W, PREVIEW_H = 640, 480
TICK_MS = 40


class LpiCamApp:
    def __init__(self):
        th.setup()
        self.cfg = load_settings()
        self.cfg["bayer"] = bayer.normalize(self.cfg["bayer"])
        self.sources = make_sources()
        if self.cfg["source"] not in self.sources:
            self.cfg["source"] = "lpi"
        self.src = self.sources[self.cfg["source"]]
        self.recorder = SequenceRecorder()
        for source in self.sources.values():
            source.worker.frame_sink = self.recorder.feed
        self._rec_locked = False
        self._rec_seconds = 0
        self._web_names = []
        self._web_thread = None
        self._web_result = None
        self.root = ctk.CTk(fg_color=th.color("bg_app"))
        self.root.title(f"{APP_NAME} v{VERSION}")
        self._seen = 0
        self._fps_t = time.time()
        self._fps_n = 0
        self._fps = 0.0
        self._mean = 0.0
        self._photo = None
        self._about = None
        self._build_ui()
        self._apply_window_style()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.src.key == "webcam":
            self._refresh_webcam_list()
        self._start_camera()
        self.root.after(TICK_MS, self._tick)

    @property
    def worker(self):
        return self.src.worker

    # ----- UI ----------------------------------------------------------------
    def _build_ui(self):
        root = self.root
        self.preview = ctk.CTkLabel(root, text="Starting camera...",
                                    width=PREVIEW_W, height=PREVIEW_H,
                                    fg_color=th.color("bg_black"),
                                    text_color=th.color("text_secondary"))
        self.preview.grid(row=0, column=0, padx=8, pady=(8, 4))

        src = ctk.CTkFrame(root, fg_color=th.color("bg_surface"))
        src.grid(row=1, column=0, padx=8, pady=(0, 4), sticky="ew")
        self._build_source_bar(src)

        bar = ctk.CTkFrame(root, fg_color=th.color("bg_surface"))
        bar.grid(row=2, column=0, padx=8, pady=(0, 4), sticky="ew")
        bar.grid_columnconfigure(2, weight=1)
        self._build_control_bar(bar)

        rec = ctk.CTkFrame(root, fg_color=th.color("bg_surface"))
        rec.grid(row=3, column=0, padx=8, pady=(0, 8), sticky="ew")
        self._build_record_panel(rec)

        self._rec_locked_widgets = (
            self.exp_slider, self.gain_slider, self.mode_menu, self.bayer_menu,
            self.btn_run, self.source_menu, self.web_dev_menu,
            self.web_fmt_menu, self.auto_cb) + self._rec_inputs
        self._configure_controls()

        # drag in the picture and the free surfaces (not on sliders)
        for w in (self.preview, src, bar, rec):
            w.bind("<Button-1>", self._on_press, add="+")
            w.bind("<B1-Motion>", self._on_drag, add="+")
            w.bind("<ButtonRelease-1>", self._on_release, add="+")
        root.bind("<Button-3>", self._on_win_menu)

    def _build_source_bar(self, src):
        label_tip(src, "Camera",
                  "Which camera to use. Meade LPI needs the WinUSB driver "
                  "(see INSTALL.txt). A webcam such as the Logitech C270 "
                  "needs no driver."
                  ).grid(row=0, column=0, padx=(6, 2), pady=6)
        self.source_menu = ctk.CTkOptionMenu(
            src, values=[s.label for s in self.sources.values()], width=120,
            command=self._on_source)
        self.source_menu.set(self.src.label)
        self.source_menu.grid(row=0, column=1, padx=2, pady=6)

        self.web_dev_menu = ctk.CTkOptionMenu(
            src, values=[self.cfg["web_name"] or "Webcam %d" % self.cfg["web_index"]],
            width=230, command=self._on_web_device)
        self.web_dev_menu.grid(row=0, column=2, padx=6, pady=6)
        self.web_fmt_lbl = label_tip(
            src, "Format",
            "YUY2 is uncompressed and best for stacking but the webcam "
            "gives fewer frames per second at large sizes. MJPG is "
            "compressed and faster. Default: YUY2.")
        self.web_fmt_lbl.grid(row=0, column=3, padx=(6, 2), pady=6)
        self.web_fmt_menu = ctk.CTkOptionMenu(
            src, values=WEBCAM_FORMATS, width=80, command=self._on_web_format)
        self.web_fmt_menu.set(self.cfg["web_format"])
        self.web_fmt_menu.grid(row=0, column=4, padx=(2, 6), pady=6)
        self._web_widgets = (self.web_dev_menu, self.web_fmt_lbl,
                             self.web_fmt_menu)

    def _build_control_bar(self, bar):
        self.btn_run = ctk.CTkButton(bar, text="Stop", width=70,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, padx=6, pady=6)

        label_tip(bar, "Mode",
                  "Image size. Meade LPI: default 320x240, 640x480 does not "
                  "work yet. Webcam: default 640x480, larger sizes give "
                  "fewer frames per second."
                  ).grid(row=0, column=1, padx=(6, 2))
        self.mode_menu = ctk.CTkOptionMenu(bar, values=self.src.mode_labels,
                                           width=100, command=self._on_mode)
        self.mode_menu.grid(row=0, column=2, sticky="w", padx=2)

        self.color_var = ctk.BooleanVar(value=self.cfg["color"])
        self.color_cb = ctk.CTkCheckBox(bar, text="Color", variable=self.color_var,
                                        command=self._on_color, width=60)
        self.color_cb.grid(row=0, column=3, padx=6)
        self.bayer_lbl = label_tip(
            bar, "Bayer",
            "Layout of the colour filter on the sensor. If red and blue "
            "look swapped, try another. Also stored in recorded SER files. "
            "Default: BGGR.")
        self.bayer_lbl.grid(row=0, column=4, padx=(6, 2))
        self.bayer_menu = ctk.CTkOptionMenu(bar, values=list(bayer.LAYOUTS),
                                            width=85, command=self._on_bayer)
        self.bayer_menu.set(self.cfg["bayer"])
        self.bayer_menu.grid(row=0, column=5, padx=(2, 6))
        self.auto_var = ctk.BooleanVar(value=self.cfg["web_auto"])
        self.auto_cb = ctk.CTkCheckBox(bar, text="Auto exposure",
                                       variable=self.auto_var,
                                       command=self._on_auto, width=110)
        self.auto_cb.grid(row=0, column=3, columnspan=3, padx=6)

        label_tip(bar, "Exposure",
                  "Meade LPI: sensor line exposure 0 to 8191 on a cubic "
                  "scale, default 40. Webcam: log2 of seconds, -6 is 1/64 s, "
                  "default -6, only active with Auto exposure off. Higher = "
                  "brighter and slower frames."
                  ).grid(row=1, column=0, columnspan=2, padx=6, sticky="w")
        self.exp_slider = ctk.CTkSlider(bar, from_=0, to=100, number_of_steps=200,
                                        command=self._on_exposure)
        self.exp_slider.grid(row=1, column=2, columnspan=3, sticky="ew", padx=6)
        self.exp_val = ctk.CTkLabel(bar, text="", width=95)
        self.exp_val.grid(row=1, column=5, padx=6)

        label_tip(bar, "Gain",
                  "Meade LPI: 0 to 63, default 20. Webcam: 0 to 255, "
                  "default 0. Higher gain also adds noise."
                  ).grid(row=2, column=0, columnspan=2, padx=6, sticky="w")
        self.gain_slider = ctk.CTkSlider(bar, from_=0, to=63, number_of_steps=63,
                                         command=self._on_gain)
        self.gain_slider.grid(row=2, column=2, columnspan=3, sticky="ew", padx=6)
        self.gain_val = ctk.CTkLabel(bar, text="", width=95)
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

    def _build_record_panel(self, rec):
        rec.grid_columnconfigure(1, weight=1)
        label_tip(rec, "Folder",
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
        label_tip(row1, "Name",
                  "File name start. The files get a date, a time and a "
                  "number added, e.g. lpi_20261124_201330_01.ser. "
                  "Default: lpi."
                  ).pack(side="left", padx=(0, 4))
        self.rec_name = ctk.CTkEntry(row1, width=130)
        self.rec_name.insert(0, self.cfg["rec_name"])
        self.rec_name.pack(side="left", padx=(0, 10))
        label_tip(row1, "Seconds",
                  "Length of each file in seconds, 1 to 3600. Default: 20."
                  ).pack(side="left", padx=(0, 4))
        self.rec_seconds_entry = ctk.CTkEntry(row1, width=60)
        self.rec_seconds_entry.insert(0, str(self.cfg["rec_seconds"]))
        self.rec_seconds_entry.pack(side="left", padx=(0, 10))
        label_tip(row1, "Sequences",
                  "Number of files recorded one after the other, 1 to 999. "
                  "Default: 1."
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

    def _configure_controls(self):
        """Adapts menus, sliders and visible widgets to the selected camera."""
        s, cfg = self.src, self.cfg
        self.mode_menu.configure(values=s.mode_labels)
        self.mode_menu.set(s.mode_label(cfg))
        lo, hi, steps = s.exp_range(cfg)
        self.exp_slider.configure(from_=lo, to=hi, number_of_steps=steps)
        self.exp_slider.set(s.exp_to_pos(cfg))
        gmax = s.gain_max(cfg)
        self.gain_slider.configure(from_=0, to=gmax, number_of_steps=gmax)
        self.gain_slider.set(s.gain(cfg))
        lpi = s.kind == "bayer"
        for w in (self.color_cb, self.bayer_lbl, self.bayer_menu):
            w.grid() if lpi else w.grid_remove()
        for w in (self.auto_cb,) + self._web_widgets:
            w.grid_remove() if lpi else w.grid()
        self.auto_var.set(bool(s.auto(cfg)))
        self._update_auto_state()
        self._refresh_value_labels()

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
                         "Own WinUSB/libusb driver, ported from Linux gspca "
                         "sonixb.\nWebcams (e.g. Logitech C270) via DirectShow.",
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
        self.exp_val.configure(text=self.src.exposure_text(self.cfg))
        self.gain_val.configure(text=str(self.src.gain(self.cfg)))

    def _push_params(self):
        self.worker.set_params(*self.src.params(self.cfg))

    def _on_exposure(self, pos):
        self.src.pos_to_exp(self.cfg, pos)
        self._refresh_value_labels()
        self._push_params()

    def _on_gain(self, value):
        self.src.set_gain(self.cfg, value)
        self._refresh_value_labels()
        self._push_params()

    def _on_auto(self):
        self.cfg["web_auto"] = bool(self.auto_var.get())
        save_settings(self.cfg)
        self._update_auto_state()
        self._push_params()

    def _update_auto_state(self):
        auto = bool(self.src.auto(self.cfg))
        self.exp_slider.configure(state="disabled" if auto else "normal")

    def _on_mode(self, label):
        self.src.set_mode(self.cfg, label)
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

    # ----- camera selection ----------------------------------------------------
    def _on_source(self, label):
        key = next(k for k, s in self.sources.items() if s.label == label)
        if key == self.src.key:
            return
        self.worker.stop()
        self.src = self.sources[key]
        self.cfg["source"] = key
        save_settings(self.cfg)
        self._configure_controls()
        if key == "webcam":
            self._refresh_webcam_list()
        self._start_camera()

    def _refresh_webcam_list(self):
        if self._web_thread is not None and self._web_thread.is_alive():
            return
        self._web_result = None

        def job():
            self._web_result = list_webcams()
        self._web_thread = threading.Thread(target=job, daemon=True)
        self._web_thread.start()

    def _apply_webcam_list(self, names):
        self._web_names = names
        if not names:
            self.web_dev_menu.configure(values=["No webcam found"])
            self.web_dev_menu.set("No webcam found")
            return
        index = min(self.cfg["web_index"], len(names) - 1)
        self.web_dev_menu.configure(values=names)
        self.web_dev_menu.set(names[index])
        changed = index != self.cfg["web_index"]
        self.cfg["web_index"], self.cfg["web_name"] = index, names[index]
        save_settings(self.cfg)
        if changed and self.src.key == "webcam":
            self._start_camera()

    def _on_web_device(self, name):
        if name not in self._web_names:
            return
        self.cfg["web_index"] = self._web_names.index(name)
        self.cfg["web_name"] = name
        save_settings(self.cfg)
        self._start_camera()

    def _on_web_format(self, value):
        self.cfg["web_format"] = value
        save_settings(self.cfg)
        self._start_camera()

    def _start_camera(self):
        self._seen = 0
        self._fps_n = 0
        self.src.start(self.cfg)
        self.btn_run.configure(text="Stop")

    def _toggle_run(self):
        if self.worker.running:
            self.worker.stop()
            self.btn_run.configure(text="Start")
        else:
            self._start_camera()

    # ----- frames --------------------------------------------------------------
    def _render(self, arr):
        if self.src.kind == "bayer":
            if self.cfg["color"]:
                rgb = cv2.cvtColor(
                    arr, bayer.demosaic_code(self.cfg["bayer"]))[:, :, ::-1]
                img = Image.fromarray(rgb)
            else:
                img = Image.fromarray(arr)
            scale = max(1, PREVIEW_W // img.width)
            return img.resize((img.width * scale, img.height * scale),
                              Image.NEAREST)
        h, w = arr.shape[:2]
        scale = min(PREVIEW_W / w, PREVIEW_H / h)
        size = (max(1, int(w * scale)), max(1, int(h * scale)))
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        small = cv2.resize(arr, size, interpolation=interp)
        return Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))

    def _tick(self):
        if self._web_result is not None:
            names, self._web_result = self._web_result, None
            self._apply_webcam_list(names)
        w = self.worker
        with w.lock:
            counter, arr = w.counter, w.latest
        if arr is not None and counter != self._seen:
            if counter > self._seen > 0:
                self._fps_n += counter - self._seen
            img = self._render(arr)
            self._photo = ctk.CTkImage(light_image=img, dark_image=img,
                                       size=img.size)
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
                text="%.1f fps   mean %.0f" % (self._fps, self._mean),
                text_color=th.color("text_secondary"))
        self.root.after(TICK_MS, self._tick)

    def _save_frame(self):
        with self.worker.lock:
            arr = None if self.worker.latest is None else self.worker.latest.copy()
        if arr is None:
            return
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        prefix = "lpi" if self.src.kind == "bayer" else "webcam"
        base = os.path.join(CAPTURE_DIR, time.strftime(prefix + "_%Y%m%d_%H%M%S"))
        if self.src.kind == "bayer":
            cv2.imwrite(base + "_raw.png", arr)
            if self.cfg["color"]:
                cv2.imwrite(base + "_color.png", cv2.cvtColor(
                    arr, bayer.demosaic_code(self.cfg["bayer"])))
        else:
            cv2.imwrite(base + "_color.png", arr)
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
        width, height, color_id, planes, instrument = self.src.rec_format(self.cfg)
        try:
            self.recorder.start(
                folder, self.cfg["rec_name"], seconds, count, width, height,
                color_id, self.cfg["observer"], instrument,
                self.cfg["telescope"], planes)
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
        if not locked:
            self._update_auto_state()

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
        for source in self.sources.values():
            source.worker.stop()
        save_settings(self.cfg)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    LpiCamApp().run()


if __name__ == "__main__":
    main()
