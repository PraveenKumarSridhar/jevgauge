"""Check distributable contents without importing the source checkout."""
from pathlib import Path
import zipfile

wheels = list(Path('dist').glob('*.whl'))
assert len(wheels) == 1, 'Expected exactly one built wheel'
with zipfile.ZipFile(wheels[0]) as wheel:
    names = set(wheel.namelist())
    expected = {'jev_router/__init__.py', 'jev_router/plugin.yaml',
                'jevgauge/__init__.py', 'jevgauge/__main__.py', 'jevgauge/cli.py'}
    assert expected <= names, expected - names
    assert all(not name.startswith(('outputs/', '.env', 'tests/')) for name in names)
print('Wheel contains the plugin manifest, policy, and portable CLI.')
