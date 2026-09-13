"""Restrained graphite theme; clam avoids mixed Windows native/light surfaces."""
import os
from tkinter import ttk

BG = '#202226'
FG = '#f0f1f2'
MUTED = '#b8bec7'
SURFACE = '#2d3036'
ACCENT = '#b9d5c7'


def apply_theme(window):
    window.configure(background=BG)
    font = ('Segoe UI' if os.name == 'nt' else 'DejaVu Sans', 10)
    window.option_add('*Font', font)
    style = ttk.Style(window)
    style.theme_use('clam')
    style.configure('.', background=BG, foreground=FG, font=font)
    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG, foreground=FG)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Title.TLabel', font=(font[0], 18, 'bold'))
    style.configure('TButton', background=SURFACE, foreground=FG, padding=(14, 9), borderwidth=1, focuscolor=ACCENT)
    style.map('TButton', background=[('active', '#41464e'), ('pressed', '#363b42')], foreground=[('disabled', '#89909a')])
    style.configure('Primary.TButton', background=ACCENT, foreground=BG, font=(font[0], 11, 'bold'), padding=(20, 12))
    style.map('Primary.TButton', background=[('active', '#d0e6dc'), ('pressed', '#9dbbad')], foreground=[('disabled', '#66736d')])
    style.configure('TCheckbutton', background=BG, foreground=FG, padding=(0, 6))
    style.map('TCheckbutton', background=[('active', BG)])
    style.configure('TEntry', fieldbackground=SURFACE, foreground=FG, insertcolor=FG, padding=6)
    style.map('TEntry', fieldbackground=[('readonly', SURFACE)])
    style.configure('TLabelframe', background=BG, bordercolor='#484d56')
    style.configure('TLabelframe.Label', background=BG, foreground=MUTED)
    style.configure('TSeparator', background='#484d56')
