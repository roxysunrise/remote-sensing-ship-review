"""Shared error identity for Python imports and the python -m CLI."""


class DetectionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
