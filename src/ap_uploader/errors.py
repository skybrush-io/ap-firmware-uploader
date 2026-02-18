__all__ = ("UploaderError", "NotSupportedError", "ExcessDataError")


class UploaderError(RuntimeError):
    """Superclass for all errors emitted from the uploader."""


class NotSupportedError(UploaderError):
    pass


class ExcessDataError(UploaderError):
    def __init__(self) -> None:
        super().__init__("unexpected data received")
