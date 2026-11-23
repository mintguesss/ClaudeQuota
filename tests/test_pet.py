"""Desktop pet: reading Claude Code's state from transcripts, and the optional hook.

Usage: python tests/test_pet.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import claude_state as cs  # noqa: E402

failed = 0


def check(ok, what):
    global failed
    failed += not ok
    print(("ok   " if ok else "FAIL ") + what)


NOW = time.time()


def at(sec_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(NOW - sec_ago)) + f".{int((NOW % 1) * 1000):03d}Z"


def assistant(sec_ago, stop, tools=(), error=False):
    content = [{"type": "tool_use", "name": n} for n in tools] or [{"type": "text", "text": "ok"}]
    e = {"type": "assistant", "timestamp": at(sec_ago), "cwd": r"C:\dev\shop-api",
         "message": {"stop_reason": stop, "content": content}}
    if error:
        e["isApiErrorMessage"] = True
    return e


def user(sec_ago, text=None):
    content = [{"type": "text", "text": text}] if text else [{"type": "tool_result"}]
    return {"type": "user", "timestamp": at(sec_ago), "cwd": r"C:\dev\shop-api", "message": {"content": content}}


def kind(*entries, **kw):
    s = cs.session_state(list(entries), NOW, **kw)
    return s and s.kind


check(kind(user(5, "add pagination")) == "working", "a fresh prompt means working")
check(kind(assistant(3, "tool_use", ["Read"])) == "working", "a read tool in flight is working")
check(kind(assistant(60, "tool_use", ["Read"])) == "working", "a slow read tool is still working, not waiting")
check(kind(assistant(60, "tool_use", ["Bash"])) == "waiting", "a Bash call with no result for a minute looks like waiting")
check(kind(assistant(60, "tool_use", ["Bash"]), guess_waiting=False) == "working",
      "with the hook installed, it doesn't guess waiting")
check(kind(assistant(30, "end_turn")) == "done", "an answer that just finished is done")
check(kind(assistant(600, "end_turn")) == "idle", "an answer from ten minutes ago is idle")
check(kind(assistant(10, None, error=True)) == "error", "an API error is an error")
check(kind(user(5, "[Request interrupted by user]")) == "idle", "an interrupted turn is idle")
check(cs.session_state([user(5, "hi")], NOW).project == "shop-api", "the project name comes from cwd")

hook = {"transcript": "x", "at": NOW - 2}
check(kind(assistant(3, "tool_use", ["Bash"]), hook=hook) == "waiting", "a hook notification after the call is waiting")
check(kind(user(1), hook=hook) == "working", "once you answer, the old notification no longer counts")

# Several sessions at once: the one that needs you most comes first, and all of them are listed.
with tempfile.TemporaryDirectory() as d:
    for proj, entries in (("a", [user(5, "go")]), ("b", [assistant(60, "tool_use", ["Bash"])]),
                          ("c", [assistant(30, "end_turn")])):
        os.makedirs(os.path.join(d, proj))
        with open(os.path.join(d, proj, "s.jsonl"), "w", encoding="utf-8") as fh:
            for e in entries:
                e = dict(e, cwd=f"C:\\dev\\{proj}")
                fh.write(json.dumps(e) + "\n")
    real_projects = cs.PROJECTS
    cs.PROJECTS = d
    best = cs.current(now=NOW)
    cs.PROJECTS = real_projects
    check(best.kind == "waiting" and best.project == "b", "the session waiting for you comes first")
    check([s.project for s in cs.last_sessions] == ["b", "a", "c"], "every active session is listed, most urgent first")

# A session you named is called by that name, not by its folder.
with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, "p"))
    path = os.path.join(d, "p", "s.jsonl")

    def write(*entries, partial=""):
        with open(path, "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            fh.write(partial)

    def named():
        real_projects = cs.PROJECTS
        cs.PROJECTS = d
        best = cs.current(now=NOW)
        cs.PROJECTS = real_projects
        return best.project

    write(user(5, "go"))
    check(named() == "shop-api", "a session you never named is called by its folder")
    write({"type": "custom-title", "customTitle": "結帳流程", "sessionId": "x"},
          *[user(5, "go")] * 300)  # the name sits far from the end of the file
    check(named() == "結帳流程", "a session you named is called by that name")
    write(user(4, "more"), partial='{"type":"custom-title","customTitle":"半')
    check(named() == "結帳流程", "a half-written line is ignored until it's finished")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('行"}\n')
    check(named() == "半行", "renaming the session picks up the new name")
    write({"type": "custom-title", "customTitle": "x" * 40})
    check(len(named()) == cs.TITLE_MAX and named().endswith("…"), "a long name is shortened")
    check(cs.session_title(os.path.join(d, "missing.jsonl")) == "", "a missing file has no name")

# A session named in the desktop app's sidebar is called by that name; /rename still wins.
with tempfile.TemporaryDirectory() as d:
    proj = os.path.join(d, "projects", "p")
    os.makedirs(proj)
    with open(os.path.join(proj, "abc-123.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(user(5, "go")) + "\n")
    meta = os.path.join(d, "desktop", "acct", "org")
    os.makedirs(meta)
    with open(os.path.join(meta, "local_1.json"), "w", encoding="utf-8") as fh:
        json.dump({"cliSessionId": "abc-123", "title": "Skills 製作", "titleSource": "auto"}, fh, ensure_ascii=False)
    real = cs.PROJECTS, cs.DESKTOP_SESSIONS, dict(cs._desktop)
    cs.PROJECTS, cs.DESKTOP_SESSIONS = os.path.join(d, "projects"), [os.path.join(d, "desktop")]
    cs._desktop.update(at=0.0, files={}, titles={}, busy=False)
    cs.desktop_titles(wait=True)
    check(cs.current(now=NOW).project == "Skills 製作", "a session named in the desktop app uses that name")
    with open(os.path.join(proj, "abc-123.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "custom-title", "customTitle": "結帳"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps(user(4, "more")) + "\n")
    check(cs.current(now=NOW).project == "結帳", "a /rename name wins over the desktop app's name")
    cs.PROJECTS, cs.DESKTOP_SESSIONS = real[0], real[1]
    cs._desktop.clear()
    cs._desktop.update(real[2])

with tempfile.TemporaryDirectory() as d:
    f = os.path.join(d, "n.json")
    with open(f, "w", encoding="utf-8") as fh:
        json.dump({"message": "Claude needs your permission to use Bash", "transcript_path": "C:\\a.jsonl"}, fh)
    check(cs.read_hook(f) is not None, "a permission notification is read")
    with open(f, "w", encoding="utf-8") as fh:
        json.dump({"message": "Claude is waiting for your input", "notification_type": "idle_prompt"}, fh)
    check(cs.read_hook(f) is None, "the idle reminder after a finished turn is not 'waiting'")

# The hook only touches its own entry in ~/.claude/settings.json.
try:
    import pet  # noqa: E402
except ImportError as e:
    print(f"skip hook tests ({e})")
else:
    with tempfile.TemporaryDirectory() as d:
        settings = os.path.join(d, "settings.json")
        theirs = {"model": "opus", "hooks": {"Notification": [{"matcher": "", "hooks": [
            {"type": "command", "command": "notify-send hi"}]}], "Stop": [{"hooks": []}]}}
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump(theirs, fh)
        pet.install_hook(settings, d)
        pet.install_hook(settings, d)  # twice must not add a second entry
        data = json.load(open(settings, encoding="utf-8"))
        cmds = [h["command"] for g in data["hooks"]["Notification"] for h in g["hooks"]]
        check(len(cmds) == 2 and "notify-send hi" in cmds, "install keeps the user's own hooks and adds one")
        check(pet.hook_installed(settings), "the hook shows as installed")
        check(os.path.exists(settings + ".claudequota.bak"), "a backup is kept before the first change")
        check("$" not in cmds[1], "the hook command has no $ for bash or cmd to expand")
        pet.uninstall_hook(settings, d)
        check(json.load(open(settings, encoding="utf-8")) == theirs, "uninstall restores the settings exactly")

        empty = os.path.join(d, "fresh", "settings.json")
        pet.install_hook(empty, d)
        pet.uninstall_hook(empty, d)
        check(json.load(open(empty, encoding="utf-8")) == {}, "install and remove on a missing file leaves it empty")

        broken = os.path.join(d, "broken.json")
        open(broken, "w").write("{not json")
        try:
            pet.install_hook(broken, d)
            check(False, "a broken settings file is not overwritten")
        except ValueError:
            check(open(broken).read() == "{not json", "a broken settings file is not overwritten")

    # Custom characters: a folder of images, with missing states falling back.
    from PIL import Image  # noqa: E402

    with tempfile.TemporaryDirectory() as d:
        folder = os.path.join(pet.custom_root(d), "my-dog")
        os.makedirs(folder)
        Image.new("RGBA", (40, 80), (200, 100, 50, 255)).save(os.path.join(folder, "idle.png"))
        Image.new("RGBA", (30, 30), (50, 100, 200, 255)).save(os.path.join(folder, "working_1.png"))
        Image.new("RGBA", (30, 30), (50, 200, 100, 255)).save(os.path.join(folder, "working_2.png"))
        check(pet.custom_characters(d) == ["my-dog"], "a folder with images shows up as a character")
        w1 = pet.custom_frame(folder, "working", 0, 1.0, (1, 2, 3))
        w2 = pet.custom_frame(folder, "working", 1, 1.0, (1, 2, 3))
        check(w1.tobytes() != w2.tobytes(), "numbered images play as an animation")
        done = pet.custom_frame(folder, "done", 0, 1.0, (1, 2, 3))
        check((200, 100, 50) in {c for _, c in done.getcolors(1 << 16)}, "a missing state falls back to idle")
        check(done.size == (pet.S, pet.S), "custom images are fitted into the pet's square")
        check(pet.custom_fps(folder) == 1000 / pet.FRAME_MS, "without pet.json it plays at the built-in pace")
        with open(os.path.join(folder, "pet.json"), "w") as fh:
            json.dump({"fps": 24}, fh)
        check(pet.custom_fps(folder) == 24, "pet.json sets the playback speed")
        check(pet.custom_count(folder, "working") == 2, "a numbered sequence counts every frame")
        open(os.path.join(folder, "a.wav"), "wb").write(b"RIFF")
        open(os.path.join(folder, "b.wav"), "wb").write(b"RIFF")
        with open(os.path.join(folder, "pet.json"), "w") as fh:
            json.dump({"fps": 24, "music": {"done": "a.wav", "awake": "b.wav", "idle": "missing.wav"}}, fh)
        m = pet.custom_music(folder)
        check(set(m) == {"done", "awake"} and m["done"].endswith("a.wav"), "each state can have its own song")
        with open(os.path.join(folder, "pet.json"), "w") as fh:
            json.dump({"music": {"file": "a.wav", "states": ["done", "awake"]}}, fh)
        check(set(pet.custom_music(folder)) == {"done", "awake"}, "one song for several states still works")
        pet.ensure_custom_root(d, "zh")
        check(os.path.exists(os.path.join(pet.custom_root(d), "README.txt")), "the custom folder gets a README")

    lines, bw, bh = pet.bubble_layout("ClaudeQuota is waiting for your permission to run a long command", 1.0)
    check(len(lines) >= 2 and bw <= pet.BUBBLE_W + 40, "long bubble text wraps instead of growing wide")
    img = pet.bubble_image("做完了！", "史萊姆", 2, False, 1.0, (1, 2, 3), 30)
    check(img.size == pet.bubble_layout("做完了！", 1.0)[1:], "the bubble image matches its layout")

    # Menus: custom themes from a JSON file, and staying clear of the pet.
    import fancymenu  # noqa: E402

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "themes.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"duckling": {"label": "Duckling", "bg": [255, 240, 246], "deco": "duck"},
                       "paper": {"bg": [0, 0, 0]}}, fh)
        fancymenu.load_custom_themes(path)
        check("duckling" in fancymenu.THEMES and fancymenu.LABELS["duckling"] == "Duckling",
              "a custom theme loads with its own name")
        check(fancymenu.THEMES["duckling"]["accent"] == fancymenu.THEMES["paper"]["accent"],
              "colors a custom theme leaves out come from the default theme")
        check(fancymenu.THEMES["paper"]["bg"] != (0, 0, 0), "a custom file can't overwrite a built-in theme")
        fancymenu.load_custom_themes(os.path.join(d, "missing.json"))
        check("duckling" not in fancymenu.THEMES, "removing the file removes the custom theme")
    area = (0, 0, 1920, 1040)
    pet_box = (800, 600, 1000, 800)
    x, y = fancymenu._clear_of(pet_box, 850, 650, 300, 400, area, 8)
    check(not fancymenu._overlaps((x, y, x + 300, y + 400), pet_box), "a menu opened on the pet moves off it")
    x, y = fancymenu._clear_of(pet_box, 100, 100, 300, 200, area, 8)
    check((x, y) == (100, 100), "a menu that doesn't touch the pet stays put")

    for ch in pet.CHARACTERS:
        for st in pet.STATES:
            img = pet.frame(ch, st, 3, 1.0, (1, 2, 3))
            colors = {c for _, c in img.getcolors(1 << 16)}
            if (1, 2, 3) not in colors or len(colors) < 4:
                check(False, f"{ch}/{st} draws something on a transparent background")
                break
        else:
            continue
        break
    else:
        check(True, "every character draws every state")

print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
