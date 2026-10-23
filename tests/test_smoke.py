def test_src_package_imports():
    from src.controller.node import Node  # noqa: F401  (pure module, no hardware)
    import bleak, numpy, networkx, returns  # noqa: F401
