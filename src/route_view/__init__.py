"""The route view: a window that shows what the route walker plans and why (spec 5.8).

`model.py` is the pure view model (no Tk, no Windows call); `window.py` is the Tk view, which packs into a parent it is
given; `follow.py` follows a run folder that is still being written; `__main__.py` is the replay command line
(`python -m src.route_view`, which only replays and which the walk no longer starts). The package is a leaf and a
boundary: only the Route tab (`src/gui/route_tab.py`) imports it, and it imports no game-input, walker, simulator, GUI or vision module.
Both rules are import-linter contracts in `pyproject.toml` ("route_view boundaries", "route_view is a leaf").
"""
