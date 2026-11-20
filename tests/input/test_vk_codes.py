"""The bot's virtual-key tables live in a pure module, so the offline checkers never import listener.py (W0-4)."""
import ast
from pathlib import Path

from src.input.harness import analyze, listener, vk_codes

HARNESS = Path(vk_codes.__file__).parent


def _imported_modules(path: Path) -> set[str]:
    """Every module a file names in an import statement, anywhere in it (also the ones inside functions)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_the_tables_are_one_object_under_every_name():
    assert listener.VK_BY_KEY is vk_codes.VK_BY_KEY
    assert listener.BOT_VKS is vk_codes.BOT_VKS
    assert analyze.VK_BY_KEY is vk_codes.VK_BY_KEY
    assert analyze.BOT_VKS is vk_codes.BOT_VKS


def test_analyze_does_not_import_the_listener():
    modules = _imported_modules(HARNESS / "analyze.py")
    assert "src.input.harness.listener" not in modules
    assert "src.input.harness.vk_codes" in modules


def test_the_vk_module_imports_no_windows_api():
    assert "ctypes" not in _imported_modules(HARNESS / "vk_codes.py")
