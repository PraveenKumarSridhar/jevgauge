"""A small allowlist over the existing Hermes YAML configuration mechanism."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re

from .cli import InstallError, PLUGIN, PLUGIN_FILES, _check_paths, _lock, _read_config, _set_enabled, _write_config, _verify_owned

SEMANTICS = 'Close Hermes Desktop before saving. Restart Desktop to apply settings to new conversations. Saved conversation bindings remain unchanged. YAML comments and formatting are normalized.'
DEFAULTS = {
    'enabled': False,
    'effort_mode': 'auto',
    'selection_timeout': 5.0,
    'tier_models': {
        'economical': ['gpt-6-luna', 'gpt-5.6-luna'],
        'balanced': ['gpt-6-sol', 'gpt-5.6-sol'],
        'strongest': ['gpt-6-astra', 'gpt-6-sol', 'gpt-5.6-sol'],
    },
}


class ConfigError(ValueError):
    pass


def validate(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - DEFAULTS.keys():
        raise ConfigError('Only supported routing settings can be changed.')
    if 'enabled' in changes and type(changes['enabled']) is not bool:
        raise ConfigError('Enabled must be a boolean.')
    if 'effort_mode' in changes and changes['effort_mode'] not in ('auto', 'manual'):
        raise ConfigError('Effort mode must be auto or manual.')
    if 'selection_timeout' in changes:
        value = changes['selection_timeout']
        if type(value) not in (int, float) or not math.isfinite(value) or not 0.01 <= value <= 10:
            raise ConfigError('Selection timeout must be between 0.01 and 10 seconds.')
    if 'tier_models' in changes:
        tiers = changes['tier_models']
        if not isinstance(tiers, dict) or set(tiers) != {'economical', 'balanced', 'strongest'}:
            raise ConfigError('Provide all three capacity tier lists.')
        if not any(tiers.values()):
            raise ConfigError('At least one candidate model is required.')
        for models in tiers.values():
            if not isinstance(models, list) or len(models) > 32 or any(
                not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model)
                for model in models
            ):
                raise ConfigError('Each tier must contain at most 32 valid model identifiers.')
            if len(set(models)) != len(models):
                raise ConfigError('A tier cannot repeat a model.')
    return changes


def _snapshot(home):
    path = home / 'config.yaml'
    if path.exists():
        with path.open('rb') as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ConfigError('Configuration exceeds the supported size.')
    else:
        raw = b''
    return _read_config(home, raw=raw), hashlib.sha256(raw).hexdigest()


def _installed(home):
    try:
        ownership = _verify_owned(home / 'plugins' / PLUGIN)
        return set(ownership['files']) == set(PLUGIN_FILES)
    except InstallError:
        return False


def read_config(home: Path):
    try:
        _check_paths(home)
        config, revision = _snapshot(home)
        stored = config.get('plugins', {}).get('entries', {}).get(PLUGIN, {}).get('settings', {})
        settings = {key: stored.get(key, value) for key, value in DEFAULTS.items()}
        # Effective enable requires both plugin loading and the router's own switch.
        settings['enabled'] = (PLUGIN in config.get('plugins', {}).get('enabled', [])
                               and settings['enabled'] is True)
        validate(settings)
        installed = _installed(home)
        settings['enabled'] = installed and settings['enabled']
        return {'settings': settings, 'revision': revision, 'semantics': SEMANTICS,
                'supported': list(DEFAULTS), 'installed': installed, 'can_enable': installed,
                'installation_notice': None if installed else 'Install the owned JevGauge plugin in a compatible Hermes checkout before enabling routing.'}
    except (InstallError, OSError, UnicodeError, RecursionError):
        raise ConfigError('Configuration is inaccessible or invalid. No changes were made.') from None


def update_config(home: Path, changes, revision):
    validate(changes)
    try:
        _check_paths(home)
        with _lock(home):
            config, current_revision = _snapshot(home)
            if not isinstance(revision, str) or revision != current_revision:
                raise ConfigError('Configuration changed since it was loaded. Reload before saving.')
            if changes.get('enabled') is True and not _installed(home):
                raise ConfigError('Complete the JevGauge install before enabling routing.')
            if 'enabled' in changes:
                _set_enabled(config, changes['enabled'])
            settings = config.setdefault('plugins', {}).setdefault('entries', {}).setdefault(PLUGIN, {}).setdefault('settings', {})
            settings.update(changes)
            _write_config(home, config)
        return read_config(home)
    except (InstallError, OSError, UnicodeError, RecursionError):
        raise ConfigError('Configuration is locked, inaccessible, or invalid. No changes were made.') from None
