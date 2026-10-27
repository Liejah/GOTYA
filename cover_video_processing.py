"""Secure preprocessing for Gotya profile cover videos.

This module performs no uploads and makes no database changes.
The caller must provide explicit FFmpeg and FFprobe executable paths.
"""
import json
import os
import stat
import subprocess
from pathlib import Path

MAX_BYTES = 15 * 1024 * 1024
MAX_SECONDS = 10.0
MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_FPS = 60.0
OUTPUT_FPS = 30
FFPROBE_TIMEOUT = 15
NORMALIZE_TIMEOUT = 45
DECODE_TIMEOUT = 30


class CoverVideoError(ValueError):
    """Raised when a cover video is invalid or cannot be normalized."""


def _run(args, timeout):
    try:
        return subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise CoverVideoError("Video processing timed out.") from exc
    except OSError as exc:
        raise CoverVideoError("Video processing could not start.") from exc


def _regular_file(path, description):
    path = Path(path)
    if path.is_symlink():
        raise CoverVideoError(f"{description} must not be a symbolic link.")
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise CoverVideoError(f"{description} is unavailable.") from exc
    if not stat.S_ISREG(mode):
        raise CoverVideoError(f"{description} must be a regular file.")
    return path


def _executable(path, description):
    path = Path(path)
    if not path.is_absolute():
        raise CoverVideoError(f"{description} path must be absolute.")
    path = _regular_file(path, description)
    if not os.access(path, os.X_OK):
        raise CoverVideoError(f"{description} is not executable.")
    return str(path)


def _probe(path, ffprobe):
    result = _run(
        [
            ffprobe,
            "-v", "error",
            "-probesize", "5000000",
            "-analyzeduration", "5000000",
            "-show_entries",
            "format=format_name,duration:"
            "stream=codec_type,codec_name,width,height,"
            "avg_frame_rate,r_frame_rate",
            "-of", "json",
            str(path),
        ],
        FFPROBE_TIMEOUT,
    )
    if result.returncode != 0:
        raise CoverVideoError("FFprobe rejected the video.")

    try:
        info = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CoverVideoError("Video metadata is invalid.") from exc

    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    format_names = set((fmt.get("format_name") or "").split(","))

    if not ({"mp4", "mov"} & format_names):
        raise CoverVideoError("Only MP4 and MOV video containers are supported.")

    videos = [s for s in streams if s.get("codec_type") == "video"]
    if len(videos) != 1:
        raise CoverVideoError("The file must contain exactly one video stream.")

    video = videos[0]
    if video.get("codec_name") not in ("h264", "hevc"):
        raise CoverVideoError("The input video codec is not supported.")

    try:
        width = int(video.get("width", 0))
        height = int(video.get("height", 0))
    except (TypeError, ValueError) as exc:
        raise CoverVideoError("Video dimensions are invalid.") from exc

    if width < 1 or height < 1:
        raise CoverVideoError("Video dimensions are invalid.")
    if width > MAX_WIDTH or height > MAX_HEIGHT:
        raise CoverVideoError("Video resolution exceeds 1920 × 1080.")

    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
    try:
        numerator, denominator = rate.split("/", 1)
        denominator = float(denominator)
        fps = float(numerator) / denominator
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise CoverVideoError("Video frame rate is invalid.") from exc

    if not (0 < fps <= MAX_FPS):
        raise CoverVideoError("Video frame rate must not exceed 60 fps.")

    try:
        duration = float(fmt.get("duration", 0))
    except (TypeError, ValueError) as exc:
        raise CoverVideoError("Video duration is invalid.") from exc

    if not (0 < duration <= MAX_SECONDS):
        raise CoverVideoError("Video duration must be over 0 and at most 10 seconds.")

    return info, video, duration


def validate_cover_video(source_path, ffprobe_path):
    """Validate an uploaded temporary file without modifying it."""
    source = _regular_file(source_path, "Input video")
    ffprobe = _executable(ffprobe_path, "FFprobe")

    try:
        size = source.stat().st_size
    except OSError as exc:
        raise CoverVideoError("Input video is unavailable.") from exc

    if size <= 0:
        raise CoverVideoError("The video file is empty.")
    if size > MAX_BYTES:
        raise CoverVideoError("The video file exceeds 15 MiB.")

    return _probe(source, ffprobe)


def normalize_cover_video(source_path, output_path, ffmpeg_path, ffprobe_path):
    """Validate, normalize, and fully decode-check an MP4 output.

    The output path must be a new, private temporary path. This function
    refuses to overwrite it and removes a partial output if processing fails.
    """
    source = _regular_file(source_path, "Input video")
    output = Path(output_path)

    if not output.is_absolute():
        raise CoverVideoError("Output path must be absolute.")
    if output.exists() or output.is_symlink():
        raise CoverVideoError("Output path already exists; refusing to overwrite it.")
    if output.suffix.lower() != ".mp4":
        raise CoverVideoError("Normalized output must use the .mp4 extension.")

    ffmpeg = _executable(ffmpeg_path, "FFmpeg")
    ffprobe = _executable(ffprobe_path, "FFprobe")

    validate_cover_video(source, ffprobe)
    output_created = False

    try:
        # Mark the destination for cleanup before launching FFmpeg.
        # If FFmpeg times out after creating a partial file, cleanup
        # must still remove that file.
        output_created = True
        result = _run(
            [
                ffmpeg,
                "-hide_banner", "-nostdin", "-v", "error", "-n",
                "-i", str(source),
                "-map", "0:v:0",
                "-an", "-sn", "-dn",
                "-map_metadata", "-1",
                "-map_chapters", "-1",
                "-vf",
                "scale=w='min(1920,iw)':h='min(1080,ih)':"
                "force_original_aspect_ratio=decrease:"
                "force_divisible_by=2,fps=30,format=yuv420p",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-threads", "2",
                "-t", "10",
                "-movflags", "+faststart",
                "-f", "mp4",
                str(output),
            ],
            NORMALIZE_TIMEOUT,
        )

        output_created = output.exists()
        if result.returncode != 0:
            raise CoverVideoError("FFmpeg could not normalize this video.")
        if not output_created or not output.is_file():
            raise CoverVideoError("Normalized video output is missing.")
        if output.stat().st_size <= 0:
            raise CoverVideoError("Normalized video output is empty.")
        if output.stat().st_size > MAX_BYTES:
            raise CoverVideoError("Normalized video exceeds 15 MiB.")

        info, video, duration = _probe(output, ffprobe)
        streams = info.get("streams") or {}
        format_names = set(
            ((info.get("format") or {}).get("format_name") or "").split(",")
        )

        if "mp4" not in format_names:
            raise CoverVideoError("Normalized output is not an MP4 container.")
        if video.get("codec_name") != "h264":
            raise CoverVideoError("Normalized output is not H.264.")
        if any(s.get("codec_type") == "audio" for s in streams):
            raise CoverVideoError("Normalized output unexpectedly contains audio.")
        if duration > MAX_SECONDS:
            raise CoverVideoError("Normalized output exceeds 10 seconds.")
        if int(video.get("width", 0)) > MAX_WIDTH:
            raise CoverVideoError("Normalized output width exceeds the limit.")
        if int(video.get("height", 0)) > MAX_HEIGHT:
            raise CoverVideoError("Normalized output height exceeds the limit.")

        decoded = _run(
            [
                ffmpeg,
                "-hide_banner", "-nostdin", "-v", "error",
                "-i", str(output),
                "-map", "0:v:0",
                "-f", "null", "-",
            ],
            DECODE_TIMEOUT,
        )
        if decoded.returncode != 0:
            raise CoverVideoError("Normalized video failed its full decode check.")

        return {
            "path": output,
            "size": output.stat().st_size,
            "duration": duration,
            "width": int(video["width"]),
            "height": int(video["height"]),
            "codec": "h264",
            "audio_removed": True,
        }

    except Exception:
        if output_created and output.is_file() and not output.is_symlink():
            try:
                output.unlink()
            except OSError:
                pass
        raise