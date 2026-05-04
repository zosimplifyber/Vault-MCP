"""GUI front-ends for the Vault MCP suite.

This package exposes the four Tk-based desktop GUIs that ship with the
project. Each module defines a ``launch_*`` entry point that ``app.py``
calls after signing in to Vault, so the GUIs share the single
authenticated session created at launch.

* ``gui.launcher``         — Vault Integration launcher dashboard
* ``gui.release_workflow`` — Release Workflow wizard
* ``gui.purchasing``       — Purchasing-sheet generator
* ``gui.mfg_package``      — Manufacturing package builder

The GUIs depend on root-level engine modules (``vault_rest_api``,
``mcp_server``, ``bom_purchasing``, ``mfg_package``, ``pdf_watermark``)
and on a few helpers under ``scripts/`` (``check_item_properties``,
``release_workflow``). ``app.py`` adds the project root and ``scripts/``
to ``sys.path`` before importing this package.
"""
