"""Anti-cheat name rules (H56, H67; spec 5.12-4 and 5.12-5)."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def scanned_files(repo_root: Path) -> list[Path]:
    """Every src/**/*.py and tools/**/*.py under repo_root, minus those under src/input/harness/."""
    harness_dir = repo_root / "src" / "input" / "harness"
    files = [*(repo_root / "src").glob("**/*.py"), *(repo_root / "tools").glob("**/*.py")]
    return sorted(p for p in files if harness_dir not in p.parents)


def test_scope():
    names = {p.relative_to(REPO).as_posix() for p in scanned_files(REPO)}
    assert "src/gui/gui.py" in names
    assert "tools/check.py" in names
    assert not any(n.startswith("src/input/harness/") for n in names)
    assert not any(n.startswith("tests/") for n in names)


def test_capture_loop_keeps_its_pause():
    text = (REPO / "src" / "vision" / "vision.py").read_text(encoding="utf-8")
    assert "time.sleep(FRAME_DELAY_MS / 1000)" in text
