# modbus-adapter — Claude Code guidance

The full guidance for this component lives in `AGENTS.md` and is shared with every agent tool. It is
imported here in full:

@AGENTS.md

## Local development

- `requirements.txt`/`pyproject.toml` pin the `edgecommons` library by git ref (it is not on public
  PyPI). To iterate against a local monorepo checkout instead, run `pip install -e ../core/libs/python`
  after the initial `pip install -e . -r requirements-test.txt` — it overlays the editable sibling on
  top of the pinned line.
- Run the unit suite with `python -m pytest` (the 90% coverage gate rides `pyproject.toml` addopts;
  add `--no-cov` when iterating on a single file).
- Run the live HOST smoke against the always-on lab Modbus sim (`ggcommons-modbus-sim` at
  `192.168.1.224:5020`) or a local `python validation/modbus_sim_server.py`, per `validation/README.md`.
