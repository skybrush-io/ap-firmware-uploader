"""Interface specification for scanner tasks that generate upload targets
where the current firmware can be uploaded, based on external events.
"""

from abc import abstractmethod
from typing import AsyncIterable

__all__ = ("Scanner", "UploadTarget")

UploadTarget = str


class Scanner:
    """Base class for scanner tasks that generate upload targets where the
    current firmware can be uploaded, based on external events.
    """

    @abstractmethod
    def run(self) -> AsyncIterable[UploadTarget]:
        """Runs the scanner task.

        Yields:
            upload targets whenever the scanner task wishes to propose a new
            upload target
        """
        raise NotImplementedError
