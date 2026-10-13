"""Run the headless test suites against a local Blender.

    python run_tests.py [--blender PATH]
"""

import argparse
import os
import subprocess
import sys

from install import find_blender

ROOT = os.path.dirname(os.path.abspath(__file__))
SUITES = ["tests/test_ops.py", "tests/test_chat.py", "tests/test_integration.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender")
    a = ap.parse_args()
    blender = a.blender or find_blender()
    if not blender:
        sys.exit("Blender not found; pass --blender PATH")
    env = dict(os.environ, TEST_PYTHON=sys.executable)
    failed = False
    for s in SUITES:
        print(f"== {s}")
        r = subprocess.run([blender, "-b", "--factory-startup", "--python", os.path.join(ROOT, s)],
                           capture_output=True, text=True, env=env)
        for line in r.stdout.splitlines():
            if line.startswith(("PASS", "FAIL", "Traceback", "  ", "AssertionError")) or "passed" in line:
                print(line)
        if r.returncode != 0 and "passed" not in r.stdout:  # Blender itself failed: show why
            print(f"Blender exited with {r.returncode}:")
            print("\n".join((r.stdout + "\n" + r.stderr).splitlines()[-40:]))
        failed |= r.returncode != 0
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
