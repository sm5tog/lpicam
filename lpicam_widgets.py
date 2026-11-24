"""Small shared widgets: hover tooltips and the label with an info mark."""
import tkinter as tk

import customtkinter as ctk

import ee_theme as th


class Tooltip:
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


def label_tip(parent, text, tip):
    """Label with a ⓘ mark and a hand cursor that shows `tip` on hover."""
    lbl = ctk.CTkLabel(parent, text=text + " ⓘ", cursor="hand2",
                       text_color=th.color("text_secondary"),
                       font=th.font("body"))
    Tooltip(lbl, tip)
    return lbl
