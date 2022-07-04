from anyio import sleep
from typing import AsyncIterable, Iterable

from .base import Scanner, UploadTarget

__all__ = ("FixedTargetList",)


class FixedTargetList(Scanner):
    """Scanner that simply iterates over a list of fixed upload targets and
    yields them one by one.
    """

    _targets: Iterable[str]
    """The targets that the scanner iterates over."""

    def __init__(self, targets: Iterable[UploadTarget]):
        self._targets = targets

    async def run(self) -> AsyncIterable[UploadTarget]:
        for target in self._targets:
            await sleep(0)  # checkpoint
            yield target
