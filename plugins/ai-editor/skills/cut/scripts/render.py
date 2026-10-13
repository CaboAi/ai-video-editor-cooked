#!/usr/bin/env python3
"""Render the cut with plain ffmpeg: every kept span trimmed and joined.

    python3 render.py <source> <edit_dir> [--crf 18] [--no-hw] [--flip | --no-flip]

Reads <edit_dir>/decisions.json and report.json, writes <edit_dir>/cut.mp4 at the
source resolution (rotation applied), H.264 + AAC. Flipped left to right when mirror.py found the take
mirrored (<edit_dir>/mirror.json), so text in the shot reads the right way.

How it stays exact on a big 4K source:
  - Each kept span is encoded on its own (video only), in parallel. One
    filter_complex over the whole 4K stream with 100 trims crawls.
  - Frames are counted per segment and again after joining. Frames in == frames out.
  - Audio is cut in ONE pass and muxed on at the end. Separate AAC tracks per
    segment add encoder padding at every join and drift by seconds.
  - Every audio span gets an 8 ms fade in and out, so a join never clicks.
  - Keyframe every 15 frames, so the next step (Remotion) can seek quickly.
Never stream-copy (-c copy) across cuts: that snaps to keyframes, up to 2 s off.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FADE_S = 0.008
LEAD_S = 0.5     # seek this far early, let the time-based trim pick the frames


def first_int(text):
    """The first whole number in ffprobe's output, else 0. FFmpeg 8+ prints "16," when the stream
    carries side data (an iPhone's ICC profile, copied into every segment), and int() fails on that."""
    m = re.search(r"\d+", text)
    return int(m.group()) if m else 0


def count_frames(path):
    # -threads 0: -count_frames decodes the whole file, on one thread unless told otherwise
    r = subprocess.run(["ffprobe", "-v", "error", "-threads", "0", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return first_int(r.stdout)


def starts(path):
    """(video stream start, file start) in seconds. With -copyts the video trim sees the stream's
    own timestamps, which can begin a frame or two in (B-frame delay, an edit list); the audio
    pass is rebased to the file start."""
    def one(entry, *sel):
        r = subprocess.run(["ffprobe", "-v", "error", *sel, "-show_entries", entry, "-of",
                            "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
        try:
            return float(r.stdout.split()[0])
        except (ValueError, IndexError):
            return 0.0
    return one("stream=start_time", "-select_streams", "v:0"), one("format=start_time")


def video_window(s, e, fps, vstart):
    """Trim times that select frames s..e-1 of the video stream exactly. Frame k sits at
    vstart + k/fps only up to the container's timestamp rounding (a 1/600 s timescale moves a
    29.97 fps frame by up to 0.8 ms either way), so a trim on the exact frame time keeps or drops
    that frame by the rounding: two edges rounded differently give N-1 or N+1. Half a frame
    early on both edges is immune to any rounding under half a frame."""
    return vstart + (s - 0.5) / fps, vstart + (e - 0.5) / fps


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
    from ai_editor import progress   # the home page's video job, when there is one
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("edit_dir")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--no-hw", action="store_true", help="skip hardware decode on a Mac")
    ap.add_argument("--flip", action=argparse.BooleanOptionalAction, default=None,
                    help="flip left to right (default: what mirror.json says)")
    a = ap.parse_args()
    progress.step("Cutting out the mistakes", job="video", key=a.edit_dir)

    d = Path(a.edit_dir)
    rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
    fps, frames = rep["fps"], rep["frames"]
    src, out = a.source, d / "cut.mp4"
    if a.flip is None:
        try:
            a.flip = bool(json.loads((d / "mirror.json").read_text(encoding="utf-8")).get("flip"))
        except (OSError, ValueError):
            a.flip = False
    if a.flip:
        print("flipping left to right: the take is mirrored (mirror.json; --no-flip keeps it as shot)", flush=True)
    hw = sys.platform == "darwin" and not a.no_hw
    vstart, fstart = starts(src)
    off = vstart - fstart     # where frame 0 plays on the audio's timeline
    x264 = ["-c:v", "libx264", "-crf", str(a.crf), "-preset", "veryfast"]
    # A Mac already decoding in hardware encodes there too: a 107 s iPhone cut went 60 s -> 20 s and
    # 192 MB -> 119 MB at 40.6 dB PSNR, no visible change. -q:v needs Apple silicon; an Intel Mac's
    # encoder refuses it and every span is redone with x264.
    enc = ["-c:v", "h264_videotoolbox", "-q:v", "65"] if hw else x264
    tmp = Path(tempfile.mkdtemp(prefix="cut_render_"))
    try:
        print(f"{len(frames)} spans, {sum(e - s for s, e in frames)} frames -> {out}", flush=True)

        def one(i):
            s, e = frames[i]
            t0, t1 = video_window(s, e, fps, vstart)
            seg = tmp / f"{i:05d}.mp4"
            # -copyts is load-bearing: without it the input seek rebases timestamps
            # to zero and the absolute trim picks the wrong footage at the right length.
            # Frame-numbered timestamps at an explicit -r: after setpts the frames carry no duration,
            # so without -r the muxer gives the last one ~0 s and FFmpeg 7's edit list ends on it,
            # dropping it on read. N/fps keeps the constant-rate clock from duplicating or dropping
            # frames over a 1/600 s source's jittered timestamps.
            cmd = ["ffmpeg", "-v", "error", "-y", "-copyts", *(["-hwaccel", "videotoolbox"] if hw else []),
                   "-ss", f"{max(0.0, t0 - fstart - LEAD_S):.6f}", "-i", src, "-an",
                   "-vf", f"trim=start={max(0.0, t0):.6f}:end={t1:.6f},setpts=N/({fps})/TB" + (",hflip" if a.flip else ""),
                   "-r", str(fps), *enc, "-pix_fmt", "yuv420p", "-g", "15", str(seg)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode:
                return f"span {i}: {r.stderr[-300:]}"
            got = count_frames(seg)
            return None if got == e - s else f"span {i}: {got} frames, wanted {e - s}"

        def encode_all():
            """The first span's error, else None."""
            # the hardware encoder holds a few sessions at once; more get refused and send everything to x264
            workers = 4 if enc is not x264 else max(2, (os.cpu_count() or 8) // 4)
            with cf.ThreadPoolExecutor(workers) as ex:
                for n, err in enumerate(ex.map(one, range(len(frames))), 1):
                    if err:
                        return err
                    if n % 20 == 0:
                        print(f"  video {n}/{len(frames)}", flush=True)

        def attempt():
            """The whole render with the current encoder: the first error, else None."""
            err = encode_all()
            if err:
                return err
            lst = tmp / "list.txt"
            # concat quoting: a ' in the temp path (a user name like O'Brien) is closed, escaped, reopened
            lst.write_text("".join("file '" + str(tmp / f"{i:05d}.mp4").replace("'", "'\\''") + "'\n"
                                   for i in range(len(frames))), encoding="utf-8")
            allv = tmp / "all.mp4"
            if subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                               "-c", "copy", str(allv)]).returncode:
                return "concat failed"
            want = sum(e - s for s, e in frames)
            if count_frames(allv) != want:
                return "concat lost frames"

            print("  audio, one pass", flush=True)
            parts = []
            for i, (s, e) in enumerate(frames):
                t0, t1 = off + s / fps, off + e / fps
                fo = max(0.0, t1 - t0 - FADE_S)
                parts.append(f"[0:a:0]atrim=start={t0:.6f}:end={t1:.6f},asetpts=PTS-STARTPTS,"
                             f"afade=t=in:d={FADE_S},afade=t=out:st={fo:.6f}:d={FADE_S}[a{i}]")
            fc = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(frames))) + \
                f"concat=n={len(frames)}:v=0:a=1[ac]"
            aud = tmp / "a.m4a"
            if subprocess.run(["ffmpeg", "-v", "error", "-y", "-vn", "-i", src,
                               "-filter_complex", fc, "-map", "[ac]",
                               "-c:a", "aac", "-b:a", "192k", str(aud)]).returncode:
                return "audio pass failed"

            if subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(allv), "-i", str(aud),
                               "-map", "0:v", "-map", "1:a", "-c", "copy", "-movflags", "+faststart",
                               str(out)]).returncode:
                return "mux failed"
            got = count_frames(out)
            print(f"  check: {got} frames of {want}, {want / fps:.2f}s")
            if got != want:
                return "frame mismatch"

        err = attempt()
        # A hardware render that fails anywhere (a refused session, a span short, a lost frame at the join)
        # is redone whole with x264: re-encoding only the failed spans would mix two encoders in one concat -c copy.
        if err and enc is not x264:
            print(f"  hardware encode failed, again with x264 ({err[:200]})", flush=True)
            enc = x264
            err = attempt()
        if err:
            sys.exit(f"ERROR {err}")
        print(out)
    finally:   # a failed render left 24-66 MB of segments behind on every try
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
