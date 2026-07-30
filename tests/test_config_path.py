# tests/test_config_path.py
"""load_config must record which file it actually read.

Tools that persist a user preference back to config.json (e.g. the BOM ->
Manufacturing Tasks dialog) need to write to the file app.py was actually
pointed at via --config, not to whichever config.json happens to sit next to
the code. Without this, running with --config my_config.json and saving a
setting silently updates the default config.json instead — the real config
in use never gets the setting.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app


def test_load_config_records_the_file_it_read(tmp_path):
    """app.py --config <other> must not make preference-saving tools write
    back to the default config.json."""
    custom = tmp_path / "my_config.json"
    custom.write_text(json.dumps({
        "vault": {"servername": "http://x", "username": "u",
                  "password": "p", "database": "d"},
    }), encoding="utf-8")

    cfg = app.load_config(custom)

    assert cfg["__path__"] == os.path.abspath(str(custom))
