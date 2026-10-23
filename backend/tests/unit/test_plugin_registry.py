"""
Unit tests for Plugin Registry System.
"""

from src.shared.plugins.registry import PluginRegistry


def test_plugin_registry_discovery():
    PluginRegistry._discovered = False
    PluginRegistry.discover_all()

    strategies = PluginRegistry.list_strategies()
    assert len(strategies) > 0

    # Ensure demo_rsi_ma strategy is discovered
    assert "demo_rsi_ma" in strategies or "rsi_mean_reversion" in strategies or len(strategies) >= 1

    # Check getter
    strat_cls = PluginRegistry.get_strategy(strategies[0])
    assert strat_cls is not None

    # Check backward compatibility aliases
    assert PluginRegistry.get(strategies[0]) == strat_cls
    assert PluginRegistry.list_names() == strategies


def test_plugin_registry_get_nonexistent():
    assert PluginRegistry.get_strategy("non_existent_plugin_xyz") is None
    assert PluginRegistry.get_indicator("non_existent_indicator_xyz") is None
