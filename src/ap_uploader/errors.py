__all__ = ("UploaderError", "NotSupportedError")


class UploaderError(RuntimeError):
    """Superclass for all errors emitted from the uploader."""


class NotSupportedError(UploaderError):
    pass
