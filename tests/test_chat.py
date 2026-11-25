"""Desktop app chat mode: reading the chat list cache and the "stop response" button.

Usage: python tests/test_chat.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chat_state as ch  # noqa: E402
import claude_state as cs  # noqa: E402

ch.enabled = True
ch._ensure_thread = lambda: None  # 測試不讀這台電腦真的桌面版

failed = 0


def check(ok, what):
    global failed
    failed += not ok
    print(("ok   " if ok else "FAIL ") + what)


# --- Snappy ---
# 長度 12；literal "abcd"；從 4 個位元組前複製 8 個（重疊複製）
check(ch.unsnappy(b"\x0c\x0cabcd\x11\x04") == b"abcdabcdabcd", "snappy literal + overlapping copy")
check(ch.load_value(b"\xff\x11\x02" + b"\x0c\x0cabcd\x11\x04") == b"abcdabcdabcd", "compressed IndexedDB value")
check(ch.load_value(b"plain") == b"plain", "uncompressed value is left alone")
try:
    ch.unsnappy(b"\x05\x0cabcd")
    check(False, "wrong length is rejected")
except ValueError:
    check(True, "wrong length is rejected")


# --- 聊天清單 ---
def v8(s):
    try:
        data = s.encode("latin-1")
        return b'"' + bytes([len(data)]) + data
    except UnicodeEncodeError:
        data = s.encode("utf-16-le")
        return b"\x00c" + bytes([len(data)]) + data


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def entry(uid, name, settled, read):
    return (b'o"\ncreated_at' + v8("2026-11-24T08:37:35.499003Z")
            + b'"\x19current_leaf_message_uuid' + v8("3d3b9c27-f271-4449-92de-4f879716fb8c")
            + b'"\x0clast_read_at' + v8(iso(read))
            + b'"\x0flast_settled_at' + v8(iso(settled))
            + b'"\x05model' + v8("claude-opus-5-5")
            + b'"\x04name' + v8(name)
            + b'"\x08settingso"\x0ceffort_level' + v8("medium") + b"{\x01"
            + b'"\nupdated_at' + v8(iso(settled))
            + b'"\x04uuid' + v8(uid) + b"{\x14")


NOW = time.time()
A = "c77cca73-9067-4dcc-a4d1-b9c7fb5387d1"
B = "d58b19f7-2474-4ecd-b517-a14b6948cb7e"
blob = b"junk" + entry(A, "800字故事寫作", NOW - 30, NOW - 30) + entry(B, "Router notes", NOW - 60, NOW - 600)
convs = ch.parse_conversations(blob)
check(set(convs) == {A, B}, "finds both chats")
check(convs[A]["name"] == "800字故事寫作", "two-byte (Chinese) title")
check(convs[B]["name"] == "Router notes", "one-byte title")
check(abs(convs[A]["settled"] - (NOW - 30)) < 1, "settled time")
check(convs[B]["read"] < convs[B]["settled"], "read time")
older = ch.parse_conversations(entry(A, "800字故事寫作", NOW - 500, NOW - 500) + entry(A, "800字故事寫作", NOW - 30, NOW - 30))
check(abs(older[A]["settled"] - (NOW - 30)) < 1, "newest copy of the same chat wins")

# --- 停止回應按鈕 ---
check(ch.streaming_title(["傳送訊息", "800字故事寫作，重新命名工作階段"]) is None, "no stop button: not replying")
check(ch.streaming_title(["停止回應", "800字故事寫作，重新命名工作階段"]) == "800字故事寫作", "replying, with title")
check(ch.streaming_title(["Stop response", "My chat, rename session"]) == "My chat", "English UI")
check(ch.streaming_title(["停止回應"]) == "", "replying, title unknown")
check(ch.streaming_title(["停止", "Skills 製作，重新命名工作階段"]) is None, "Code tab's stop button is not a chat")

# --- 變成寵物看得懂的狀態 ---
ch._convs.clear()
ch._convs.update(convs)
ch._live.update(title=None, since=0.0)
got = {s["id"]: s for s in ch.sessions(NOW)}
check(got[A]["kind"] == "done" and not got[A]["unread"], "done, and you saw it")
check(got[B]["kind"] == "done" and got[B]["unread"], "done while you were elsewhere: unread")
ch._live.update(title="800字故事寫作", since=NOW - 5)
got = ch.sessions(NOW)
check([s["kind"] for s in got if s["name"] == "800字故事寫作"] == ["working"], "replying again hides its old done")
check(next(s for s in got if s["kind"] == "working")["id"] == A, "replying chat is matched to its id")
check(ch.sessions(NOW + 3600) == [s for s in ch.sessions(NOW + 3600) if s["kind"] == "working"], "old done expires")

states = cs.chat_states(NOW)
w = next(s for s in states if s.kind == "working")
check(w.entry == "claude-chat" and w.link == "chat:" + A, "State for a chat links to it")
d = next(s for s in states if s.kind == "done")
check(d.key == "chat:Router notes" and d.unread, "done chat State")
check(w.key == "chat:800字故事寫作", "working and done share a key per chat")
ch.enabled = False
check(ch.sessions(NOW) == [], "turned off: nothing")

if os.name == "nt":
    import ctypes
    # 別的套件改了共用 user32 的參數型別時，找視窗不能跟著壞（自動切回、聊天偵測都靠它）
    check(cs._user32() is not ctypes.windll.user32, "window lookup uses its own user32")

print("\nall good" if not failed else f"\n{failed} failed")
sys.exit(1 if failed else 0)
