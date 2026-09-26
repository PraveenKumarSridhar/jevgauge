# Installer behavior

Use `python -m jevgauge --help` for arguments. Every command accepts `--home`; doctor/install/enable also inspect `--hermes-repo` (default `<home>/hermes-agent`).

| Command | Effect |
|---|---|
| doctor | Read source API/config shape; no network, credential reads, or writes |
| install | Validate compatibility, copy packaged plugin, write owned-file hashes, enable |
| enable | Validate compatibility and owned files, enable new routing |
| disable | Disable future decisions; works without a compatible checkout |
| uninstall | Disable, remove unchanged owned files; preserve other settings/files |

Close Desktop and other processes editing Hermes config before mutations. A lock serializes JevGauge installers, not other applications. YAML comments/formatting normalize; unrelated values are retained. Config writes are atomic. An interrupted install may leave a disabled managed copy that can be safely installed again. Never remove a stale lock until you confirm no installer is running.

When cache or user files remain after uninstall, a small ownership record remains with them. Reinstall uses that record to restore only the managed files. Modified managed files are still refused. Python caches are preserved; the restored source timestamp changes so a same-size upgrade cannot execute stale bytecode.

Existing plugin symlinks or unmanaged directories are refused. For development symlinks, keep the symlink and configure the plugin manually, or back it up and move it aside before managed installation. There is intentionally no force-overwrite flag.

## Clean wheel smoke

```sh
python -m build
python scripts/smoke_install.py --wheel dist/jevgauge-0.1.0-py3-none-any.whl --hermes-repo /path/to/integrated/hermes-agent
```

This creates an isolated environment and a temporary home with spaces in the path. It exercises install twice, an actual deployed-plugin import that creates Python bytecode, disable, enable, uninstall/reinstall, unrelated configuration preservation, and rejection of the unmodified source API. No provider call is made. The wheel's dependencies are fetched from public PyPI.

## Troubleshooting

- **Missing API marker:** the checkout is stock or unsupported. Follow the explicit integration guide. Adding only the marker is not integration.
- **No panel:** confirm both the GUI and backend came from the integrated checkout, then start a new eligible chat.
- **Defaults retained:** check `/jev-status`. Typical causes: missing TypeSafe key, no account candidates, timeout, or confidence below threshold.
- **Nothing routes:** check enabled list + plugin setting, launch profile, `openai-codex`, and whether this is already an existing conversation.
- **Changed managed files:** back up/move the directory. Uninstall will not delete local modifications.
- **Auth works in a terminal but not Desktop:** ensure the key is in the Hermes launch home's secret environment. Shell-only exports may not reach GUI launches.
