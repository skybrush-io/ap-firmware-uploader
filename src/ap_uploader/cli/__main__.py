import logging

from argparse import ArgumentParser
from contextlib import AbstractContextManager
from functools import partial
from typing import Any, Dict, TYPE_CHECKING

from ap_uploader.uploader import (
    CRCMismatchEvent,
    LogEvent,
    UploadStep,
    Uploader,
    UploaderEvent,
    UploaderLifecycleEvent,
    UploadProgressEvent,
    UploadStepEvent,
)

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID


def create_parser() -> ArgumentParser:
    """Creates the command line parser for the uploader CLI."""
    parser = ArgumentParser(description="ArduPilot/PX4 firmware uploader")
    parser.add_argument("firmware", help="path to the firmware file to upload")
    parser.add_argument(
        "-p",
        "--port",
        help="serial port that the uploader will send data to",
        required=True,
    )
    return parser


class UploaderConsoleUI(AbstractContextManager):
    """Console-based user interface that provides feedback for the progress of
    an Uploader_.
    """

    _progress: "Progress"
    """Progress widget that the console UI shows."""

    _uploader_to_task_id: Dict[Uploader, "TaskID"]
    """Mapping from uploader instances to the corresponding task IDs in the
    progress widget.
    """

    def __init__(self):
        """Constructor."""
        from rich.progress import Progress

        self._progress = Progress()

    def __enter__(self):
        self._uploader_to_task_id = {}
        self._progress.__enter__()
        return self

    def __exit__(self, *args: Any):
        self._progress.__exit__(*args)
        self._uploader_to_task_id.clear()
        return super().__exit__(*args)

    def handle_event(self, sender: Uploader, event: UploaderEvent) -> None:
        """Handles an event from the uploader process."""
        if isinstance(event, UploadProgressEvent):
            task_id = self._get_task_id_for_uploader(sender)
            self._progress.update(task_id, completed=round(event.progress * 100))
        elif isinstance(event, UploadStepEvent):
            task_id = self._get_task_id_for_uploader(sender)
            if event.step is UploadStep.UPLOADING:
                self._progress.start_task(task_id)
            self._progress.update(task_id, description=event.step.description.ljust(13))
        elif isinstance(event, UploaderLifecycleEvent):
            if event.type == "finished":
                self._uploader_to_task_id.pop(sender, None)
            if event.type == "timeout":
                self._print_error("Timeout during upload")
        elif isinstance(event, CRCMismatchEvent):
            self._print_error(
                f"CRC mismatch, expected 0x{event.expected:04x}, got 0x{event.observed:04x}"
            )
        elif isinstance(event, LogEvent):
            if event.level >= logging.ERROR:
                self._print_error(event.message)
            elif event.level >= logging.WARNING:
                self._print_warning(event.message)
            else:
                self._print_message(event.message)

    def _get_task_id_for_uploader(
        self, uploader: Uploader, *, create: bool = True
    ) -> "TaskID":
        """Returns the task ID corresponding to the given uploader task."""
        task_id = self._uploader_to_task_id.get(uploader)
        if task_id is None:
            task_id = self._progress.add_task(description="Starting...", start=False)
            self._uploader_to_task_id[uploader] = task_id
        return task_id

    def _print_error(self, message: str) -> None:
        """Prints an error message to the console output."""
        self._progress.console.print(f"[red bold]:x:[/red bold] {message}")

    def _print_warning(self, message: str) -> None:
        """Prints a warning message to the console output."""
        self._progress.console.print(f"[yellow bold]![/yellow bold] {message}")

    def _print_message(self, message: str) -> None:
        """Prints an informational message to the console output."""
        self._progress.console.print(f"[green bold]>[/green bold] {message}")


async def uploader(options) -> None:
    port: str = options.port

    up = Uploader()
    with UploaderConsoleUI() as ui:
        await up.load_firmware(options.firmware)
        await up.upload_firmware(port, on_event=partial(ui.handle_event, up))


def main() -> None:
    from anyio import run

    parser = create_parser()
    options = parser.parse_args()

    run(uploader, options)
