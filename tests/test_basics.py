"""Checks that run without a Claude login: translations and the opt-in defaults.

Usage: python tests/test_basics.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import i18n  # noqa: E402
import claude_state  # noqa: E402
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

# Weekly pace: 3.5 days into the week, extrapolate the average rate to the reset.
from datetime import datetime, timedelta, timezone  # noqa: E402

now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
mid = now + timedelta(days=3.5)


def pace(used, resets=mid):
    return quota_source.Quota(0, None, week_used=used, week_resets_at=resets).week_pace(now)


check(pace(20) == ("plenty", None), "20% at mid-week is plenty")
check(pace(45) == ("on_track", None), "45% at mid-week is on track")
kind, finish = pace(70)
check(kind == "short" and now < finish < mid, "70% at mid-week runs out before reset")
check(pace(30, now + timedelta(days=6.9)) is None, "no pace in the first hours of the week")
check(pace(30, None) is None, "no pace without a reset time")

# The desktop cache of a different account must not stand in for Claude Code's.
real_org = quota_source._code_org
quota_source._code_org = lambda: "org-a"
check(quota_source._other_account({"org": "org-b"}), "desktop cache of another account is skipped")
check(not quota_source._other_account({"org": "org-a"}), "desktop cache of the same account is used")
check(not quota_source._other_account({"org": None}), "desktop cache without an account id is used")
quota_source._code_org = real_org

try:
    import widget  # noqa: E402
except ImportError as e:  # Pillow / Tk missing: skip, the CI job installs them
    print(f"skip poke test ({e})")
else:
    seen = {}
    real_run, real_find = subprocess.run, widget.find_claude_cli
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    subprocess.run = lambda args, **kw: seen.update(args=args, env=kw.get("env"), cwd=kw.get("cwd")) or         SimpleNamespace(returncode=0, stderr=b"")
    widget.find_claude_cli = lambda: "claude"
    try:
        widget.poke_window()
    finally:
        subprocess.run, widget.find_claude_cli = real_run, real_find
        del os.environ["ANTHROPIC_API_KEY"]
    check(seen.get("env") is not None and "ANTHROPIC_API_KEY" not in seen["env"],
          "auto-start runs without ANTHROPIC_API_KEY")
    check("haiku" in seen.get("args", []), "auto-start uses the Haiku model")
    check("--no-session-persistence" in seen.get("args", []), "auto-start leaves no session behind")
    check(seen.get("cwd") == claude_state.POKE_DIR, "auto-start runs outside your projects")

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
