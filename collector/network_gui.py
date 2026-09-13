"""Foreground network UI; worker never touches Tk or SQLite."""
import queue as messages
import threading
import time
import webbrowser
from tkinter import ttk, messagebox
from .gui import App, tk
from .credentials import CredentialStore
from .transport import Pairing, Uploader, PAIR_URI


class Snapshot:
    def __init__(self, events):
        self.events, self.accepted = events, []

    def batch(self, limit):
        return self.events[:limit]

    def acknowledge(self, ids):
        self.accepted = list(ids)


class ConnectedApp(App):
    def __init__(self, window, log_root, queue):
        self.results = messages.Queue()
        self.busy = False
        self.pairing = self.uploader = None
        self.upload_enabled = False
        self.store = CredentialStore(queue.path.parent / 'credentials.dpapi')
        super().__init__(window, log_root, queue)
        window.title('SPHOL — combat collector')
        window.geometry('700x580')
        self.status.set('Stopped. Capture is local; pairing and upload status are shown below.')
        self.connection = ttk.Label(window, text='NOT CONNECTED — pair explicitly to enable HTTPS uploads.', wraplength=650)
        self.connection.pack(padx=20)
        code_frame = ttk.Frame(window)
        code_frame.pack(padx=20, pady=8)
        ttk.Label(code_frame, text='Код привязки:').pack(side='left', padx=8)
        self.pairing_code = tk.StringVar(master=window, value='')
        self.code_field = ttk.Entry(code_frame, textvariable=self.pairing_code,
                                    state='readonly', width=34, exportselection=False)
        self.code_field.pack(side='left')
        self.copy_button = ttk.Button(code_frame, text='Скопировать код',
                                      command=self.copy_pairing_code, state='disabled')
        self.copy_button.pack(side='left', padx=8)
        self.copy_feedback = ttk.Label(window, text='')
        self.copy_feedback.pack()
        buttons = ttk.Frame(window)
        buttons.pack(pady=10)
        ttk.Button(buttons, text='Pair with SPHOL', command=self.pair).pack(side='left')
        ttk.Button(buttons, text='Enable uploads', command=self.enable).pack(side='left', padx=8)
        ttk.Button(buttons, text='Unpair locally', command=self.unpair).pack(side='left')
        try:
            credentials = self.store.load()
            if credentials:
                self.uploader = Uploader(credentials)
                self.show_identity()
        except Exception:
            self.connection.config(text='NOT CONNECTED — stored credentials unavailable/expired. Unpair then pair again.')
        window.after(250, self.network_tick)

    def clear_pairing_code(self):
        # Only clear our UI: never overwrite the user's clipboard automatically.
        self.pairing_code.set('')
        self.copy_button.config(state='disabled')
        self.copy_feedback.config(text='')

    def copy_pairing_code(self):
        # Invoked by Tk on the main thread, never by the network worker.
        if not self.pairing or time.monotonic() >= self.pairing.deadline:
            self.clear_pairing_code()
            return
        code = self.pairing_code.get()
        if not code:
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(code)
        except tk.TclError:
            self.copy_feedback.config(text='Не удалось скопировать. Выделите код и нажмите Ctrl+C.')
        else:
            self.copy_feedback.config(text='Код скопирован')

    def show_identity(self):
        c = self.uploader.credentials
        self.connection.config(text='Paired (not proof of current connection): ' + ', '.join(x['name'] for x in c['characters']) + '\nExpires: ' + c['expires_at'] + '. Uploads require Enable uploads.')

    def work(self, kind, function):
        if self.busy:
            return
        self.busy = True
        def run():
            try:
                self.results.put((kind, function(), None))
            except Exception as error:
                # Never forward request bodies or exception messages to UI/logs.
                self.results.put((kind, None, type(error).__name__))
        threading.Thread(target=run, daemon=True).start()

    def pair(self):
        if self.busy or self.uploader or self.pairing:
            return
        if self.queue.count():
            messagebox.showwarning('Pending events', 'Delete pending before pairing to prevent cross-account transfer.')
            return
        if not messagebox.askyesno('Pair collector?', 'Open sphol.com for explicit member/character consent? Only approved characters may upload. No password is entered here.'):
            return
        self.pairing = Pairing()
        self.work('pair', self.pairing.start)

    def enable(self):
        if self.uploader and not self.uploader.paused:
            self.upload_enabled = True
            self.connection.config(text='Uploads enabled; waiting for server acknowledgement. Other characters remain pending.')

    def stop(self):
        self.upload_enabled = False
        super().stop()
        if hasattr(self, 'connection'):
            self.connection.config(text='Uploads stopped; an already sent request may finish. Pending is retained until exact ACK.')

    def start(self):
        super().start()
        if self.tailer:
            self.status.set('Capturing new combat events locally. Upload status is shown separately below.')

    def clear(self):
        if self.busy:
            messagebox.showwarning('Request in progress', 'Wait for the current request before deleting pending.')
            return
        super().clear()

    def unpair(self):
        if self.busy:
            return
        if not messagebox.askyesno('Unpair locally?', 'Remove this computer’s credential and delete pending events? Revoke the installation on the website separately.'):
            return
        self.stop()
        self.store.clear()
        self.queue.clear()
        self.uploader = self.pairing = None
        self.clear_pairing_code()
        self.connection.config(text='NOT CONNECTED — local credential removed. Website revocation is separate.')

    def network_tick(self):
        try:
            kind, result, error = self.results.get_nowait()
        except messages.Empty:
            pass
        else:
            self.busy = False
            if error:
                self.upload_enabled = False
                self.pairing = None
                self.clear_pairing_code()
                self.connection.config(text='NOT CONNECTED / request failed (' + error + '); pending retained. No success assumed.')
            elif kind == 'pair':
                self.pairing_code.set(result)
                self.copy_button.config(state='normal')
                self.copy_feedback.config(text='')
                self.connection.config(text='Введите код на sphol.com и подтвердите привязку.')
                webbrowser.open(PAIR_URI)
            elif kind == 'token' and result:
                self.clear_pairing_code()
                try:
                    self.store.save(result)
                    self.uploader = Uploader(result)
                    self.show_identity()
                except Exception:
                    self.connection.config(text='NOT CONNECTED — DPAPI storage failed; no insecure fallback.')
                self.pairing = None
            elif kind == 'upload':
                snapshot, status = result
                self.queue.acknowledge(snapshot.accepted)
                if status:
                    self.connection.config(text=status)
        if self.pairing_code.get() and (not self.pairing or time.monotonic() >= self.pairing.deadline):
            self.clear_pairing_code()
        if not self.busy:
            if self.pairing:
                self.work('token', self.pairing.poll)
            elif self.upload_enabled and self.uploader:
                snapshot = Snapshot(self.queue.batch(listeners={c['name'] for c in self.uploader.credentials['characters']}))
                uploader = self.uploader
                self.work('upload', lambda: (snapshot, uploader.upload(snapshot)))
        self.window.after(1000, self.network_tick)
