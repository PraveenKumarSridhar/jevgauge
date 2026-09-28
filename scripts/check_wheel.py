"""Check distributable contents without importing the source checkout."""
from pathlib import Path
import zipfile

wheels = list(Path('dist').glob('*.whl'))
assert len(wheels) == 1, 'Expected exactly one built wheel'
with zipfile.ZipFile(wheels[0]) as wheel:
    names = set(wheel.namelist())
    expected = {'jev_router/__init__.py', 'jev_router/plugin.yaml', 'jev_router/desktop/plugin.js',
                'jevgauge/__init__.py', 'jevgauge/__main__.py', 'jevgauge/cli.py',
                'jevgauge/dashboard.py', 'jevgauge/dashboard_config.py',
                'jevgauge/telemetry.py', 'jevgauge/analytics.py', 'jevgauge/pricing.json',
                'jevgauge/demo.py', 'jevgauge/static/index.html',
                'jevgauge/static/app.js', 'jevgauge/static/styles.css'}
    assert expected <= names, expected - names
    assert all(not name.startswith(('outputs/', '.env', 'tests/')) for name in names)
print('Wheel contains plugin, telemetry, dashboard API, pricing and browser assets.')
