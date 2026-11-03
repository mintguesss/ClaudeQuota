"""Checks that run without a Claude login: translations and the opt-in defaults.

Usage: python tests/test_basics.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import i18n  # noqa: E402
import quota_source  # noqa: E402

failed = 0


def check(ok, what):
    global failed
    failed += not ok
    print(("ok   " if ok else "FAIL ") + what)


missing = [k for k, v in i18n.STRINGS.items() if not v.get("zh") or not v.get("en")]
check(not missing, f"every string has zh and en {missing or ''}")

for lang in ("zh", "en"):
    i18n.set_lang(lang)
    check(i18n.t("left.hm", h=2, m=5) != "", f"formatting works in {lang}")

check(quota_source.ALLOW_REFRESH is False, "token refresh is off by default")

with open(os.path.join(os.path.dirname(__file__), "..", "widget.py"), encoding="utf-8") as f:
    src = f.read()
check('st.get("auto_poke", False)' in src, "auto-start after reset is off by default")
check('st.get("token_refresh", False)' in src, "token refresh setting defaults to off")

# Auto-start must bill the subscription: with ANTHROPIC_API_KEY in the environment,
# `claude -p` uses the API key instead, so the 5-hour window never starts.
import subprocess  # noqa: E402
from types import SimpleNamespace  # noqa: E402

try:
    import widget  # noqa: E402
except ImportError as e:  # Pillow / Tk missing: skip, the CI job installs them
    print(f"skip poke test ({e})")
else:
    seen = {}
    real_run, real_find = subprocess.run, widget.find_claude_cli
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    subprocess.run = lambda args, **kw: seen.update(args=args, env=kw.get("env")) or SimpleNamespace(returncode=0)
    widget.find_claude_cli = lambda: "claude"
    try:
        widget.poke_window()
    finally:
        subprocess.run, widget.find_claude_cli = real_run, real_find
        del os.environ["ANTHROPIC_API_KEY"]
    check(seen.get("env") is not None and "ANTHROPIC_API_KEY" not in seen["env"],
          "auto-start runs without ANTHROPIC_API_KEY")
    check("haiku" in seen.get("args", []), "auto-start uses the Haiku model")

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
