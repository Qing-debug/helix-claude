"""Runs the firmware's host-compiled C++ tests (firmware/test/) from pytest, so one run checks the wire contract from both sides.

Each `firmware/test/test_*.cpp` is compiled with the command in firmware/README.md ("Host tests"):
`g++ -std=c++17 -Wall -Werror -I firmware/Helix_BLE <source> -o <exe>`, with the executable in `tmp_path`, then run. It
must exit 0 and print `ALL PASS`. `test_protocol.cpp` reads the golden vectors in `firmware/test/vectors.json`, the file
`tests/input/test_protocol.py` reads too (that file's 10 skipped tests are the vectors only the board can run).

Host compile only: nothing here builds the sketch, uses `arduino-cli`, or touches a board. Without g++ on PATH the
compile-and-run test is skipped, with the reason below. It is marked `slow` (a g++ build of each source).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIRMWARE = REPO / "firmware"
HEADERS = FIRMWARE / "Helix_BLE"
VECTORS = FIRMWARE / "test" / "vectors.json"
HOST_TESTS = sorted(path.name for path in (FIRMWARE / "test").glob("test_*.cpp"))
EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
COMPILE_TIMEOUT_S = 300
RUN_TIMEOUT_S = 120


def test_the_host_test_sources_are_found():
    assert {"test_protocol.cpp", "test_notify_retry.cpp"} <= set(HOST_TESTS)
    assert VECTORS.is_file() and (HEADERS / "protocol.h").is_file() and (HEADERS / "notify_retry.h").is_file()


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("g++") is None,
                    reason="g++ is not on PATH, so the C++ host tests in firmware/test/ cannot be compiled "
                           "(firmware/README.md, 'Host tests'; the Windows setup uses the WinLibs g++)")
@pytest.mark.parametrize("source", HOST_TESTS)
def test_the_cpp_host_test_compiles_and_passes(source, tmp_path):
    exe = tmp_path / (Path(source).stem + EXE_SUFFIX)
    compile_cmd = ["g++", "-std=c++17", "-Wall", "-Werror", f"-I{HEADERS}", str(FIRMWARE / "test" / source),
                   "-o", str(exe)]
    built = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S)
    assert built.returncode == 0, f"{' '.join(compile_cmd)}\n{built.stdout}{built.stderr}"

    args = [str(VECTORS)] if source == "test_protocol.cpp" else []  # only the protocol test takes the vectors path
    ran = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    assert ran.returncode == 0, f"{exe.name} exited {ran.returncode}\n{ran.stdout}{ran.stderr}"
    assert "ALL PASS" in ran.stdout, ran.stdout
