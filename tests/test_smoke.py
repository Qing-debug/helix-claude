def test_src_package_imports():
    from src.controller.node import Node  # noqa: F401  (pure module, no hardware)
    import bleak  # noqa: F401
    import numpy  # noqa: F401
    import networkx  # noqa: F401
    import returns  # noqa: F401
