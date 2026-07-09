"""Import-sweep the integration against the installed Home Assistant core.

Run inside a venv holding the DECLARED FLOOR version of homeassistant
(hacs.json "homeassistant" key) plus the integration's own runtime
requirements (manifest.json). Any module-level use of a core API that does
not exist at the floor fails the import and this script.

Limitations (documented, not fixable here): runtime-only attribute access
(e.g. calling a method added in a newer core) is not caught — only
module-level imports and class-definition-time references are.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import pkgutil
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    floor = json.loads((REPO_ROOT / "hacs.json").read_text())["homeassistant"]
    import homeassistant.const

    installed = homeassistant.const.__version__
    if not installed.startswith(floor.rsplit(".", 1)[0]):
        print(f"WARNING: installed HA {installed} does not match declared floor {floor}")

    package = importlib.import_module("custom_components.fluidra_pool")
    count = 1
    failures: list[str] = []
    for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        try:
            importlib.import_module(module_info.name)
            count += 1
        except Exception as err:
            failures.append(f"{module_info.name}: {type(err).__name__}: {err}")

    if failures:
        print(f"FLOOR COMPAT FAILURES against homeassistant=={installed}:")
        print("\n".join(failures))
        return 1
    print(f"OK: imported {count} modules against homeassistant=={installed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
