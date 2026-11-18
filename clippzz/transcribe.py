"""Speech-to-text with word timings, for the captions. Runs on the Pi with
faster-whisper. The model is downloaded once into /data/models."""
import gc
import logging
import wave
from pathlib import Path

from . import config

log = logging.getLogger("clippzz.transcribe")


def _samples(wav_path: Path):
    """The 16 kHz mono 16-bit WAV as float samples. Read directly, so
    faster-whisper never needs its own audio decoder (PyAV)."""
    import numpy as np

    with wave.open(str(wav_path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError("expected 16 kHz mono 16-bit audio")
        pcm = w.readframes(w.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def words(audio: Path, prompt: str = "") -> list[list]:
    """[[start_s, end_s, word], ...] for a 16 kHz mono WAV."""
    from faster_whisper import WhisperModel  # heavy import, only needed here

    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                         cpu_threads=config.WHISPER_THREADS, download_root=str(config.MODELS_DIR))
    try:
        segments, _ = model.transcribe(
            _samples(audio), language="en", beam_size=1, word_timestamps=True,
            vad_filter=False, condition_on_previous_text=False,
            initial_prompt=prompt or None)
        out = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                # Very unsure words are usually noise or music heard as speech.
                if text and (w.probability is None or w.probability >= 0.1):
                    out.append([round(float(w.start), 2), round(float(w.end), 2), text])
        return out
    finally:
        del model
        gc.collect()
