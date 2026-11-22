"""Make a custom desktop pet from your own video clips and photos.

Every state can come from a video clip (played back at the video's own speed), a still photo, or one
photo moved the way the built-in animals move. Backgrounds are removed with rembg, other people in the
frame are dropped, and music for a state can be cut from a clean studio track lined up with the video.

    pip install "rembg[cpu]" opencv-python-headless pillow      (ffmpeg must be on PATH)

Examples
  # clips from a performance video + photos for the rest, studio audio lined up with the video
  python tools/make_pet.py --name Star --video stage.mp4 --crop 480 \\
      --clip awake=14:10 --clip done=171.5:10 --clip working=36:8 \\
      --photo idle=lying.jpg --photo error=pout.jpg --photo waiting=wave.jpg --photo held=wave.jpg \\
      --music awake --music done --studio track.m4a

  # one cut-out photo that moves like the built-in animals (bounce, hop, icons)
  python tools/make_pet.py --name Star-mini --animal standing.png

The pet appears under Desktop pet > Character the next time you open the menu. Only use footage, photos
and music you are allowed to use, and keep pets made from other people's work to yourself.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pet  # noqa: E402

K = 4                      # frames are drawn at 64 * 4 = 256 px; the app scales them to the pet's size
SIZE = pet.S * K


# ───────────────────────────── cutting people out ─────────────────────────────

class Cutter:
    def __init__(self, model: str):
        from rembg import new_session
        self.session = new_session(model)

    def __call__(self, img: Image.Image) -> Image.Image:
        from rembg import remove
        return remove(img.convert("RGB"), session=self.session)


def keep_main_person(img: Image.Image, half_width: int | None = None) -> Image.Image:
    """Keep the person whose head is nearest the middle; drop anyone at the sides, then crop."""
    import cv2
    a = np.array(img.convert("RGBA"))
    alpha = a[:, :, 3]
    h, w = alpha.shape
    if half_width:
        ys, xs = np.nonzero(alpha[: h // 5] > 60)
        mid = xs[(xs > w * 0.25) & (xs < w * 0.75)]
        cx = int(np.median(mid)) if len(mid) else w // 2
        alpha[:, : max(0, cx - half_width)] = 0
        alpha[:, cx + half_width:] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 40).astype(np.uint8), 8)
    if n > 1:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        alpha[labels != big] = 0
    a[:, :, 3] = alpha
    out = Image.fromarray(a)
    box = out.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    return out.crop(box) if box else out


# ───────────────────────────── composing frames ─────────────────────────────

def place(person: Image.Image, scale: float | None = None, lift=0.0, dx=0.0, sq=0.0,
          height: float | None = None) -> Image.Image:
    """Put a cut-out on the pet's 64x64 square, feet on the ground line."""
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    if height:
        scale = height * K / person.height
    if scale is None:
        scale = min(54 * K / person.width, 60 * K / person.height)
    w = max(1, round(person.width * scale * (1 + sq)))
    h = max(1, round(person.height * scale * (1 - sq)))
    p = person.resize((w, h), Image.LANCZOS)
    x = round(30 * K - w / 2 + dx * K)
    y = round(pet.FEET * K - h - lift * K)
    canvas.alpha_composite(p, (x, max(0, y)))
    return canvas


def with_icon(canvas: Image.Image, state: str, f: int) -> Image.Image:
    pen = pet._Pen(K)
    pet._overlay(pen, state, f % pet.FRAMES)
    canvas.alpha_composite(pen.img)
    return canvas


# ───────────────────────────── video and audio ─────────────────────────────

def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def clip_frames(video: str, start: float, length: float, fps: int, crop: int | None, tmp: str) -> list[str]:
    folder = tempfile.mkdtemp(dir=tmp)
    vf = f"fps={fps}"
    if crop:
        vf += f",crop={crop}:ih:(iw-{crop})/2:0"
    ffmpeg("-ss", str(start), "-t", str(length), "-i", video, "-vf", vf, os.path.join(folder, "f_%04d.png"))
    return sorted(os.path.join(folder, n) for n in os.listdir(folder))[: round(length * fps)]


def _mono8k(path: str, tmp: str) -> np.ndarray:
    out = os.path.join(tmp, os.path.basename(path) + ".8k.wav")
    ffmpeg("-i", path, "-ac", "1", "-ar", "8000", out)
    with wave.open(out) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)


def _flux(x: np.ndarray, hop=80, win=512) -> np.ndarray:
    n = (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(win), axis=1))
    edges = np.geomspace(2, spec.shape[1] - 1, 13).astype(int)
    bands = np.log(np.stack([spec[:, a:b].sum(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1) + 1e-3)
    flux = np.maximum(0, np.diff(bands, axis=0, prepend=bands[:1]))
    return (flux - flux.mean(axis=0)) / (flux.std(axis=0) + 1e-9)


def studio_offset(video: str, studio: str, tmp: str, search: float = 30.0) -> float:
    """Seconds to add to a video time to get the same moment in the studio track.

    Live stages play the track straight through, so one offset fits the whole song; it is found by
    matching band-wise spectral flux (beats and notes) over the whole song, which crowd noise barely moves.
    """
    a, b = _flux(_mono8k(video, tmp)), _flux(_mono8k(studio, tmp))
    best, lag = -1e9, 0
    for L in range(-int(search * 100), int(search * 100)):
        a0, a1 = max(0, -L), min(len(a), len(b) - L)
        if a1 - a0 < 500:
            continue
        score = float((a[a0:a1] * b[a0 + L:a1 + L]).sum(axis=1).mean())
        if score > best:
            best, lag = score, L
    return lag / 100


def has_audio(path: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(r.stdout.strip())


def cut_audio(src: str, start: float, length: float, out: str, volume: float):
    ffmpeg("-ss", f"{max(0.0, start):.2f}", "-t", str(length), "-i", src, "-ac", "2", "-ar", "44100",
           "-af", f"volume={volume},afade=t=in:d=0.03,afade=t=out:st={length - 0.08}:d=0.08", out)


# ───────────────────────────── main ─────────────────────────────

def parse_pairs(items, kind):
    out = {}
    for item in items or []:
        state, _, value = item.partition("=")
        if state not in pet.STATES or not value:
            sys.exit(f"--{kind} {item!r}: use STATE=VALUE, STATE one of {', '.join(pet.STATES)}")
        out[state] = value
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="character name (folder name)")
    ap.add_argument("--out", help="pets folder (default: pets next to widget.py; for the exe use "
                                  "%%APPDATA%%\\ClaudeQuota\\pets)")
    ap.add_argument("--video", help="video file for --clip")
    ap.add_argument("--clip", action="append", help="STATE=START:LENGTH in seconds, from --video")
    ap.add_argument("--crop", type=int, help="keep only this many pixels around the middle of the video width")
    ap.add_argument("--photo", action="append", help="STATE=IMAGE, a still photo for a state")
    ap.add_argument("--animal", help="one photo that moves like the built-in animals, for every state")
    ap.add_argument("--fps", type=int, default=24, help="frames per second for video clips (default 24)")
    ap.add_argument("--music", action="append", default=[], help="STATE whose clip should play its music")
    ap.add_argument("--studio", help="clean studio track to use for --music instead of the video's own sound")
    ap.add_argument("--video-audio", help="the video's sound as a separate file, if the video file has none")
    ap.add_argument("--volume", type=float, default=0.6, help="music volume (default 0.6)")
    ap.add_argument("--model", default="birefnet-general-lite", help="rembg model (default birefnet-general-lite)")
    args = ap.parse_args()

    root = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pets")
    out = os.path.join(root, args.name)
    os.makedirs(out, exist_ok=True)
    cut = Cutter(args.model)
    tmp = tempfile.mkdtemp(prefix="make_pet_")
    meta = {}

    def save(state, frames):
        for name in os.listdir(out):
            if name.startswith(state + "_") and name.endswith(".png"):
                os.remove(os.path.join(out, name))
        for i, im in enumerate(frames, 1):
            im.save(os.path.join(out, f"{state}_{i}.png"))
        print(f"{state}: {len(frames)} frame(s)")

    if args.animal:  # the built-in animals' way: one body, their motion, their icons, their pace
        her = keep_main_person(cut(Image.open(args.animal)))
        for state in pet.STATES:
            frames = []
            for f in range(pet.FRAMES):
                m = pet._motion(state, f)
                body = her
                if state == "error":
                    body = Image.blend(her.convert("RGB"), Image.new("RGB", her.size, (170, 176, 205)), 0.45)
                    body.putalpha(her.getchannel("A"))
                frames.append(with_icon(place(body, lift=m["lift"], dx=m["dx"], sq=m["sq"], height=44), state, f))
            save(state, frames)
        meta["fps"] = 1000 / pet.FRAME_MS
    else:
        clips = {s: tuple(map(float, v.split(":"))) for s, v in parse_pairs(args.clip, "clip").items()}
        if clips and not args.video:
            sys.exit("--clip needs --video")
        if args.music and not has_audio(args.video_audio or args.video):  # check before the slow part
            sys.exit(f"{args.video_audio or args.video} has no sound track. Give the video's sound with "
                     "--video-audio (it's needed to line up --studio, or to cut music from the video itself).")
        for state, (start, length) in clips.items():
            people = [keep_main_person(cut(Image.open(p)), args.crop and args.crop // 3)
                      for p in clip_frames(args.video, start, length, args.fps, args.crop, tmp)]
            scale = min(min(54 * K / p.width, 60 * K / p.height) for p in people)  # one scale: no jitter
            icon = state in ("waiting",)
            save(state, [with_icon(place(p, scale), state, i // 3) if icon else place(p, scale)
                         for i, p in enumerate(people)])
        for state, path in parse_pairs(args.photo, "photo").items():
            person = keep_main_person(cut(Image.open(path)))
            if state == "error":
                save(state, [place(person)])
                continue
            frames = []
            for f in range(args.fps * 2):
                ph = f / (args.fps * 2) * 2 * math.pi
                lift = {"waiting": max(0.0, math.sin(ph * 3)) * 3.5,
                        "held": 2.5 + math.sin(ph * 2)}.get(state, 0.0)
                dx = math.sin(ph * 2) * 2 if state == "held" else 0.0
                frames.append(with_icon(place(person, lift=lift, dx=dx), state, f // 3))
            save(state, frames)
        meta["fps"] = args.fps
        if args.music:
            sound = args.video_audio or args.video
            offset = studio_offset(sound, args.studio, tmp) if args.studio else 0.0
            if args.studio:
                print(f"studio track = video time {offset:+.2f}s")
            meta["music"] = {}
            for state in args.music:
                if state not in clips:
                    sys.exit(f"--music {state}: that state has no --clip")
                start, length = clips[state]
                name = f"{state}.wav"
                cut_audio(args.studio or sound, start + offset, length, os.path.join(out, name), args.volume)
                meta["music"][state] = name
    with open(os.path.join(out, "pet.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    print("saved to", os.path.abspath(out))


if __name__ == "__main__":
    main()
