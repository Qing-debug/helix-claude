"""The route view: a window that shows what the route walker plans and why (spec 5.8).

`model.py` is the pure view model (no Tk, no Windows call); the window, the live follower and the replay controls are
added by later tasks. The package is a leaf and a boundary: nothing else in `src` imports it (the walk starts it as a
child process by module name, `python -m src.route_view`), and it imports no game-input, walker, simulator, GUI or
vision module. Both rules are import-linter contracts in `pyproject.toml` ("route_view boundaries", "route_view is a
leaf").
"""
