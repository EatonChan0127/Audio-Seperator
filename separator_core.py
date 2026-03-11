from __future__ import annotations

import re
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import soundfile as sf

BASE_STEMS = ("vocals", "drums", "bass", "other")
AVAILABLE_TARGETS = BASE_STEMS + ("accompaniment",)
MODEL_NAME = "htdemucs"

ProgressCallback = Callable[[float, str], None]


class SeparationError(RuntimeError):
    """Raised when Demucs separation fails."""


@dataclass
class SeparationResult:
    export_dir: Path
    files: list[Path]


def separate_audio(
    input_audio: Path,
    selected_targets: Iterable[str],
    workspace_dir: Path,
    callback: ProgressCallback,
) -> SeparationResult:
    input_audio = input_audio.expanduser().resolve()
    if not input_audio.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_audio}")

    targets = _normalize_targets(selected_targets)
    if not targets:
        raise ValueError("Please select at least one target to export.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    import tempfile
    temp_base = Path(tempfile.mkdtemp(prefix="AudioSeparator_"))
    
    run_root = temp_base / "runs"
    run_root.mkdir(parents=True, exist_ok=True)

    callback(5, "Checking Demucs runtime...")
    _run_demucs(input_audio=input_audio, output_root=run_root, callback=callback)

    callback(92, "Collecting separated stems...")
    stem_dir = _find_stem_directory(run_root)

    export_dir = temp_base / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    output_files: list[Path] = []
    for target in targets:
        if target == "accompaniment":
            accompaniment_file = export_dir / f"{input_audio.stem}_accompaniment.wav"
            _build_accompaniment(
                stem_dir=stem_dir,
                output_file=accompaniment_file,
            )
            output_files.append(accompaniment_file)
            callback(95, f"Built accompaniment: {accompaniment_file.name}")
            continue

        stem_file = stem_dir / f"{target}.wav"
        if not stem_file.exists():
            raise SeparationError(f"Stem missing from Demucs output: {stem_file}")

        destination = export_dir / f"{input_audio.stem}_{target}.wav"
        shutil.copy2(stem_file, destination)
        output_files.append(destination)
        callback(95, f"Exported {target}: {destination.name}")

    callback(100, "Separation finished.")
    return SeparationResult(export_dir=export_dir, files=output_files)


def _run_demucs(input_audio: Path, output_root: Path, callback: ProgressCallback) -> None:
    if getattr(sys, "frozen", False):
        command = [
            sys.executable,
            "-m",
            "demucs.separate",
            "-n",
            MODEL_NAME,
            "-o",
            str(output_root),
            str(input_audio),
        ]
    else:
        command = [
            sys.executable,
            str(Path(__file__).parent / "app.py"),
            "-m",
            "demucs.separate",
            "-n",
            MODEL_NAME,
            "-o",
            str(output_root),
            str(input_audio),
        ]

    callback(10, "Launching Demucs process...")
    progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
    tail_logs: deque[str] = deque(maxlen=25)
    fake_progress = 10.0

    kwargs = {}
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **kwargs
    )

    assert process.stdout is not None
    output_queue: queue.Queue[str] = queue.Queue()

    def _stdout_reader() -> None:
        assert process.stdout is not None
        for raw_line in iter(process.stdout.readline, ""):
            output_queue.put(raw_line)

    reader_thread = threading.Thread(target=_stdout_reader, daemon=True)
    reader_thread.start()

    while True:
        consumed_output = False
        while True:
            try:
                line = output_queue.get_nowait()
            except queue.Empty:
                break

            consumed_output = True
            clean_line = line.replace("\r", "").strip()
            if clean_line:
                tail_logs.append(clean_line)
                callback(fake_progress, clean_line)

            matches = progress_regex.findall(line)
            if matches:
                parsed = float(matches[-1])
                mapped = 10.0 + max(0.0, min(100.0, parsed)) * 0.8
                if mapped > fake_progress:
                    fake_progress = mapped
                    callback(fake_progress, f"Separating... {parsed:.1f}%")

        if process.poll() is not None and output_queue.empty():
            break

        if not consumed_output:
            # Keep the progress bar moving even when tqdm output is buffered.
            fake_progress = min(fake_progress + 0.2, 90.0)
            callback(fake_progress, "Separating audio...")
            time.sleep(0.25)

    if process.stdout:
        process.stdout.close()
    reader_thread.join(timeout=1)

    return_code = process.wait()
    if return_code != 0:
        summary = "\n".join(tail_logs) or "Demucs exited with a non-zero code."
        raise SeparationError(f"Demucs failed (code {return_code}):\n{summary}")


def _find_stem_directory(output_root: Path) -> Path:
    candidates = [stem.parent for stem in output_root.rglob("vocals.wav")]
    if not candidates:
        raise SeparationError("Could not find Demucs stem output folder.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _build_accompaniment(stem_dir: Path, output_file: Path) -> None:
    accompaniment_stems = [stem_dir / "drums.wav", stem_dir / "bass.wav", stem_dir / "other.wav"]
    missing = [path.name for path in accompaniment_stems if not path.exists()]
    if missing:
        raise SeparationError(f"Cannot build accompaniment, missing stems: {', '.join(missing)}")

    _mix_wav_files(accompaniment_stems, output_file)


def _mix_wav_files(input_files: Iterable[Path], output_file: Path) -> None:
    tracks: list[np.ndarray] = []
    sample_rate: int | None = None
    max_length = 0
    max_channels = 1

    for file_path in input_files:
        data, sr = sf.read(file_path, always_2d=True)
        data = data.astype(np.float32)

        if sample_rate is None:
            sample_rate = sr
        elif sample_rate != sr:
            raise SeparationError("Sample rates do not match across stems.")

        tracks.append(data)
        max_length = max(max_length, data.shape[0])
        max_channels = max(max_channels, data.shape[1])

    if sample_rate is None:
        raise SeparationError("No audio tracks were provided for mixing.")

    mix = np.zeros((max_length, max_channels), dtype=np.float32)
    for track in tracks:
        normalized = _match_channels(track, target_channels=max_channels)
        padded = np.zeros((max_length, max_channels), dtype=np.float32)
        padded[: normalized.shape[0], :] = normalized
        mix += padded

    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 1.0:
        mix /= peak

    sf.write(output_file, mix, samplerate=sample_rate, subtype="PCM_16")


def _match_channels(track: np.ndarray, target_channels: int) -> np.ndarray:
    channels = track.shape[1]
    if channels == target_channels:
        return track

    if channels == 1 and target_channels > 1:
        return np.repeat(track, target_channels, axis=1)

    if channels > target_channels:
        return track[:, :target_channels]

    padding = np.zeros((track.shape[0], target_channels - channels), dtype=np.float32)
    return np.concatenate([track, padding], axis=1)


def _normalize_targets(selected_targets: Iterable[str]) -> list[str]:
    unique_targets: list[str] = []
    for target in selected_targets:
        stem = target.strip().lower()
        if stem and stem in AVAILABLE_TARGETS and stem not in unique_targets:
            unique_targets.append(stem)
    return unique_targets
