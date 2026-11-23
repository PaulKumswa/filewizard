"""Video and audio (ffmpeg).

ffmpeg comes from the `imageio-ffmpeg` package (a bundled static build), so nothing needs
installing by hand. A system ffmpeg, or FILEWIZARD_FFMPEG, is used if present instead.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .util import UserError, human_size, report, sibling, unique_dir

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg",
              ".3gp", ".ts", ".mts", ".m2ts", ".ogv", ".vob", ".f4v", ".asf"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wma", ".aiff",
              ".aif", ".m4b", ".amr", ".ac3", ".mka", ".alac", ".ape", ".wv"}

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def ffmpeg() -> str:
    env = os.environ.get("FILEWIZARD_FFMPEG")
    if env and Path(env).exists():
        return env
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    raise UserError("Video/audio tools need ffmpeg. Run: pip install imageio-ffmpeg")


@dataclass
class Info:
    duration: float = 0.0
    vcodec: str | None = None
    acodec: str | None = None
    width: int = 0
    height: int = 0
    fps: float = 0.0
    rotation: int = 0


def probe(path: Path) -> Info:
    """Parse `ffmpeg -i` output (imageio-ffmpeg ships no ffprobe)."""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    err = r.stderr
    if "Invalid data found" in err or "No such file" in err:
        raise UserError(f"{path.name} isn't a video or audio file ffmpeg can read.")
    info = Info()
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        info.duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    for line in err.splitlines():
        if "Stream #" not in line:
            continue
        if ": Video:" in line and info.vcodec is None and "attached pic" not in line:
            info.vcodec = re.search(r"Video:\s*(\w+)", line)[1]
            size = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
            if size:
                info.width, info.height = int(size[1]), int(size[2])
            fps = re.search(r"([\d.]+)\s*fps", line)
            if fps:
                info.fps = float(fps[1])
        elif ": Audio:" in line and info.acodec is None:
            info.acodec = re.search(r"Audio:\s*(\w+)", line)[1]
    rot = re.search(r"rotation of (-?[\d.]+) degrees|rotate\s*:\s*(-?\d+)", err)
    if rot:
        info.rotation = int(float(rot[1] or rot[2])) % 360
    if info.vcodec is None and info.acodec is None:
        raise UserError(f"{path.name} has no video or audio ffmpeg can read.")
    return info


def run(args: list[str], duration: float = 0.0, label: str = "") -> None:
    """Run ffmpeg, feeding its progress into the progress bar."""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    err_lines: list[str] = []
    import threading

    def drain():  # keep stderr from filling its pipe and stalling ffmpeg
        for line in proc.stderr:
            err_lines.append(line)
            del err_lines[:-40]

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    try:
        for line in proc.stdout:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if duration > 0:
                    report(us / 1e6 / duration, label or None)
    except BaseException:  # cancelled: stop ffmpeg and remove its half-written output
        proc.kill()
        proc.wait()
        _discard_partial(args)
        raise
    proc.wait()
    t.join(2)
    if proc.returncode != 0:
        _discard_partial(args)
        tail = "".join(err_lines[-8:]).strip()
        raise UserError(f"ffmpeg failed: {tail[-600:]}")


def _discard_partial(args: list[str]) -> None:
    for a in args:
        if ".partial." in a:
            try:
                Path(a).unlink()
            except OSError:
                pass


def _out(src: Path, label: str | None, ext: str) -> Path:
    return sibling(src, label, ext)


def _finish(out: Path, tmp: Path) -> Path:
    os.replace(tmp, out)
    return out


def _tmp_for(out: Path) -> Path:
    # write to a temp name, rename at the end: a cancelled job never leaves a broken file
    return out.with_name(f".{out.stem}.partial{out.suffix}")


# ---------- encoder settings ----------

AUDIO_TARGETS = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],          # ~190 kbps VBR
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".aac": ["-c:a", "aac", "-b:a", "192k"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    ".opus": ["-c:a", "libopus", "-b:a", "128k"],
    ".aiff": ["-c:a", "pcm_s16be"],
}

_H264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p"]
VIDEO_TARGETS = {
    ".mp4": (_H264, ["-c:a", "aac", "-b:a", "160k"], ["-movflags", "+faststart"]),
    ".mov": (_H264, ["-c:a", "aac", "-b:a", "160k"], ["-movflags", "+faststart"]),
    ".mkv": (_H264, ["-c:a", "aac", "-b:a", "160k"], []),
    ".webm": (["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-deadline", "good",
               "-cpu-used", "4", "-pix_fmt", "yuv420p"], ["-c:a", "libopus", "-b:a", "128k"], []),
    ".avi": (["-c:v", "mpeg4", "-q:v", "4"], ["-c:a", "libmp3lame", "-q:a", "3"], []),
    ".wmv": (["-c:v", "wmv2", "-q:v", "4"], ["-c:a", "wmav2", "-b:a", "160k"], []),
}
# containers that can take the source streams untouched (instant, lossless "conversion")
_COPY_OK = {
    ".mp4": ({"h264", "hevc", "av1", "mpeg4"}, {"aac", "mp3", "ac3", "alac", "opus"}),
    ".mov": ({"h264", "hevc", "mpeg4", "prores"}, {"aac", "mp3", "alac", "pcm_s16le"}),
    ".mkv": ({"h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "mpeg2video"},
             {"aac", "mp3", "ac3", "opus", "vorbis", "flac", "dts", "eac3"}),
    ".webm": ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}),
}


def convert_audio(src: Path, ext: str) -> list[Path]:
    info = probe(src)
    if not info.acodec:
        raise UserError(f"{src.name} has no audio track.")
    out = _out(src, None, ext)
    tmp = _tmp_for(out)
    extra = ["-ar", "48000"] if ext == ".opus" else []
    run(["-i", str(src), "-vn", "-map_metadata", "0", *AUDIO_TARGETS[ext], *extra, str(tmp)],
        info.duration, f"Converting {src.name}")
    return [_finish(out, tmp)]


def convert_video(src: Path, ext: str) -> list[Path]:
    info = probe(src)
    if not info.vcodec:
        raise UserError(f"{src.name} has no video track.")
    out = _out(src, None, ext)
    tmp = _tmp_for(out)
    v, a, extra = VIDEO_TARGETS[ext]
    copy = _COPY_OK.get(ext)
    if copy and info.vcodec in copy[0] and (info.acodec is None or info.acodec in copy[1]):
        v, a = ["-c:v", "copy"], ["-c:a", "copy"]  # remux: instant and lossless
        if ext in (".mp4", ".mov") and info.vcodec == "hevc":
            v += ["-tag:v", "hvc1"]  # so QuickTime/iPhone accept it
    run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", *v, *a, *extra, str(tmp)],
        info.duration, f"Converting {src.name}")
    return [_finish(out, tmp)]


def to_gif(src: Path, max_width: int = 480, fps: int = 12) -> list[Path]:
    info = probe(src)
    out = _out(src, None, ".gif")
    tmp = _tmp_for(out)
    vf = (f"fps={fps},scale='min({max_width},iw)':-2:flags=lanczos,split[a][b];"
          "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    run(["-i", str(src), "-vf", vf, "-loop", "0", str(tmp)], info.duration, f"Making GIF of {src.name}")
    return [_finish(out, tmp)]


def gif_to_mp4(src: Path) -> list[Path]:
    out = _out(src, None, ".mp4")
    tmp = _tmp_for(out)
    run(["-i", str(src), "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-crf", "20", str(tmp)],
        probe(src).duration)
    return [_finish(out, tmp)]


# ---------- video tools ----------

def compress(src: Path, target_mb: float | None = None) -> list[Path]:
    """Default: visually-good CRF encode. With target_mb: two-pass to fit under a size limit."""
    info = probe(src)
    if not info.vcodec:
        return compress_audio(src)
    before = src.stat().st_size
    label = f"under {target_mb:g} MB" if target_mb else "compressed"
    out = _out(src, label, ".mp4")
    tmp = _tmp_for(out)
    if not target_mb:
        vf = "scale='if(gt(ih,iw),min(1080,iw),-2)':'if(gt(ih,iw),-2,min(1080,ih))'"
        run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-c:v", "libx264",
             "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", str(tmp)], info.duration, f"Compressing {src.name}")
        _finish(out, tmp)
        if out.stat().st_size >= before * 0.95:
            out.unlink()
            raise UserError(f"{src.name} is already efficiently compressed; the result wasn't smaller.")
        return [out]

    if info.duration <= 0:
        raise UserError("Couldn't read the video's length, so can't target a size.")
    budget_bits = target_mb * 1024 * 1024 * 8 * 0.96  # 4% for container overhead
    audio_kbps = 96 if info.acodec else 0
    video_kbps = budget_bits / info.duration / 1000 - audio_kbps
    if video_kbps < 60:
        raise UserError(f"{src.name} is too long ({info.duration / 60:.0f} min) to fit in {target_mb:g} MB "
                        "at watchable quality. Try trimming it first.")
    height = 1080 if video_kbps > 2500 else 720 if video_kbps > 900 else 480 if video_kbps > 350 else 360
    short = min(info.width, info.height) or height
    height = min(height, short)
    portrait = info.height > info.width
    vf = f"scale={height}:-2" if portrait else f"scale=-2:{height}"
    common = ["-i", str(src), "-map", "0:v:0", "-vf", vf, "-c:v", "libx264", "-preset", "medium",
              "-b:v", f"{video_kbps:.0f}k", "-maxrate", f"{video_kbps * 1.5:.0f}k",
              "-bufsize", f"{video_kbps * 2:.0f}k", "-pix_fmt", "yuv420p"]
    with tempfile.TemporaryDirectory(prefix="fw-2pass-") as td:
        passlog = str(Path(td) / "pass")
        from . import util

        base, span = util.PROGRESS.base, util.PROGRESS.span
        util.PROGRESS.span = span / 2
        run([*common, "-pass", "1", "-passlogfile", passlog, "-an", "-f", "mp4", os.devnull],
            info.duration, f"Analyzing {src.name} (pass 1 of 2)")
        util.PROGRESS.base = base + span / 2
        audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{audio_kbps}k"] if info.acodec else []
        run([*common, *audio, "-pass", "2", "-passlogfile", passlog, "-movflags", "+faststart", str(tmp)],
            info.duration, f"Compressing {src.name} (pass 2 of 2)")
        util.PROGRESS.base, util.PROGRESS.span = base, span
    _finish(out, tmp)
    size = out.stat().st_size
    if size > target_mb * 1024 * 1024:
        raise UserError(f"Got {human_size(size)}, a bit over {target_mb:g} MB. The file was kept: {out.name}")
    return [out]


def compress_audio(src: Path) -> list[Path]:
    info = probe(src)
    out = _out(src, "compressed", ".mp3")
    tmp = _tmp_for(out)
    run(["-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "128k", str(tmp)], info.duration)
    _finish(out, tmp)
    if out.stat().st_size >= src.stat().st_size * 0.95:
        out.unlink()
        raise UserError(f"{src.name} is already small; the result wasn't smaller.")
    return [out]


def resize(src: Path, height: int) -> list[Path]:
    info = probe(src)
    short = min(info.width, info.height)
    if short and short <= height:
        raise UserError(f"{src.name} is already {info.width}x{info.height}, no bigger than {height}p.")
    portrait = info.height > info.width
    vf = f"scale={height}:-2" if portrait else f"scale=-2:{height}"
    out = _out(src, f"{height}p", ".mp4")
    tmp = _tmp_for(out)
    run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, *_H264, "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(tmp)], info.duration, f"Resizing {src.name}")
    return [_finish(out, tmp)]


def parse_time(s: str) -> float:
    """'90', '1:30', '01:02:03.5' -> seconds."""
    s = s.strip()
    if not s:
        raise ValueError("empty")
    parts = [float(p) for p in s.split(":")]
    if len(parts) > 3:
        raise ValueError(s)
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def parse_span(spec: str, duration: float) -> tuple[float, float]:
    """'0:10-1:25', '1:00-' (to end), '-0:30' (from start)."""
    spec = spec.replace(" ", "").replace("to", "-")
    if "-" not in spec:
        raise UserError("Give a start and end like 0:10-1:25 (or 1:00- for 'to the end').")
    a, b = spec.split("-", 1)
    try:
        start = parse_time(a) if a else 0.0
        end = parse_time(b) if b else duration
    except ValueError as e:
        raise UserError(f"Couldn't read the time '{spec}'. Use minutes:seconds, e.g. 0:10-1:25.") from e
    if duration and end > duration + 0.5:
        end = duration
    if end <= start:
        raise UserError("The end time must be after the start time.")
    return start, end


def trim(src: Path, span: str) -> list[Path]:
    info = probe(src)
    start, end = parse_span(span, info.duration)
    out = _out(src, "trimmed", src.suffix.lower() if info.vcodec is None else ".mp4")
    tmp = _tmp_for(out)
    if info.vcodec:  # re-encode so the cut is frame-accurate (stream copy snaps to keyframes)
        codec = [*_H264, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
        maps = ["-map", "0:v:0", "-map", "0:a:0?"]
    else:
        ext = src.suffix.lower()
        codec = AUDIO_TARGETS.get(ext, AUDIO_TARGETS[".mp3"])
        if ext not in AUDIO_TARGETS:
            out = _out(src, "trimmed", ".mp3")
            tmp = _tmp_for(out)
        maps = ["-vn"]
    run(["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}", *maps, *codec, str(tmp)],
        end - start, f"Trimming {src.name}")
    return [_finish(out, tmp)]


def mute(src: Path) -> list[Path]:
    info = probe(src)
    if not info.acodec:
        raise UserError(f"{src.name} already has no sound.")
    ext = src.suffix.lower()
    out = _out(src, "muted", ext if ext in (".mp4", ".mov", ".mkv", ".webm", ".m4v") else ".mp4")
    tmp = _tmp_for(out)
    run(["-i", str(src), "-map", "0:v:0", "-c:v", "copy", "-an", str(tmp)], info.duration)
    return [_finish(out, tmp)]


def rotate(src: Path, how: str) -> list[Path]:
    vf = {"right": "transpose=1", "left": "transpose=2", "180": "transpose=1,transpose=1",
          "hflip": "hflip", "vflip": "vflip"}[how]
    label = {"right": "rotated right", "left": "rotated left", "180": "rotated 180",
             "hflip": "mirrored", "vflip": "flipped"}[how]
    info = probe(src)
    out = _out(src, label, ".mp4")
    tmp = _tmp_for(out)
    run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, *_H264, "-c:a", "copy",
         "-movflags", "+faststart", str(tmp)], info.duration, f"Rotating {src.name}")
    return [_finish(out, tmp)]


def speed(src: Path, factor: float) -> list[Path]:
    info = probe(src)
    atempo = []
    f = factor
    while f > 2.0:  # atempo accepts 0.5..2 per stage
        atempo.append("atempo=2.0")
        f /= 2
    while f < 0.5:
        atempo.append("atempo=0.5")
        f *= 2
    atempo.append(f"atempo={f:.4f}")
    label = f"{factor:g}x speed"
    if info.vcodec:
        out = _out(src, label, ".mp4")
        tmp = _tmp_for(out)
        args = ["-i", str(src), "-map", "0:v:0", "-filter:v", f"setpts=PTS/{factor}", *_H264]
        if info.acodec:
            args += ["-map", "0:a:0", "-filter:a", ",".join(atempo), "-c:a", "aac", "-b:a", "160k"]
        run([*args, "-movflags", "+faststart", str(tmp)], info.duration / factor)
    else:
        ext = src.suffix.lower() if src.suffix.lower() in AUDIO_TARGETS else ".mp3"
        out = _out(src, label, ext)
        tmp = _tmp_for(out)
        run(["-i", str(src), "-vn", "-filter:a", ",".join(atempo), *AUDIO_TARGETS[ext], str(tmp)],
            info.duration / factor)
    return [_finish(out, tmp)]


def snapshot(src: Path) -> list[Path]:
    """A still frame from 10% in (skips black intro frames)."""
    info = probe(src)
    at = info.duration * 0.1 if info.duration > 2 else 0
    out = _out(src, "frame", ".jpg")
    run(["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)])
    if not out.exists():
        raise UserError(f"Couldn't grab a frame from {src.name}.")
    return [out]


def extract_frames(src: Path, every_seconds: float = 1.0) -> list[Path]:
    info = probe(src)
    if info.duration / every_seconds > 3000:
        every_seconds = info.duration / 3000
    folder = unique_dir(src.with_name(f"{src.stem} (frames)"))
    run(["-i", str(src), "-vf", f"fps=1/{every_seconds:g}", "-q:v", "3", str(folder / "frame %04d.jpg")],
        info.duration, f"Extracting frames from {src.name}")
    return sorted(folder.iterdir())


def normalize(src: Path) -> list[Path]:
    """Even out loudness to the -16 LUFS podcast/streaming standard."""
    info = probe(src)
    if not info.acodec:
        raise UserError(f"{src.name} has no audio track.")
    if info.vcodec:
        out = _out(src, "normalized", ".mp4")
        tmp = _tmp_for(out)
        run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af",
             "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", str(tmp)], info.duration)
    else:
        ext = src.suffix.lower() if src.suffix.lower() in AUDIO_TARGETS else ".mp3"
        out = _out(src, "normalized", ext)
        tmp = _tmp_for(out)
        run(["-i", str(src), "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000",
             *AUDIO_TARGETS[ext], str(tmp)], info.duration, f"Normalizing {src.name}")
    return [_finish(out, tmp)]


def merge(files: list[Path]) -> list[Path]:
    """Join videos (or audio files) end to end, even if sizes/codecs differ."""
    if len(files) < 2:
        raise UserError("Select at least two files to join.")
    infos = [probe(f) for f in files]
    is_video = all(i.vcodec for i in infos)
    if not is_video and any(i.vcodec for i in infos):
        raise UserError("Can't join video and audio-only files together.")
    total = sum(i.duration for i in infos)
    args: list[str] = []
    for f in files:
        args += ["-i", str(f)]
    if is_video:
        w = infos[0].width or 1280
        h = infos[0].height or 720
        w, h = w - w % 2, h - h % 2
        parts, labels = [], []
        for k, info in enumerate(infos):
            parts.append(f"[{k}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{k}]")
            if info.acodec:
                parts.append(f"[{k}:a:0]aformat=sample_rates=48000:channel_layouts=stereo[a{k}]")
            else:  # silent filler so every segment has audio
                parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{max(info.duration, 0.1):.3f}[a{k}]")
            labels.append(f"[v{k}][a{k}]")
        graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(files)}:v=1:a=1[v][a]"
        out = _out(files[0], "joined", ".mp4")
        tmp = _tmp_for(out)
        run([*args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", *_H264, "-c:a", "aac",
             "-b:a", "160k", "-movflags", "+faststart", str(tmp)], total, "Joining videos")
    else:
        parts = [f"[{k}:a:0]aformat=sample_rates=48000:channel_layouts=stereo[a{k}]" for k in range(len(files))]
        graph = ";".join(parts) + ";" + "".join(f"[a{k}]" for k in range(len(files))) + \
            f"concat=n={len(files)}:v=0:a=1[a]"
        ext = files[0].suffix.lower() if files[0].suffix.lower() in AUDIO_TARGETS else ".mp3"
        out = _out(files[0], "joined", ext)
        tmp = _tmp_for(out)
        run([*args, "-filter_complex", graph, "-map", "[a]", *AUDIO_TARGETS[ext], str(tmp)], total, "Joining audio")
    return [_finish(out, tmp)]


def burn_subtitles(src: Path) -> list[Path]:
    """Hard-code subtitles into the picture, from a .srt/.ass/.vtt with the same name next to it."""
    subs = next((src.with_suffix(e) for e in (".srt", ".ass", ".vtt")
                 if src.with_suffix(e).exists()), None)
    if subs is None:
        raise UserError(f"Put a subtitle file named '{src.stem}.srt' next to the video first.")
    info = probe(src)
    out = _out(src, "subtitled", ".mp4")
    tmp = _tmp_for(out)
    # ffmpeg's subtitles filter needs ':' and '\\' escaped in the path (Windows drive letters!)
    esc = str(subs).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"subtitles='{esc}'", *_H264,
         "-c:a", "copy", "-movflags", "+faststart", str(tmp)], info.duration, f"Adding subtitles to {src.name}")
    return [_finish(out, tmp)]


def extract_subtitles(src: Path) -> list[Path]:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if ": Subtitle:" not in r.stderr:
        raise UserError(f"{src.name} has no subtitle tracks.")
    out = _out(src, None, ".srt")
    run(["-i", str(src), "-map", "0:s:0", str(out)])
    return [out]


def ringtone(src: Path, seconds: float = 30.0) -> list[Path]:
    """iPhone ringtone (.m4r): first 30 s, AAC, with a short fade-out."""
    info = probe(src)
    if not info.acodec:
        raise UserError(f"{src.name} has no audio track.")
    length = min(seconds, info.duration or seconds)
    out = _out(src, "ringtone", ".m4r")
    tmp = _tmp_for(out).with_suffix(".m4a")  # ffmpeg picks the ipod muxer from .m4a
    fade = f"afade=t=out:st={max(0.0, length - 2):.2f}:d=2"
    run(["-i", str(src), "-t", f"{length:.2f}", "-vn", "-af", fade, "-c:a", "aac", "-b:a", "192k",
         "-f", "ipod", str(tmp)], length, f"Making ringtone from {src.name}")
    return [_finish(out, tmp)]


def compatible(src: Path) -> list[Path]:
    """H.264/AAC MP4 at ≤1080p, 8-bit 4:2:0: plays on every device, browser and app
    (fixes iPhone HEVC/HDR videos that won't play on older PCs or upload sites)."""
    info = probe(src)
    if not info.vcodec:
        raise UserError(f"{src.name} has no video track.")
    out = _out(src, "compatible", ".mp4")
    tmp = _tmp_for(out)
    vf = "scale='if(gt(ih,iw),min(1080,iw),-2)':'if(gt(ih,iw),-2,min(1080,ih))',format=yuv420p"
    run(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-c:v", "libx264", "-profile:v", "high",
         "-level", "4.1", "-preset", "medium", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-ac", "2",
         "-movflags", "+faststart", str(tmp)], info.duration, f"Making {src.name} play everywhere")
    return [_finish(out, tmp)]


def replace_audio(files: list[Path]) -> list[Path]:
    """One video + one audio file -> the video with that audio as its soundtrack."""
    vids = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
    auds = [f for f in files if f.suffix.lower() in AUDIO_EXTS]
    if len(vids) != 1 or len(auds) != 1:
        raise UserError("Select exactly one video and one audio file.")
    v, a = vids[0], auds[0]
    vinfo = probe(v)
    out = _out(v, "new audio", ".mp4" if v.suffix.lower() not in (".mov", ".mkv") else v.suffix.lower())
    tmp = _tmp_for(out)
    vcodec = ["-c:v", "copy"] if vinfo.vcodec in ("h264", "hevc", "av1", "mpeg4") or out.suffix == ".mkv" else _H264
    fade = max(0.0, vinfo.duration - 1.5)
    run(["-i", str(v), "-i", str(a), "-map", "0:v:0", "-map", "1:a:0", *vcodec, "-c:a", "aac", "-b:a", "192k",
         "-af", f"afade=t=out:st={fade:.2f}:d=1.5", "-t", f"{vinfo.duration:.3f}", "-shortest",
         "-movflags", "+faststart", str(tmp)], vinfo.duration, f"Adding {a.name} to {v.name}")
    return [_finish(out, tmp)]
