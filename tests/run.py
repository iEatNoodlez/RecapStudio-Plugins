"""Run the plugin list's tests against a Recap Studio checkout: each test_*.py in a process of its own.

    python tests/run.py                     the app found beside this repository (../App, ../RecapStudio, ../Release)
    python tests/run.py --app D:\\RecapStudio
    python tests/run.py youtube spanish     only the tests whose names have these words
    python tests/run.py --network           also the ones that ask real services (AniList)

Run them with the app's own Python (its .venv, or the packaged app's python folder), which has what the app needs.
Nothing here touches your projects: each test makes a scratch data folder of its own. The plugins tested are the
ones in this repository's features/ and sources/, as they are now (not the zips in dist/).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("words", nargs="*", help="only tests whose names have these words")
    ap.add_argument("--app", help="the Recap Studio folder (with recap/ in it)")
    ap.add_argument("--network", action="store_true", help="also tests that ask real services")
    args = ap.parse_args()
    env = dict(os.environ)
    if args.app:
        env["RECAP_APP"] = str(Path(args.app).resolve())
    if args.network:
        env["RECAP_TEST_NETWORK"] = "1"
    tests = sorted(HERE.glob("test_*.py"))
    if args.words:
        tests = [t for t in tests if any(w in t.stem for w in args.words)]
    failed = []
    for t in tests:
        start = time.time()
        r = subprocess.run([sys.executable, "-X", "utf8", str(t)], cwd=str(HERE), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        lines = (r.stdout or "").strip().splitlines()
        ok = r.returncode == 0 and lines and lines[-1].startswith(("ALL PASS", "SKIPPED"))
        print(f"{'ok  ' if ok else 'FAIL'} {t.stem:28} {time.time() - start:5.1f} s  {lines[-1] if lines else ''}")
        if not ok:
            failed.append(t.stem)
            for line in [x for x in lines if x.startswith("FAIL")] or (r.stderr or "").strip().splitlines()[-15:]:
                print("       " + line)
    print("\nALL PASS" if not failed else f"\n{len(failed)} FAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
