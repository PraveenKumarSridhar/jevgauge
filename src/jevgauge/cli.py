"""Install a user-scoped plugin without modifying the Hermes checkout.

Compatibility checks are deliberately static: doctor never imports Hermes, reads
credentials, or sends network requests. Its success proves an API contract, not
account entitlement or live routing success.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

import yaml

PLUGIN = 'jev-router'
PLUGIN_FILES = ('__init__.py', 'plugin.yaml')
MANIFEST = '.jevgauge-install.json'
REQUIRED_SYMBOLS = {
    'hermes_cli/plugins.py': ('has_hook', 'invoke_hook', 'register_hook', 'register_command', 'get_config'),
    'hermes_cli/auth.py': ('resolve_codex_runtime_credentials',),
    'hermes_cli/auth_codex.py': ('_codex_base_url', 'resolve_codex_runtime_credentials'),
    'hermes_cli/codex_models.py': ('_ranked_slugs',),
    'agent/codex_headers.py': ('codex_account_headers',),
    'agent/model_metadata.py': ('fetch_codex_catalog_entries',),
    'agent/reasoning_effort.py': ('route_supported_efforts',),
    'agent/secret_scope.py': ('get_secret',),
}


class InstallError(Exception):
    """A safe, user-facing error without configuration or credential contents."""


def check_compatibility(repo: Path) -> None:
    for relative, required in REQUIRED_SYMBOLS.items():
        try:
            tree = ast.parse((repo / relative).read_text(encoding='utf-8'))
        except (OSError, SyntaxError, UnicodeError):
            raise InstallError(f'Cannot inspect required Hermes module {relative}. Supply --hermes-repo with the checkout path.') from None
        if relative == 'hermes_cli/plugins.py':
            supported = any(
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'SESSION_RUNTIME_SELECTION_API' for target in node.targets)
                and isinstance(node.value, ast.Constant) and type(node.value.value) is int and node.value.value == 1
                for node in tree.body
            )
            if not supported:
                raise InstallError('Hermes lacks SESSION_RUNTIME_SELECTION_API = 1. Standalone installation requires this generic hook in an upstream Hermes release. See the documented integration patch for a development checkout; this installer never patches Hermes.')
        symbols = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        symbols.update(alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names)
        missing = set(required) - symbols
        if missing:
            raise InstallError(f'Incompatible Hermes API in {relative}: missing {", ".join(sorted(missing))}.')


def _plugin_files() -> dict[str, bytes]:
    source = resources.files('jev_router')
    return {name: source.joinpath(name).read_bytes() for name in PLUGIN_FILES}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_paths(home: Path) -> None:
    for path in (home, home / 'plugins', home / 'plugins' / PLUGIN, home / 'config.yaml'):
        if path.is_symlink():
            raise InstallError('Refusing a symlink at the home, plugin, or config destination. Select a physical --home directory and migrate an existing development symlink explicitly.')


def _clone(value, depth=0):
    # Detach YAML aliases so editing plugin settings cannot mutate another section.
    if depth > 100:
        raise InstallError('Configuration has recursive or excessively nested YAML.')
    if isinstance(value, dict):
        return {key: _clone(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item, depth + 1) for item in value]
    return value


def _read_config(home: Path) -> dict:
    path = home / 'config.yaml'
    try:
        loaded = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
    except (yaml.YAMLError, UnicodeError):
        raise InstallError('Cannot parse config.yaml. Fix its YAML before installing; no configuration was printed.') from None
    result = _clone({} if loaded is None else loaded)
    if not isinstance(result, dict):
        raise InstallError('config.yaml must contain a mapping.')
    plugins = result.get('plugins', {})
    if not isinstance(plugins, dict):
        raise InstallError('plugins must be a mapping.')
    enabled = plugins.get('enabled', [])
    if not isinstance(enabled, list) or not all(isinstance(name, str) for name in enabled):
        raise InstallError('plugins.enabled must be a list of names.')
    entries = plugins.get('entries', {})
    if not isinstance(entries, dict):
        raise InstallError('plugins.entries must be a mapping.')
    entry = entries.get(PLUGIN, {})
    if not isinstance(entry, dict) or not isinstance(entry.get('settings', {}), dict):
        raise InstallError('jev-router entry and settings must be mappings.')
    return result


def _set_enabled(config: dict, enabled: bool) -> None:
    plugins = config.setdefault('plugins', {})
    names = plugins.setdefault('enabled', [])
    names[:] = [name for name in names if name != PLUGIN]
    if enabled:
        names.append(PLUGIN)
    entry = plugins.setdefault('entries', {}).setdefault(PLUGIN, {})
    entry.setdefault('settings', {})['enabled'] = enabled


def _atomic_write(path: Path, data: bytes, mode=0o600) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_config(home: Path, config: dict) -> None:
    path = home / 'config.yaml'
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    _atomic_write(path, yaml.safe_dump(config, sort_keys=False).encode('utf-8'), mode)


def _verify_owned(plugin: Path) -> None:
    if not plugin.is_dir() or plugin.is_symlink():
        raise InstallError('Plugin is not an owned JevGauge directory. Existing directories and development symlinks must be migrated explicitly.')
    manifest = plugin / MANIFEST
    if manifest.is_symlink():
        raise InstallError('Refusing a symlink ownership manifest.')
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
        valid = isinstance(data, dict) and data.get('owner') == 'jevgauge' and data.get('format') == 1
        hashes = data.get('files') if valid else None
        if not isinstance(hashes, dict) or set(hashes) != set(PLUGIN_FILES):
            raise ValueError
        for name in PLUGIN_FILES:
            path = plugin / name
            if path.is_symlink() or not path.is_file() or _hash(path.read_bytes()) != hashes[name]:
                raise ValueError
    except (OSError, ValueError, UnicodeError):
        raise InstallError('Refusing unmanaged or locally modified plugin files. Back up and move the directory before reinstalling or removing it.') from None


@contextmanager
def _lock(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    path = home / '.jevgauge-install.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise InstallError('Another JevGauge operation holds the installation lock. If interrupted, confirm no installer is running before removing .jevgauge-install.lock.') from None
    os.close(descriptor)
    try:
        yield
    finally:
        path.unlink()


def operate(command: str, home: Path, repo: Path) -> None:
    _check_paths(home)
    if command in ('doctor', 'install', 'enable'):
        check_compatibility(repo)
    if command == 'doctor':
        _read_config(home)
        print('Hermes API contract is compatible. This does not verify authentication, account models, or a live provider call.')
        return
    with _lock(home):
        _check_paths(home)
        config = _read_config(home)
        plugin = home / 'plugins' / PLUGIN
        if command == 'install':
            if plugin.exists():
                _verify_owned(plugin)
                # Reinstall of identical wheel is a no-op; upgrades use uninstall/install.
                if any((plugin / name).read_bytes() != contents for name, contents in _plugin_files().items()):
                    raise InstallError('Installed plugin differs from this package. Disable and uninstall it before installing the new version; your other settings remain intact.')
            else:
                contents = _plugin_files()
                plugin.parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(prefix='.jevgauge-', dir=plugin.parent))
                try:
                    for name, data in contents.items():
                        (temporary / name).write_bytes(data)
                    (temporary / MANIFEST).write_text(json.dumps({'owner': 'jevgauge', 'format': 1, 'files': {name: _hash(data) for name, data in contents.items()}}, indent=2), encoding='utf-8')
                    temporary.rename(plugin)
                finally:
                    if temporary.exists():
                        for path in temporary.iterdir():
                            path.unlink()
                        temporary.rmdir()
            _set_enabled(config, True)
            _write_config(home, config)
        elif command == 'enable':
            _verify_owned(plugin)
            _set_enabled(config, True)
            _write_config(home, config)
        elif command == 'disable':
            _set_enabled(config, False)
            _write_config(home, config)
        elif command == 'uninstall':
            _verify_owned(plugin)
            _set_enabled(config, False)
            # Preserve user settings for a later reinstall, except installer-owned enable.
            settings = config['plugins']['entries'][PLUGIN]['settings']
            settings.pop('enabled', None)
            if not settings:
                config['plugins']['entries'][PLUGIN].pop('settings', None)
            if not config['plugins']['entries'][PLUGIN]:
                config['plugins']['entries'].pop(PLUGIN)
            _write_config(home, config)
            for name in (*PLUGIN_FILES, MANIFEST):
                (plugin / name).unlink()
            try:
                plugin.rmdir()
            except OSError:
                print('Owned plugin files removed; remaining user or cache files were preserved.')
        print(f'{command.capitalize()} complete. Restart Hermes Desktop to apply changes.')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Manage the JevGauge user-scoped Hermes plugin. Never patches Hermes.')
    result.add_argument('command', choices=('doctor', 'install', 'enable', 'disable', 'uninstall'))
    result.add_argument('--home', type=Path, default=Path(os.environ.get('HERMES_HOME', '~/.hermes')).expanduser(), help='Hermes home (default: HERMES_HOME or ~/.hermes)')
    result.add_argument('--hermes-repo', type=Path, help='Hermes source checkout (default: <home>/hermes-agent)')
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    home = args.home.expanduser().absolute()
    repo = (args.hermes_repo or home / 'hermes-agent').expanduser().absolute()
    try:
        operate(args.command, home, repo)
    except InstallError as exc:
        print(f'JevGauge: {exc}', file=sys.stderr)
        return 1
    except (OSError, ImportError) as exc:
        print(f'JevGauge: filesystem or package operation failed ({type(exc).__name__}). Check permissions and package installation.', file=sys.stderr)
        return 1
    return 0
