from abc import ABCMeta, abstractproperty
from anyio import to_thread
from typing import Any, Dict, IO, Mapping, Optional, Tuple

from .utils import crc32

__all__ = ("Firmware", "load_firmware")


class Firmware(metaclass=ABCMeta):
    """Interface specification for firmware objects that load a firmware from
    a file in a specific format.
    """

    @abstractproperty
    def board_id(self) -> int:
        """Returns the numeric board ID of the firmware. This indicates which
        board the firmware can be installed on.
        """
        raise NotImplementedError

    @abstractproperty
    def board_revision(self) -> Optional[int]:
        """Returns the numeric board revision ID of the firmware. This indicates
        which revision of the board the firmware can be installed on. Currently
        used for informational purposes only.
        """
        raise NotImplementedError

    @abstractproperty
    def crc(self) -> int:
        """Returns the CRC32 checksum of the firmware, without any padding."""
        raise NotImplementedError

    @abstractproperty
    def image(self) -> bytes:
        """The firmware image as a raw sequence of bytes."""
        raise NotImplementedError

    @abstractproperty
    def image_size(self) -> int:
        """The length of the image, in bytes."""
        raise NotImplementedError

    @abstractproperty
    def metadata(self) -> Mapping[str, Any]:
        """Returns a Python mapping object that contains the firmware metadata."""
        raise NotImplementedError


class FirmwareBase(Firmware):
    """Base implementation of the Firmware_ interface."""

    _crc: Optional[int] = None
    """Cached CRC of the firmware."""

    _image: Optional[bytes] = None
    """The firmware image; ``None`` if it has not been loaded yet."""

    _metadata: Dict[str, Any]
    """The metadata of the image."""

    def __init__(self) -> None:
        self._metadata = {}

    @property
    def board_id(self) -> int:
        return int(self.metadata["board_id"])

    @property
    def board_revision(self) -> Optional[int]:
        rev = self.metadata.get("board_revision")
        return None if rev is None else int(rev)

    @property
    def crc(self) -> int:
        if self._crc is None:
            self._crc = crc32(self.image)
        return self._crc

    @property
    def image(self) -> bytes:
        if self._image is None:
            raise RuntimeError("image has not been loaded yet")
        return self._image

    @property
    def image_size(self) -> int:
        size = self.metadata.get("image_size")
        if size is None:
            size = len(self.image)
        return size

    @property
    def metadata(self) -> Mapping[str, Any]:
        return self._metadata

    def _clear_metadata(self) -> None:
        """Clears the metadata associated to the image."""
        self._metadata.clear()

    def _set_image(self, image: bytes) -> None:
        """Internal function that sets the image stored in the instance and
        invalidates the cached CRC.
        """
        self._image = image
        self._crc = None

    def _update_metadata(self, **kwds) -> None:
        """Updates the metadata of the image with new key-value pairs."""
        self._metadata.update(**kwds)

    def _process_loaded_image_and_metadata(
        self, image: bytes, metadata: Dict[str, Any]
    ) -> None:
        self._clear_metadata()
        self._set_image(image)
        self._update_metadata(**metadata)


class APJFirmware(FirmwareBase):
    """Implementation of firmware files in APJ format."""

    async def read(self, fp: IO[bytes]) -> None:
        """Updates the firmware image by reading the given IO stream in an
        asynchronous manner.

        Loading and decompressing the image is delegated to a worker thread.

        Parameters:
            fp: the stream to read
            length: the number of bytes to read from the stream
        """
        image, metadata = await to_thread.run_sync(
            self._load_image, fp, cancellable=True
        )
        self._process_loaded_image_and_metadata(image, metadata)

    def read_sync(self, fp: IO[bytes]) -> None:
        """Updates the firmware image by reading the given IO stream in a
        synchronous manner.

        Parameters:
            fp: the stream to read
            length: the number of bytes to read from the stream
        """
        image, metadata = self._load_image(fp)
        self._process_loaded_image_and_metadata(image, metadata)

    def _load_image(self, fp: IO[bytes]) -> Tuple[bytes, Dict[str, Any]]:
        from base64 import b64decode
        from json import load
        from zlib import decompress

        obj: Dict[str, Any] = load(fp)
        image = decompress(b64decode(obj.pop("image")))

        return image, obj


async def load_firmware(source: str) -> Firmware:
    """Loads a firmware from the given source.

    Parameters:
        source: the source to load from. Right now we support APJ files only;
            later on we may add support for arbitrary URLs.

    Returns:
        the loaded firmware
    """
    firmware = APJFirmware()
    with open(source, "rb") as fp:
        await firmware.read(fp)
    return firmware
