"""Guard the config import contract.

Every module loads settings with:

    try:
        from private.config_live import A, B, C
    except ImportError:
        from config import A, B, C

Python aborts the WHOLE import statement if a single name is missing, so one
absent variable silently discards the entire private config (real API keys
included) and falls back to public defaults. That failure is near-invisible at
runtime -- the bot keeps running on public RPCs with no Gemini key.

These tests assert every name imported anywhere in the codebase actually exists
in config.py and in config.example.py, so the template can't drift again.
"""
import ast
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Matches both `from config import ...` and `from private.config_live import ...`
IMPORT_RE = re.compile(
    r"from\s+(?:private\.config_live|config)\s+import\s+([^\n(]+)", re.MULTILINE
)


def _module_files():
    for name in sorted(os.listdir(ROOT)):
        if name.endswith(".py") and name not in ("config.py", "config.example.py"):
            yield os.path.join(ROOT, name)


def _required_names():
    """Map of {module filename: [config names it imports]}."""
    required = {}
    for path in _module_files():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        names = []
        for chunk in IMPORT_RE.findall(src):
            for raw in chunk.split(","):
                cleaned = raw.strip().split(" as ")[0].strip()
                if cleaned and cleaned != "*":
                    names.append(cleaned)
        if names:
            required[os.path.basename(path)] = sorted(set(names))
    return required


def _defined_names(filename):
    with open(os.path.join(ROOT, filename), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def test_import_sites_were_discovered():
    """Sanity check: the regex must actually find the import sites."""
    required = _required_names()
    assert "drops.py" in required
    assert "MAX_CONTRACT_AGE_HOURS" in required["drops.py"]


@pytest.mark.parametrize("config_file", ["config.py", "config.example.py"])
def test_config_defines_every_imported_name(config_file):
    defined = _defined_names(config_file)
    missing = {
        module: [n for n in names if n not in defined]
        for module, names in _required_names().items()
    }
    missing = {m: n for m, n in missing.items() if n}
    assert not missing, (
        f"{config_file} is missing names that modules import. Because a single "
        f"missing name aborts the whole import statement, this silently discards "
        f"the private config at runtime. Missing: {missing}"
    )
