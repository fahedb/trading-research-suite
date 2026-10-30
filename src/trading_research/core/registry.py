"""Minimal name-to-factory registry for resolved component selection.

It exists to turn a resolved component name such as ``"zscore"`` into an
object. It is not a dependency-injection container and should not grow into one.

Registration inverts the dependency — implementations import the registry,
never the reverse — which is what keeps ``core`` free of concrete components.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trading_research.core.errors import DuplicateComponentError, UnknownComponentError

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Registry"]


class Registry[T]:
    """A namespace of factories producing components of one kind."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._factories: dict[str, Callable[..., T]] = {}

    @property
    def kind(self) -> str:
        """Human-readable name of what this registry holds."""
        return self._kind

    def names(self) -> frozenset[str]:
        """Every registered name."""
        return frozenset(self._factories)

    def add(self, name: str, factory: Callable[..., T]) -> None:
        """Register ``factory`` under ``name``.

        Raises:
            DuplicateComponentError: If ``name`` is already registered.
        """
        if name in self._factories:
            raise DuplicateComponentError(f"{self._kind} {name!r} is already registered")
        self._factories[name] = factory

    def register(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator form of :meth:`add`."""

        def decorate(factory: Callable[..., T]) -> Callable[..., T]:
            self.add(name, factory)
            return factory

        return decorate

    # `Any` is the point here: a registry exists precisely to construct
    # components whose parameters it cannot know. Each factory types its own.
    def create(self, name: str, **params: Any) -> T:  # noqa: ANN401
        """Construct a component by name.

        Raises:
            UnknownComponentError: If ``name`` is not registered.
        """
        try:
            factory = self._factories[name]
        except KeyError:
            raise UnknownComponentError(
                f"unknown {self._kind} {name!r}; registered: {sorted(self._factories)}"
            ) from None
        return factory(**params)
