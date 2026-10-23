"""
Plugin Discovery and Registration System.

Discovers and registers:
1. Strategy plugins from ``src.plugins.strategies`` (subclasses of ``BaseStrategy``)
2. Indicator plugins from ``src.plugins.indicators`` (subclasses of ``BaseIndicator``)
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from src.domain.indicators.base import BaseIndicator
from src.domain.strategy.base import BaseStrategy
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from typing import ClassVar

logger = get_logger(__name__)


class PluginRegistry:
    """
    Registry for dynamic plugins (Strategies, Indicators, Agents).
    """

    _strategies: ClassVar[dict[str, type[BaseStrategy]]] = {}
    _indicators: ClassVar[dict[str, type[BaseIndicator]]] = {}
    _discovered: bool = False

    @classmethod
    def _discover_strategies(cls) -> None:
        package_name = "src.plugins.strategies"
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            return
        for _, module_name, is_pkg in pkgutil.iter_modules(package.__path__):
            if is_pkg:
                continue
            full_name = f"{package_name}.{module_name}"
            try:
                module = importlib.import_module(full_name)
            except Exception as e:
                logger.warning("strategy_plugin_load_error", module=full_name, error=str(e))
                continue
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseStrategy)
                    and attr is not BaseStrategy
                ):
                    strategy_metadata = getattr(attr, "metadata", None)
                    name = getattr(strategy_metadata, "name", attr.__name__)
                    cls._strategies[name] = attr
                    logger.info("strategy_plugin_registered", name=name)

    @classmethod
    def _discover_indicators(cls) -> None:
        package_name = "src.plugins.indicators"
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            return
        for _, module_name, is_pkg in pkgutil.iter_modules(package.__path__):
            if is_pkg:
                continue
            full_name = f"{package_name}.{module_name}"
            try:
                module = importlib.import_module(full_name)
            except Exception as e:
                logger.warning("indicator_plugin_load_error", module=full_name, error=str(e))
                continue
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseIndicator)
                    and attr is not BaseIndicator
                ):
                    indicator_metadata = getattr(attr, "metadata", None)
                    name = getattr(indicator_metadata, "name", attr.__name__)
                    cls._indicators[name] = attr
                    logger.info("indicator_plugin_registered", name=name)

    @classmethod
    def discover_all(cls) -> None:
        if cls._discovered:
            return
        cls._discover_strategies()
        cls._discover_indicators()
        cls._discovered = True

    @classmethod
    def get_strategy(cls, name: str) -> type[BaseStrategy] | None:
        cls.discover_all()
        return cls._strategies.get(name)

    @classmethod
    def list_strategies(cls) -> list[str]:
        cls.discover_all()
        return list(cls._strategies.keys())

    @classmethod
    def get_indicator(cls, name: str) -> type[BaseIndicator] | None:
        cls.discover_all()
        return cls._indicators.get(name)

    @classmethod
    def list_indicators(cls) -> list[str]:
        cls.discover_all()
        return list(cls._indicators.keys())

    @classmethod
    def get(cls, name: str) -> type[BaseStrategy] | None:
        """Backward compatibility for existing strategy getter."""
        return cls.get_strategy(name)

    @classmethod
    def list_names(cls) -> list[str]:
        """Backward compatibility for existing strategy list."""
        return cls.list_strategies()


# Export singleton
registry = PluginRegistry
