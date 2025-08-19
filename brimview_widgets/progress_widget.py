import panel as pn
import time
import datetime

pn.extension()

class ProgressWidget(pn.Column):
    def __init__(self, step_interval=1, min_interval=0.1, **kwargs):
        self.label = pn.pane.Markdown("### Ready")

        self.progress = pn.widgets.Progress(
            value=0, max=100, width=400, name="Progress"
        )
        self.status_text = pn.pane.Str("0 / 0")
        self.time_info = pn.pane.Str("Start: -- | Elapsed: -- | ETA: --")

        self._start_time = None
        self._last_update_time = 0
        self._last_step = 0
        self._step_interval = step_interval
        self._min_interval = min_interval

        super().__init__(
            self.label,
            pn.Row(self.progress, self.status_text),
            self.time_info,
            **kwargs
        )

    def _format_duration(self, seconds):
        return str(datetime.timedelta(seconds=int(seconds)))

    def start(self, total: int, task: str = "Working..."):
        now = time.time()
        self._start_time = now
        self._last_update_time = now
        self._last_step = 0

        self.progress.max = total
        self.progress.value = 0
        self.label.object = f"### {task}"
        self.status_text.object = f"0 / {total}"
        self._update_time_info(current=0)

    def update(self, current: int, total: int = None):
        now = time.time()
        if total is not None:
            self.progress.max = total

        should_update = (
            (current - self._last_step >= self._step_interval) or
            (now - self._last_update_time >= self._min_interval) or
            (current == self.progress.max)
        )

        if not should_update:
            return

        self.progress.value = current
        self.status_text.object = f"{current} / {self.progress.max}"
        self._update_time_info(current=current)

        self._last_update_time = now
        self._last_step = current

    def _update_time_info(self, current):
        if self._start_time is None or current == 0:
            self.time_info.object = "Start: -- | Elapsed: -- | ETA: --"
        else:
            now = time.time()
            elapsed = now - self._start_time
            rate = elapsed / current if current > 0 else 0
            remaining = self.progress.max - current
            eta = rate * remaining

            self.time_info.object = (
                f"Start: {time.strftime('%H:%M:%S', time.localtime(self._start_time))} | "
                f"Elapsed: {self._format_duration(elapsed)} | "
                f"ETA: {self._format_duration(eta)}"
            )

    def finish(self, message="Done"):
        self.progress.value = self.progress.max
        self.status_text.object = f"{self.progress.max} / {self.progress.max}"
        self._update_time_info(current=self.progress.max)
        self.label.object += f" -- {message}"
