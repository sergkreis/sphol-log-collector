"""Foreground GUI: no network, no service, no tray, no auto-start."""
import ctypes
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from .core import PendingQueue, Tailer, QueueFull


def documents():
    if os.name != 'nt':
        raise OSError('This GUI targets Windows; core tests run cross-platform')
    # Windows Documents known folder (includes OneDrive / redirected Documents).
    import uuid
    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('FDD39AD0-238F-46AF-ADB4-6C85480369C7').bytes_le)
    result = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(result))
    if hr != 0:
        raise OSError('Cannot locate Windows Documents')
    try:
        return Path(result.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(result)


class App:
    def __init__(self, window, log_root, queue):
        self.window, self.log_root, self.queue = window, log_root, queue
        self.tailer = None
        window.title('SPHOL — combat log collector (preview)')
        window.geometry('650x330')
        self.status = tk.StringVar(value='Stopped. NOT CONNECTED — server integration is not available.')
        self.pending = tk.StringVar()
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Local-only preview • Nothing is uploaded', font=('', 14)).pack(anchor='w')
        ttk.Label(frame, text=str(log_root), wraplength=600).pack(anchor='w', pady=10)
        ttk.Label(frame, text='Reads new combat lines only. No Chatlogs, memory, passwords or background service.', wraplength=600).pack(anchor='w')
        ttk.Label(frame, textvariable=self.status, wraplength=600).pack(anchor='w', pady=15)
        ttk.Label(frame, textvariable=self.pending).pack(anchor='w')
        buttons = ttk.Frame(frame)
        buttons.pack(anchor='w', pady=15)
        ttk.Button(buttons, text='Start capture', command=self.start).pack(side='left')
        ttk.Button(buttons, text='Stop capture', command=self.stop).pack(side='left', padx=8)
        ttk.Button(buttons, text='Delete pending', command=self.clear).pack(side='left')
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.tick()

    def start(self):
        if self.tailer is not None:
            return
        try:
            self.tailer = Tailer(self.log_root, self.queue)
            self.status.set('Capturing locally. NOT CONNECTED — nothing uploaded.')
        except (OSError, ValueError) as error:
            self.status.set('Cannot start: ' + str(error))

    def stop(self):
        self.tailer = None
        self.status.set('Stopped. NOT CONNECTED — pending data remains on this computer.')

    def clear(self):
        if messagebox.askyesno('Delete pending?', 'Permanently delete all queued events? Stop capture first to avoid recapturing a blocked event.'):
            self.stop()
            self.queue.clear()

    def tick(self):
        if self.tailer:
            try:
                self.tailer.poll()
            except QueueFull:
                self.status.set('QUEUE FULL — capture paused, not uploaded. Stop or delete pending.')
            except Exception as error:
                self.stop()
                self.status.set('Capture stopped after local error: ' + type(error).__name__)
        self.pending.set(f'Pending on disk: {self.queue.count()} events')
        self.window.after(1000, self.tick)

    def close(self):
        if self.queue.count() and not messagebox.askyesno('Unsent events', 'Pending events have NOT been uploaded. Keep them on disk and exit? No background process will remain.'):
            return
        self.tailer = None
        self.queue.close()
        self.window.destroy()


def main():
    window = tk.Tk()
    try:
        root = documents() / 'EVE' / 'logs' / 'Gamelogs'
        state = Path(os.environ['LOCALAPPDATA']) / 'SPHOLLogCollector' / 'pending.sqlite3'
        App(window, root, PendingQueue(state))
    except Exception as error:
        messagebox.showerror('Cannot open collector', str(error))
        window.destroy()
        return
    window.mainloop()
