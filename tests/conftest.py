# tests/conftest.py
"""Suite-wide guards.

Sheet generation now asks Vault for each part's lifecycle state. On a developer
machine `config.json` holds live credentials, so any test that calls
`generate_from_file` would quietly hit the network — slow, and failing off-VPN.
Stub the lookup for every test; the tests that care about state override it.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _no_live_vault_lookups(monkeypatch):
    # Stub the network boundary, not lookup_file_states itself, so the tests that
    # exercise the lookup's own config/error handling still run the real thing.
    import vault_state

    async def _no_fetch(cfg, numbers):
        return {}

    monkeypatch.setattr(vault_state, "_fetch_states", _no_fetch)
