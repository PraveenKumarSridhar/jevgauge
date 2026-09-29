"""Install a user-scoped plugin without modifying the Hermes checkout.

Compatibility checks are deliberately static: doctor never imports Hermes, reads
credentials, or sends network requests. Its success proves an API contract, not
account entitlement or live routing success.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

import yaml

PLUGIN = 'jev-router'
PLUGIN_FILES = (
    '__init__.py', 'plugin.yaml', 'telemetry.py', 'runtime.json',
    'dashboard/__init__.py', 'dashboard/manifest.json', 'dashboard/plugin_api.py',
    'dashboard/dist/__init__.py', 'dashboard/dist/index.js',
)
LEGACY_PLUGIN_FILE_SETS = (
    {'__init__.py', 'plugin.yaml'},
    {'__init__.py', 'plugin.yaml', 'telemetry.py', 'runtime.json'},
)
MANIFEST = '.jevgauge-install.json'
DESKTOP_MANIFEST = '.jevgauge-desktop-install.json'
LEGACY_DESKTOP_MANIFEST = '.jevgauge-ui-install.json'
REQUIRED_SYMBOLS = {
    'hermes_cli/plugins.py': ('has_hook', 'invoke_hook', 'register_hook', 'register_middleware',
                              'register_command', 'get_config'),
}
NATIVE_REQUIRED_SYMBOLS = {
    'hermes_cli/plugins.py': ('get_secret',),
}
LEGACY_REQUIRED_SYMBOLS = {
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


def _assigned_constant(tree: ast.AST, name: str, expected) -> bool:
    for node in getattr(tree, 'body', ()):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if (any(isinstance(target, ast.Name) and target.id == name for target in targets)
                and isinstance(node.value, ast.Constant) and node.value.value == expected
                and type(node.value.value) is type(expected)):
            return True
    return False


def _declares_rpc(tree: ast.AST, name: str) -> bool:
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if (isinstance(call.func, ast.Name) and call.func.id == 'method' and call.args
                and isinstance(call.args[0], ast.Constant) and call.args[0].value == name):
            return True
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and decorator.args[0].value == name):
                return True
    return False


def _parse_optional(repo: Path, relative: str) -> ast.AST | None:
    try:
        return ast.parse((repo / relative).read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeError):
        return None


def _routing_contract(repo: Path, plugins_tree: ast.AST) -> str | None:
    middleware = _parse_optional(repo, 'hermes_cli/middleware.py')
    resolver = _parse_optional(repo, 'hermes_cli/turn_routing.py')
    read_api = _parse_optional(repo, 'tui_gateway/methods_turn_route.py')
    middleware_symbols = ({node.name for node in ast.walk(middleware)
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                          if middleware is not None else set())
    resolver_symbols = ({node.name for node in ast.walk(resolver)
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                        if resolver is not None else set())
    native = (
        middleware is not None
        and _assigned_constant(middleware, 'TURN_ROUTE_MIDDLEWARE', 'turn_route')
        and _assigned_constant(middleware, 'TURN_ROUTE_API_VERSION', 1)
        and 'apply_turn_route_middleware' in middleware_symbols
        and 'resolve_turn_route' in resolver_symbols
        and read_api is not None
        and _declares_rpc(read_api, 'session.turn_route.read')
    )
    if native:
        return 'turn_route'
    if _assigned_constant(plugins_tree, 'SESSION_RUNTIME_SELECTION_API', 1):
        return 'select_session_runtime'
    if _routed_start_contract(repo):
        return 'routed_start'
    return None


def _routed_start_contract(repo: Path) -> bool:
    """Detect the stock, source-free routed-session seam Jev actually consumes."""
    sessions = _parse_optional(repo, 'tui_gateway/contracts/sessions.py')
    prompt = _parse_optional(repo, 'tui_gateway/contracts/prompt_voice.py')
    config_models = _parse_optional(repo, 'tui_gateway/contracts/config_free_tier_control.py')
    methods = _parse_optional(repo, 'tui_gateway/methods_session.py')
    dashboard = _parse_optional(repo, 'hermes_cli/web_server_dashboard.py')
    secrets = _parse_optional(repo, 'agent/secret_scope.py')
    config = _parse_optional(repo, 'hermes_cli/config.py')
    if None in (sessions, prompt, config_models, methods, dashboard, secrets, config):
        return False
    fields = set()
    for node in sessions.body:
        if isinstance(node, ast.ClassDef) and node.name == 'SessionCreateParams':
            fields = {item.target.id for item in node.body
                      if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
    if not {'model', 'provider', 'reasoning_effort'} <= fields:
        return False
    if (not _declares_rpc(sessions, 'session.create') or not _declares_rpc(prompt, 'prompt.submit')
            or not _declares_rpc(config_models, 'model.options')
            or not _declares_rpc(config_models, 'config.get')):
        return False
    functions = {node.name: node for node in ast.walk(methods)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    create = functions.get('_create_session')
    if '_create_overrides' not in functions or create is None:
        return False
    scheduled = [node.lineno for node in ast.walk(create)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == '_schedule_agent_build']
    bound = [node.lineno for node in ast.walk(create)
             if isinstance(node, (ast.Assign, ast.AnnAssign))
             and any(isinstance(target, ast.Name) and target.id in {
                 'model_override', 'session_model_override', 'reasoning_override', 'create_reasoning_override'}
                     for root in (node.targets if isinstance(node, ast.Assign) else [node.target])
                     for target in ast.walk(root))]
    if not scheduled or not bound or max(bound) >= min(scheduled):
        return False
    dashboard_functions = {node.name for node in ast.walk(dashboard)
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not {'_plugin_route_secret_scope', '_mount_plugin_api_routes'} <= dashboard_functions:
        return False
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'get_secret'
               for node in ast.walk(secrets)):
        return False
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'load_config_readonly'
               for node in ast.walk(config)):
        return False
    try:
        sdk = (repo / 'apps/desktop/src/sdk/index.ts').read_text(encoding='utf-8')
        plugin_sdk = (repo / 'apps/desktop/src/contrib/plugin.ts').read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return False
    return (all(symbol in sdk for symbol in ('profileRoutes', 'requestProfile', 'retainProfile', 'openSession'))
            and 'rest:' in plugin_sdk)


def _check_symbols(repo: Path, requirements: dict[str, tuple[str, ...]], parsed: dict) -> None:
    for relative, required in requirements.items():
        try:
            tree = parsed.get(relative) or ast.parse((repo / relative).read_text(encoding='utf-8'))
        except (OSError, SyntaxError, UnicodeError):
            raise InstallError(f'Cannot inspect required Hermes module {relative}. Supply --hermes-repo with the checkout path.') from None
        parsed[relative] = tree
        symbols = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        symbols.update(alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names)
        missing = set(required) - symbols
        if missing:
            raise InstallError(f'Incompatible Hermes API in {relative}: missing {", ".join(sorted(missing))}.')


def check_compatibility(repo: Path) -> str:
    parsed = {}
    _check_symbols(repo, REQUIRED_SYMBOLS, parsed)
    contract = _routing_contract(repo, parsed['hermes_cli/plugins.py'])
    if not contract:
        raise InstallError(
            'Hermes lacks the routed session contract Jev needs, the native turn_route plus '
            'session.turn_route.read contract, and the legacy SESSION_RUNTIME_SELECTION_API = 1 contract. Update Hermes to a compatible '
            'release or use the documented isolated development integration; this installer never patches Hermes.')
    _check_symbols(
        repo,
        NATIVE_REQUIRED_SYMBOLS if contract == 'turn_route' else
        (LEGACY_REQUIRED_SYMBOLS if contract == 'select_session_runtime' else {}),
        parsed,
    )
    return contract


def _plugin_files() -> dict[str, bytes]:
    source = resources.files('jev_router')
    return {
        '__init__.py': source.joinpath('__init__.py').read_bytes(),
        'plugin.yaml': source.joinpath('plugin.yaml').read_bytes(),
        'telemetry.py': resources.files('jevgauge').joinpath('telemetry.py').read_bytes(),
        'runtime.json': json.dumps({'python': sys.executable}).encode('utf-8'),
        'dashboard/__init__.py': source.joinpath('dashboard/__init__.py').read_bytes(),
        'dashboard/manifest.json': source.joinpath('dashboard/manifest.json').read_bytes(),
        'dashboard/plugin_api.py': source.joinpath('dashboard/plugin_api.py').read_bytes(),
        'dashboard/dist/__init__.py': source.joinpath('dashboard/dist/__init__.py').read_bytes(),
        'dashboard/dist/index.js': source.joinpath('dashboard/dist/index.js').read_bytes(),
    }


def _desktop_plugin_bytes() -> bytes:
    return resources.files('jev_router').joinpath('desktop/plugin.js').read_bytes()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_paths(home: Path) -> None:
    for path in (home, home / 'plugins', home / 'plugins' / PLUGIN, home / 'config.yaml',
                 home / 'desktop-plugins', home / 'desktop-plugins' / PLUGIN):
        if path.is_symlink():
            raise InstallError('Refusing a symlink at the home, plugin, Desktop plugin, or config destination. Select a physical --home directory and migrate an existing development symlink explicitly.')


def _clone(value, depth=0, budget=None):
    # Detach aliases with a total-node budget, not only a nesting-depth bound.
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if budget[0] < 0:
        raise InstallError('Configuration exceeds the expanded YAML node budget.')
    if depth > 100:
        raise InstallError('Configuration has recursive or excessively nested YAML.')
    if isinstance(value, dict):
        return {key: _clone(item, depth + 1, budget) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item, depth + 1, budget) for item in value]
    return value


def _read_config(home: Path, *, raw: bytes | None = None) -> dict:
    path = home / 'config.yaml'
    try:
        loaded = yaml.safe_load(raw.decode('utf-8')) if raw is not None else (yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {})
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
    if enabled:
        entry['update_admission'] = 'required'


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


@contextmanager
def _config_write_lock(home: Path):
    """Share Hermes' plugin-settings lock without importing the host checkout."""
    path = home / '.config.yaml.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':  # pragma: no cover - exercised on Windows CI
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':  # pragma: no cover - exercised on Windows CI
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _update_config(home: Path, *, enabled: bool, remove_installer_setting: bool = False) -> None:
    """Merge Jev-owned fields into the latest config under the shared writer lock."""
    with _config_write_lock(home):
        config = _read_config(home)
        _set_enabled(config, enabled)
        if remove_installer_setting:
            entries = config['plugins']['entries']
            if entries[PLUGIN].get('update_admission') == 'required':
                entries[PLUGIN].pop('update_admission')
            settings = entries[PLUGIN]['settings']
            settings.pop('enabled', None)
            if not settings:
                entries[PLUGIN].pop('settings', None)
            if not entries[PLUGIN]:
                entries.pop(PLUGIN)
        _write_config(home, config)


def _verify_owned(plugin: Path, *, allow_uninstalled=False) -> dict:
    if not plugin.is_dir() or plugin.is_symlink():
        raise InstallError('Plugin is not an owned JevGauge directory. Existing directories and development symlinks must be migrated explicitly.')
    manifest = plugin / MANIFEST
    if manifest.is_symlink():
        raise InstallError('Refusing a symlink ownership manifest.')
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
        valid = isinstance(data, dict) and data.get('owner') == 'jevgauge' and data.get('format') == 1
        hashes = data.get('files') if valid else None
        state = data.get('state', 'installed') if valid else None
        if state not in ('installed', 'uninstalled') or (state == 'uninstalled' and not allow_uninstalled):
            raise ValueError
        source_mtime = data.get('source_mtime', 0) if valid else 0
        if type(source_mtime) is not int or not 0 <= source_mtime < 2**32 - 1:
            raise ValueError
        if not isinstance(hashes, dict) or (set(hashes) != set(PLUGIN_FILES)
                                            and set(hashes) not in LEGACY_PLUGIN_FILE_SETS):
            raise ValueError
        for name in hashes:
            path = _owned_path(plugin, name)
            if state == 'uninstalled' and not path.exists() and not path.is_symlink():
                continue
            if path.is_symlink() or not path.is_file() or _hash(path.read_bytes()) != hashes[name]:
                raise ValueError
        return data
    except (OSError, ValueError, UnicodeError):
        raise InstallError('Refusing unmanaged or locally modified plugin files. Back up and move the directory before reinstalling or removing it.') from None


def _owned_path(plugin: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or not relative.parts or any(part in ('', '.', '..') for part in relative.parts):
        raise ValueError('invalid owned path')
    current = plugin
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError('invalid owned parent')
    return plugin / relative


def _write_owned(plugin: Path, name: str, data: bytes, mode=0o644) -> None:
    path = _owned_path(plugin, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, data, mode)


def _remove_owned(plugin: Path, name: str) -> None:
    path = _owned_path(plugin, name)
    path.unlink(missing_ok=True)
    parent = path.parent
    while parent != plugin:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _desktop_plan(home: Path, content: bytes, *, uninstall: bool = False) -> str:
    """Inspect the native extension before changing any installed files."""
    parent = home / 'desktop-plugins'
    if parent.exists() and not parent.is_dir():
        raise InstallError('Refusing a non-directory Desktop plugin root.')
    plugin = parent / PLUGIN
    if not plugin.exists():
        return 'absent'
    if not plugin.is_dir() or plugin.is_symlink():
        raise InstallError('Refusing an unmanaged Desktop plugin destination.')
    manifest = plugin / DESKTOP_MANIFEST
    legacy_manifest = plugin / LEGACY_DESKTOP_MANIFEST
    source = plugin / 'plugin.js'
    if source.is_symlink() or manifest.is_symlink() or legacy_manifest.is_symlink():
        raise InstallError('Refusing a symlink in the Desktop plugin destination.')
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
            state = data.get('state', 'installed') if isinstance(data, dict) else None
            expected_keys = ({'owner', 'format', 'sha256'}
                             | ({'state'} if isinstance(data, dict) and 'state' in data else set()))
            valid = (isinstance(data, dict) and set(data) == expected_keys
                     and data.get('owner') == 'jevgauge' and data.get('format') == 1
                     and state in ('installed', 'uninstalling')
                     and isinstance(data.get('sha256'), str) and len(data['sha256']) == 64
                     and all(char in '0123456789abcdef' for char in data['sha256']))
            if not valid:
                raise ValueError
            if source.exists():
                if not source.is_file() or _hash(source.read_bytes()) != data['sha256']:
                    raise ValueError
            elif state != 'uninstalling':
                raise ValueError
            if legacy_manifest.exists():
                legacy = _read_legacy_desktop_manifest(
                    legacy_manifest, source, allow_missing=state == 'uninstalling')
                if legacy['sha256'] != data['sha256']:
                    raise ValueError
            if uninstall:
                return 'owned-uninstalling' if state == 'uninstalling' else 'owned'
            if state == 'uninstalling':
                raise InstallError('Desktop plugin removal was interrupted. Run uninstall again before installing.')
            if source.read_bytes() != content:
                raise ValueError
        except (OSError, ValueError, UnicodeError):
            raise InstallError('Desktop plugin differs from the JevGauge package or ownership record. Preserve it before changing the installation.') from None
        return 'owned-legacy' if legacy_manifest.exists() else 'owned'
    if legacy_manifest.exists():
        try:
            _read_legacy_desktop_manifest(legacy_manifest, source, allow_missing=uninstall)
        except (OSError, ValueError, UnicodeError):
            raise InstallError('Legacy Desktop plugin differs from its JevGauge ownership record. Preserve it before changing the installation.') from None
        if uninstall:
            return 'legacy-owned'
        if not source.exists() or source.read_bytes() != content:
            raise InstallError('Installed legacy Desktop plugin differs from this package. Uninstall it before installing the new version.')
        return 'legacy-adopt'
    if uninstall:
        return 'unmanaged'
    # Adopt an exact manual copy from this package without replacing it.
    if {entry.name for entry in plugin.iterdir()} == {'plugin.js'} and source.is_file() and source.read_bytes() == content:
        return 'adopt'
    raise InstallError('Existing Desktop plugin is unmanaged or differs from this package. Preserve it before installing.')


def _read_legacy_desktop_manifest(manifest: Path, source: Path, *, allow_missing: bool = False) -> dict:
    data = json.loads(manifest.read_text(encoding='utf-8'))
    valid = (isinstance(data, dict) and set(data) == {'owner', 'source', 'sha256'}
             and data.get('owner') == 'jevgauge' and isinstance(data.get('source'), str)
             and bool(data['source']) and isinstance(data.get('sha256'), str)
             and len(data['sha256']) == 64
             and all(char in '0123456789abcdef' for char in data['sha256']))
    if not valid:
        raise ValueError
    if source.exists():
        if not source.is_file() or _hash(source.read_bytes()) != data['sha256']:
            raise ValueError
    elif not allow_missing:
        raise ValueError
    return data


def _install_desktop(home: Path, content: bytes, plan: str) -> None:
    plugin = home / 'desktop-plugins' / PLUGIN
    if plan == 'owned':
        return
    if plan in ('adopt', 'legacy-adopt', 'owned-legacy'):
        _atomic_write(plugin / DESKTOP_MANIFEST,
                      json.dumps({'owner': 'jevgauge', 'format': 1, 'sha256': _hash(content),
                                  'state': 'installed'}).encode('utf-8'))
        if plan in ('legacy-adopt', 'owned-legacy'):
            (plugin / LEGACY_DESKTOP_MANIFEST).unlink(missing_ok=True)
        return
    plugin.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.jevgauge-desktop-', dir=plugin.parent))
    try:
        (temporary / 'plugin.js').write_bytes(content)
        (temporary / DESKTOP_MANIFEST).write_text(
            json.dumps({'owner': 'jevgauge', 'format': 1, 'sha256': _hash(content),
                        'state': 'installed'}), encoding='utf-8')
        temporary.rename(plugin)
    finally:
        if temporary.exists():
            for entry in temporary.iterdir():
                entry.unlink()
            temporary.rmdir()


def _uninstall_desktop(home: Path, plan: str) -> None:
    if plan not in ('owned', 'owned-uninstalling', 'legacy-owned'):
        return
    plugin = home / 'desktop-plugins' / PLUGIN
    source = plugin / 'plugin.js'
    manifest = plugin / DESKTOP_MANIFEST
    legacy_manifest = plugin / LEGACY_DESKTOP_MANIFEST
    if plan == 'legacy-owned':
        digest = _read_legacy_desktop_manifest(legacy_manifest, source, allow_missing=True)['sha256']
    else:
        data = json.loads(manifest.read_text(encoding='utf-8'))
        digest = data['sha256']
    _atomic_write(manifest, json.dumps({
        'owner': 'jevgauge', 'format': 1, 'sha256': digest, 'state': 'uninstalling',
    }).encode('utf-8'))
    source.unlink(missing_ok=True)
    legacy_manifest.unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)
    if not any(plugin.iterdir()):
        plugin.rmdir()


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
    contract = None
    if command in ('doctor', 'install', 'enable'):
        contract = check_compatibility(repo)
    if command == 'doctor':
        _read_config(home)
        print(f'Hermes API contract is compatible ({contract}). This does not verify authentication, account models, or a live provider call.')
        return
    with _lock(home):
        _check_paths(home)
        _read_config(home)
        plugin = home / 'plugins' / PLUGIN
        desktop_bytes = _desktop_plugin_bytes() if command in ('install', 'uninstall') else b''
        desktop_plan = (_desktop_plan(home, desktop_bytes, uninstall=command == 'uninstall')
                        if command in ('install', 'uninstall') else None)
        if command == 'install':
            if plugin.exists():
                ownership = _verify_owned(plugin, allow_uninstalled=True)
                contents = _plugin_files()
                if ownership.get('state', 'installed') == 'installed':
                    # Reinstall of identical wheel is a no-op; upgrades use uninstall/install.
                    if set(ownership['files']) != set(contents) or any((plugin / name).read_bytes() != data for name, data in contents.items()):
                        raise InstallError('Installed plugin differs from this package. Disable and uninstall it before installing the new version; your other settings remain intact.')
                else:
                    # A retained ownership tombstone allows reinstall without touching
                    # user files or caches. It also makes interrupted deletion retryable.
                    if any(_owned_path(plugin, name).exists() or _owned_path(plugin, name).is_symlink()
                           for name in set(contents) - set(ownership['files'])):
                        raise InstallError('Upgrade would overwrite an unowned plugin file. Move that file explicitly before upgrading.')
                    for name in ownership['files']:
                        _remove_owned(plugin, name)
                    previous_mtime = ownership.get('source_mtime', 0)
                    source_mtime = max(int(time.time()), int(previous_mtime) + 1)
                    ownership.update(state='uninstalled', files={name: _hash(data) for name, data in contents.items()}, source_mtime=source_mtime)
                    _atomic_write(plugin / MANIFEST, json.dumps(ownership).encode('utf-8'))
                    for name, data in contents.items():
                        _write_owned(plugin, name, data)
                    # Keep timestamp-based Python caches from executing a previous
                    # same-size version installed within the same clock second.
                    os.utime(plugin / '__init__.py', (source_mtime, source_mtime))
                    ownership['state'] = 'installed'
                    _atomic_write(plugin / MANIFEST, json.dumps(ownership).encode('utf-8'))
            else:
                contents = _plugin_files()
                plugin.parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(prefix='.jevgauge-', dir=plugin.parent))
                try:
                    for name, data in contents.items():
                        path = temporary / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                    (temporary / MANIFEST).write_text(json.dumps({'owner': 'jevgauge', 'format': 1, 'files': {name: _hash(data) for name, data in contents.items()}}, indent=2), encoding='utf-8')
                    temporary.rename(plugin)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
            _install_desktop(home, desktop_bytes, desktop_plan)
            _update_config(home, enabled=True)
        elif command == 'enable':
            _verify_owned(plugin)
            _update_config(home, enabled=True)
        elif command == 'disable':
            _update_config(home, enabled=False)
        elif command == 'uninstall':
            ownership = _verify_owned(plugin, allow_uninstalled=True) if plugin.exists() else None
            _update_config(home, enabled=False, remove_installer_setting=True)
            if ownership is not None:
                source = plugin / '__init__.py'
                if source.exists():
                    ownership['source_mtime'] = max(0, int(source.stat().st_mtime))
                ownership['state'] = 'uninstalled'
                _atomic_write(plugin / MANIFEST, json.dumps(ownership).encode('utf-8'))
                for name in ownership['files']:
                    _remove_owned(plugin, name)
                if any(path.name != MANIFEST for path in plugin.iterdir()):
                    print('Owned plugin files removed; user files and caches preserved with an ownership record for reinstall.')
                else:
                    (plugin / MANIFEST).unlink()
                    plugin.rmdir()
            _uninstall_desktop(home, desktop_plan)
        print(f'{command.capitalize()} complete. Restart Hermes Desktop to apply changes.')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Manage the JevGauge user-scoped Hermes plugin. Never patches Hermes.')
    result.add_argument('command', choices=('doctor', 'install', 'enable', 'disable', 'uninstall', 'dashboard'))
    result.add_argument('--home', type=Path, default=Path(os.environ.get('HERMES_HOME', '~/.hermes')).expanduser(), help='Hermes home (default: HERMES_HOME or ~/.hermes)')
    result.add_argument('--hermes-repo', type=Path, help='Hermes source checkout (default: <home>/hermes-agent)')
    result.add_argument('--port', type=int, default=8765, help='Loopback dashboard port (default: 8765)')
    result.add_argument('--demo', action='store_true', help='Use isolated synthetic dashboard evidence')
    result.add_argument('--open', dest='open_browser', action='store_true', help='Open the dashboard in your browser')
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    home = args.home.expanduser().absolute()
    repo = (args.hermes_repo or home / 'hermes-agent').expanduser().absolute()
    try:
        if args.command == 'dashboard':
            if not 1 <= args.port <= 65535:
                raise InstallError('Dashboard port must be between 1 and 65535.')
            from .dashboard import serve
            serve(home, port=args.port, demo=args.demo, open_browser=args.open_browser)
        else:
            if args.demo or args.open_browser or args.port != 8765:
                raise InstallError('Dashboard options only apply to the dashboard command.')
            operate(args.command, home, repo)
    except InstallError as exc:
        print(f'JevGauge: {exc}', file=sys.stderr)
        return 1
    except (OSError, ImportError) as exc:
        print(f'JevGauge: filesystem or package operation failed ({type(exc).__name__}). Check permissions and package installation.', file=sys.stderr)
        return 1
    return 0
