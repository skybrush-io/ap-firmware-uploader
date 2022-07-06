"""User interface for the command-line uploader based on Rich."""

import logging

from contextlib import AbstractContextManager
from typing import Any, Dict, Optional, TYPE_CHECKING

from ap_uploader.uploader import (
    CRCMismatchEvent,
    LogEvent,
    UploadStep,
    UploaderEvent,
    UploaderLifecycleEvent,
    UploadProgressEvent,
    UploadStepEvent,
)

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID


__all__ = ("RichConsoleUI",)


class RichConsoleUI(AbstractContextManager):
    """Console-based user interface that provides feedback for the progress of
    an Uploader_.
    """

    _progress: "Progress"
    """Progress widget that the console UI shows."""

    _event_source_to_task_id: Dict[str, "TaskID"]
    """Mapping from upload target names to the corresponding task IDs in the
    progress widget.
    """

    def __init__(self):
        """Constructor."""
        from rich.progress import Progress, SpinnerColumn

        self._progress = Progress(
            SpinnerColumn(),
            "[bold white]{task.fields[source]:<13}[/bold white]",
            *Progress.get_default_columns(),
        )

    def __enter__(self):
        self._event_source_to_task_id = {}
        self._progress.__enter__()
        return self

    def __exit__(self, *args: Any):
        self._progress.__exit__(*args)
        self._event_source_to_task_id.clear()
        return super().__exit__(*args)

    def handle_event(self, sender: str, event: UploaderEvent) -> None:
        """Handles an event from the uploader process."""
        if isinstance(event, UploadProgressEvent):
            task_id = self._get_task_id_for_event_source(sender)
            self._progress.update(task_id, completed=round(event.progress * 100))
        elif isinstance(event, UploadStepEvent):
            task_id = self._get_task_id_for_event_source(sender)
            if event.step is UploadStep.UPLOADING:
                self._progress.start_task(task_id)
            self._progress.update(task_id, description=event.step.description.ljust(13))
        elif isinstance(event, UploaderLifecycleEvent):
            task_id = self._get_task_id_for_event_source(sender)
            if event.type == "finished":
                if not event.success:
                    if event.cancelled:
                        description = "[yellow bold]Cancelled    [/yellow bold]"
                    else:
                        description = "[red bold]Failed :(    [/red bold]"
                else:
                    description = "[green bold]Finished.    [/green bold]"
                self._progress.update(task_id, description=description)
                self._progress.stop_task(task_id)
                # Do not remove the association between sender and task ID in
                # case the upload is retried -- we don't want to create a new
                # line in this case
                # self._event_source_to_task_id.pop(sender, None)
        elif isinstance(event, CRCMismatchEvent):
            self.log(
                f"CRC mismatch, expected 0x{event.expected:04x}, got 0x{event.observed:04x}",
                level=logging.ERROR,
                sender=sender,
            )
        elif isinstance(event, LogEvent):
            self.log(event.message, sender=sender, level=event.level)

    def log(
        self, message: str, *, sender: Optional[str] = None, level: int = logging.INFO
    ):
        if level >= logging.ERROR:
            sign = "[red bold]X[/red bold]"
        elif level >= logging.WARNING:
            sign = "[yellow bold]![/yellow bold]"
        else:
            sign = "[green bold]>[/green bold]"
        if sender:
            sender = self._format_sender(sender)
            self._progress.console.print(f"{sign} {sender} {message}")
        else:
            self._progress.console.print(f"{sign} {message}")

    def _format_sender(self, sender: str) -> str:
        return f"[bold white]{sender:<15}[/bold white]"

    def _get_task_id_for_event_source(
        self, source: str, *, create: bool = True
    ) -> "TaskID":
        """Returns the task ID corresponding to the given uploader event
        source.
        """
        task_id = self._event_source_to_task_id.get(source)
        if task_id is None:
            task_id = self._progress.add_task(
                description="Starting...", start=False, source=source
            )
            self._event_source_to_task_id[source] = task_id
        return task_id
