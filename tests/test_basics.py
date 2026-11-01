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

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
