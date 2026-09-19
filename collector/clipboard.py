"""Widget-local paste; Windows virtual key works independently of layout."""
import tkinter as tk


def paste(widget):
    if str(widget.cget('state')) != 'normal':
        return 'break'
    try:
        text = widget.clipboard_get()
    except tk.TclError:
        return 'break'  # Do not erase a selection when the clipboard is unavailable.
    if widget.selection_present():
        start = widget.index('sel.first')
        widget.delete('sel.first', 'sel.last')
        widget.icursor(start)
    widget.insert('insert', text)
    return 'break'


def bind_paste(widget):
    def control(event):
        windows = widget.tk.call('tk', 'windowingsystem') == 'win32'
        if event.keysym.lower() == 'v' or (windows and event.keycode == 86):
            return paste(widget)
        return None
    widget.bind('<Control-KeyPress>', control, add='+')
    widget.bind('<<Paste>>', lambda event: paste(widget))
