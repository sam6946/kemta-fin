"""Logs structurés (JSON en production) + corrélation par `request_id`."""

from __future__ import annotations

import json
import logging
import uuid

_REQUEST_ID = None


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(value: str | None) -> None:
    global _REQUEST_ID
    _REQUEST_ID = value


def get_request_id() -> str:
    return _REQUEST_ID or "-"


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """Une ligne JSON par événement, sans donnée sensible."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", get_request_id()),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
