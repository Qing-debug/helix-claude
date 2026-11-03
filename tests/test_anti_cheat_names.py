"""Anti-cheat name rules (H56, H67; spec 5.12-4 and 5.12-5)."""
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Each name is split into adjacent pieces so that no banned name appears in the repo as plain text (RT-11-D1,
# option B). The joined value is what the scan matches.
BANNED = (
    "Get" + "Foreground" + "Window",
    "Enum" + "Windows",
    "Enum" + "Processes",
    "Create" + "Toolhelp32" + "Snapshot",
    "Open" + "Process",
    "Find" + "Window",
    "Get" + "Window" + "Text",
    "Get" + "Class" + "Name",
    "Get" + "Window" + "Thread" + "Process" + "Id",
    "task" + "list",
    "Get-" + "Process",
    "psu" + "til",
    "im" + "show",
    "named" + "Window",
    "wait" + "Key",
)
CASE_INSENSITIVE = frozenset({"task" + "list", "Get-" + "Process", "psu" + "til"})


def scanned_files(repo_root: Path) -> list[Path]:
    """Every src/**/*.py and tools/**/*.py under repo_root, minus those under src/input/harness/."""
    harness_dir = repo_root / "src" / "input" / "harness"
    files = [*(repo_root / "src").glob("**/*.py"), *(repo_root / "tools").glob("**/*.py")]
    return sorted(p for p in files if harness_dir not in p.parents)


def banned_names(paths: Iterable[Path]) -> list[str]:
    """One "<path>:<line>: <name>" per banned name per line, comments and strings included."""
    hits = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for name in BANNED:
                if name in CASE_INSENSITIVE:
                    found = name.lower() in line.lower()
                else:
                    found = name in line
                if found:
                    hits.append(f"{path}:{number}: {name}")
    return hits


def test_no_banned_name_in_src_or_tools():
    assert banned_names(scanned_files(REPO)) == []


def test_the_scan_can_fail(tmp_path):
    fixture = tmp_path / "fixture.py"
    fixture.write_text(
        "\n".join([
            "user32." + "Get" + "Foreground" + "Window()",
            "cv2." + "im" + "show(\"x\", f)",
            "user32." + "Get" + "Window" + "Text" + "W(hwnd, buf, 256)",
            "subprocess.run([\"" + "TASK" + "LIST\"])",
        ]) + "\n",
        encoding="utf-8",
    )
    assert banned_names([fixture]) == [
        f"{fixture}:1: {BANNED[0]}",
        f"{fixture}:2: {BANNED[12]}",
        f"{fixture}:3: {BANNED[6]}",
        f"{fixture}:4: {BANNED[9]}",
    ]


def test_each_banned_name_is_caught(tmp_path):
    """Pins all 15 joined names (RT-11-R m1): each line plants one name, written with split points other than
    BANNED's, so a typo in any piece of BANNED (or a name dropped or reordered) leaves its line without its hit."""
    planted = (
        "GetFore" + "groundWindow",
        "EnumW" + "indows",
        "EnumPro" + "cesses",
        "CreateTool" + "help32Snapshot",
        "Op" + "enProcess",
        "FindW" + "indow",
        "GetWin" + "dowText",
        "GetCla" + "ssName",
        "GetWindowThread" + "ProcessId",
        "TASK" + "LIST",
        "get-pro" + "cess",
        "PSU" + "TIL",
        "ims" + "how",
        "namedW" + "indow",
        "waitK" + "ey",
    )
    fixture = tmp_path / "fixture.py"
    fixture.write_text("\n".join(planted) + "\n", encoding="utf-8")
    assert len(BANNED) == len(planted) == 15
    assert banned_names([fixture]) == [f"{fixture}:{i}: {name}" for i, name in enumerate(BANNED, start=1)]


def test_scope():
    names = {p.relative_to(REPO).as_posix() for p in scanned_files(REPO)}
    assert "src/gui/gui.py" in names
    assert "tools/check.py" in names
    assert not any(n.startswith("src/input/harness/") for n in names)
    assert not any(n.startswith("tests/") for n in names)


def test_capture_loop_keeps_its_pause():
    """The grab waits inside Windows for a new frame (H77); the loop keeps only its 20 ms floor sleep (H80)."""
    text = (REPO / "src" / "vision" / "vision.py").read_text(encoding="utf-8")
    assert "time.sleep(THREAD_SLEEP_DURATION_S)" in text
    assert "FRAME_DELAY_MS" not in text
