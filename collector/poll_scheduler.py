"""One owned Tk polling callback per stream, with fail-closed fault handling."""
from functools import wraps
from .diagnostics import emit


def scheduled_poll(function):
    @wraps(function)
    def tick(self):
        window = getattr(self, 'window', None) or self.app.window
        if getattr(self, '_poll_running', False) or getattr(self, '_poll_failed', False) or getattr(self, 'closed', False):
            return
        self._poll_running = True
        try:
            previous = getattr(self, '_poll_after', None)
            self._poll_after = None
            if previous is not None:
                window.after_cancel(previous)
            function(self)
            self._poll_after = window.after(1000, self.tick)
        except Exception as exc:
            # Do not call stop(): it finalizes consent checkpoints and may itself
            # fail. Retain durable recovery and require an explicit app reopen.
            self._poll_failed = True
            self.capture_problem = True
            self.problem = True
            self.enabled = False
            self.upload_enabled = False
            if getattr(self, 'tailer', None) is not None:
                self.tailer.stopped = True
            self.tailer = None
            emit('capture.scheduler', 'error', error=exc, stream=1 if hasattr(self, 'window') else 2)
            text = 'Ошибка сбора. Сбор остановлен; очередь сохранена. Перезапустите приложение.'
            # Independent best-effort presentation; a broken widget must not
            # prevent the other controls from reflecting the stopped state.
            actions = [lambda: self.status.set(text), lambda: self.status.config(text=text)]
            local = getattr(self, 'stop_local', None)
            if local:
                actions.append(local)
            for name in ('main_button', 'start_button', 'stop_button'):
                widget = getattr(self, name, None)
                if widget is not None:
                    actions.append(lambda w=widget: w.config(state='disabled'))
            dashboard = getattr(self, 'dashboard', None)
            if dashboard:
                actions.append(lambda: dashboard.heading.config(text='Ошибка сбора — перезапустите приложение'))
            for action in actions:
                try:
                    action()
                except Exception:
                    pass
        finally:
            self._poll_running = False
    return tick
