"""Registry for monitor specifications, without execution or I/O."""

from app.core.contracts import MonitorSpec


class MonitorRegistry:
    def __init__(self) -> None:
        self._items: dict[str, MonitorSpec] = {}

    def register(self, spec: MonitorSpec) -> None:
        if not spec.name.strip():
            raise ValueError("monitor name must not be empty")
        if spec.name in self._items:
            raise ValueError(f"duplicate monitor: {spec.name}")
        self._items[spec.name] = spec

    def get(self, name: str) -> MonitorSpec:
        return self._items[name]

    def names(self) -> tuple[str, ...]:
        return tuple(self._items)
