"""Small interfaces shared across monitoring layers."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping


@dataclass(frozen=True)
class MonitorResult:
    name: str
    ok: bool
    message: str = ""
    data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitorContext:
    now: datetime
    settings: Mapping[str, bool]
    intervals: Mapping[str, int]


@dataclass(frozen=True)
class MonitorSpec:
    name: str
    interval_key: str
    runner: Callable[[], object]
