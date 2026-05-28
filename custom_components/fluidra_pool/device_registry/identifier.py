"""Device-identification logic (pattern matching + scoring + caching)."""

from __future__ import annotations

from functools import lru_cache
import re
from typing import Any

from .configs import DEVICE_CONFIGS
from .types import DeviceConfig


@lru_cache(maxsize=1024)
def _compile_wildcard_pattern(pattern_lower: str) -> re.Pattern[str]:
    """Compile a wildcard pattern (``*`` supported) into a case-insensitive regex."""
    regex = re.escape(pattern_lower).replace(r"\*", ".*")
    return re.compile(f"^{regex}$")


def _match(value: str, patterns: tuple[str, ...]) -> bool:
    """Pure-function equivalent of ``DeviceIdentifier._matches_pattern`` for caching."""
    if not value or not patterns:
        return False
    value_lower = value.lower()
    for pattern in patterns:
        pattern_lower = pattern.lower()
        if "*" in pattern_lower:
            if _compile_wildcard_pattern(pattern_lower).match(value_lower):
                return True
        elif pattern_lower in value_lower:
            return True
    return False


@lru_cache(maxsize=512)
def _identify_device_uncached(
    *,
    device_id: str,
    device_name: str,
    family: str,
    model: str,
    device_type_hint: str,
    comp7_value: str,
) -> DeviceConfig | None:
    """Resolve a :class:`DeviceConfig` from hashable primitives so lru_cache can memoise."""
    sorted_configs = sorted(DEVICE_CONFIGS.items(), key=lambda x: x[1].priority, reverse=True)

    best_match: DeviceConfig | None = None
    best_score = 0

    for config_name, config in sorted_configs:
        score = 0

        if _match(device_id, tuple(config.identifier_patterns)):
            score += 50
        if _match(device_name, tuple(config.name_patterns)):
            score += 30
        if _match(family, tuple(config.family_patterns)):
            score += 20
        if _match(model, tuple(config.model_patterns)):
            score += 20
        if config.device_type in device_type_hint:
            score += 10

        if config_name == "lg_heat_pump" and _match(comp7_value, ("BXWAA",)):
            score += 100

        if config_name == "z260iq_heat_pump":
            if _match(comp7_value, ("BXWAD",)):
                score += 100
            else:
                score = 0

        if score > best_score:
            best_score = score
            best_match = config

    if best_score < 10:
        if "heat_pump" in device_type_hint or "heat" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_heat_pump")
        if "pump" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_pump")
        if "heater" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_heater")
        if "light" in device_type_hint:
            return DEVICE_CONFIGS.get("generic_light")

    return best_match


class DeviceIdentifier:
    """Helper to identify device type from device data."""

    @staticmethod
    def _matches_pattern(value: str, patterns: list[str] | tuple[str, ...]) -> bool:
        """Check if value matches any pattern (supports ``*`` wildcard)."""
        if not value or not patterns:
            return False

        value_lower = value.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if "*" in pattern_lower:
                if _compile_wildcard_pattern(pattern_lower).match(value_lower):
                    return True
            elif pattern_lower in value_lower:
                return True
        return False

    @staticmethod
    def _check_component_signature(device: dict, component_id: int, value_patterns: list[str]) -> bool:
        """Check if a specific component contains expected values."""
        try:
            components = device.get("components", {})
            if isinstance(components, dict) and str(component_id) in components:
                component = components[str(component_id)]
                reported_value = str(component.get("reportedValue", ""))
                return DeviceIdentifier._matches_pattern(reported_value, value_patterns)
        except (AttributeError, TypeError):
            pass
        return False

    @staticmethod
    def identify_device(device: dict) -> DeviceConfig | None:
        """Identify device type and return its configuration.

        Returns the best matching DeviceConfig based on priority and matching criteria.
        Results are cached in-place on the device dict to avoid recomputation.
        """
        if not isinstance(device, dict):
            return None

        family = device.get("family", "")
        if family and "bridge" in family.lower():
            return None

        # Cache result on the device itself — the key includes the component-7
        # signature so a signature change (first vs subsequent polls) invalidates it.
        raw_components = device.get("components")
        components: dict[str, Any] = raw_components if isinstance(raw_components, dict) else {}
        comp7_value = ""
        if "7" in components and isinstance(components["7"], dict):
            comp7_value = str(components["7"].get("reportedValue", ""))

        cache_key = (
            device.get("device_id", ""),
            family,
            device.get("model", ""),
            device.get("type", ""),
            comp7_value,
        )
        cache = device.get("_identify_cache")
        if isinstance(cache, dict) and cache.get("key") == cache_key:
            return cache.get("config")

        result = _identify_device_uncached(
            device_id=str(cache_key[0]),
            device_name=device.get("name", ""),
            family=family,
            model=str(cache_key[2]),
            device_type_hint=str(cache_key[3]).lower(),
            comp7_value=comp7_value,
        )
        device["_identify_cache"] = {"key": cache_key, "config": result}
        return result

    @staticmethod
    def should_create_entity(device: dict, entity_type: str) -> bool:
        """Check if a specific entity type should be created for this device."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return False
        return entity_type in config.entities

    @staticmethod
    def get_components_range(device: dict) -> int:
        """Get the component scan range for this device."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return 25
        return config.components_range

    @staticmethod
    def has_feature(device: dict, feature_name: str) -> bool:
        """Check if device supports a specific feature."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return False
        return config.features.get(feature_name, False)

    @staticmethod
    def get_feature(device: dict, feature_name: str, default: Any = None) -> Any:
        """Get a feature value for this device."""
        config = DeviceIdentifier.identify_device(device)
        if not config:
            return default
        return config.features.get(feature_name, default)
