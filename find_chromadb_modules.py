"""Find all chromadb submodules that can be imported locally.
This is to identify what needs to be in hiddenimports for PyInstaller."""
import os
import sys
import pkgutil
import subprocess

# Find all chromadb submodules
import chromadb
root = os.path.dirname(chromadb.__file__)
modules = []
for m in pkgutil.walk_packages([root], prefix="chromadb."):
    modules.append(m.name)
# Add special ones
modules.append("chromadb_rust_bindings")
modules.append("overrides")

print(f"Total chromadb submodules found: {len(modules)}")

# Test each one
fail = []
ok = 0
for name in modules:
    r = subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        ok += 1
    else:
        # Extract just the error line
        err = (r.stderr or "").strip().split("\n")[-1] if r.stderr else "no stderr"
        fail.append((name, err[:150]))

print(f"Imported OK: {ok}")
print(f"Failed: {len(fail)}")
for name, err in fail:
    print(f"  {name}")
    print(f"    {err}")
