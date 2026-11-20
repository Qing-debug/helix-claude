"""The bot's virtual-key tables live in a pure module, so the offline checkers never import listener.py (W0-4)."""
import subprocess
import sys
from pathlib import Path

import pytest

from src.input.harness import analyze, listener, vk_codes

REPO = Path(__file__).resolve().parents[2]

LISTENER = "src.input.harness.listener"


def _loaded_after_importing(module: str) -> set[str]:
    """The names in `sys.modules` of a fresh interpreter that did nothing but `import src.input.harness.<module>`.

    A child process, because this process has already imported the listener. `-I` drops the current directory from
    `sys.path`, so the repo root is added by hand. It sees every way a module can be reached (`from x import listener`,
    a relative import, an import inside a function that runs at import time), which a scan of the source does not.
    """
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import src.input.harness." + module
            + "; print('\\n'.join(sys.modules))")
    done = subprocess.run([sys.executable, "-I", "-c", code, str(REPO)], capture_output=True, text=True, timeout=120,
                          check=True)
    return set(done.stdout.split())


def test_the_tables_are_one_object_under_every_name():
    assert listener.VK_BY_KEY is vk_codes.VK_BY_KEY
    assert listener.BOT_VKS is vk_codes.BOT_VKS
    assert analyze.VK_BY_KEY is vk_codes.VK_BY_KEY
    assert analyze.BOT_VKS is vk_codes.BOT_VKS


@pytest.mark.parametrize("module", ["analyze", "realism", "vk_codes"])
def test_the_offline_checkers_do_not_load_the_listener(module):
    assert LISTENER not in _loaded_after_importing(module)


@pytest.mark.parametrize("module", ["analyze", "vk_codes"])
def test_these_load_no_windows_api(module):
    # realism is left out: it imports numpy, which loads ctypes
    assert "ctypes" not in _loaded_after_importing(module)
