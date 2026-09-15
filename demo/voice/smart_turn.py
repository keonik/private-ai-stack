"""Smart Turn v3.2: does this pause sound like the end of what someone meant to say?

A pause alone cannot tell "what's the tallest mountain in… (thinking) …Ohio" from "what's the tallest
mountain?". Smart Turn listens to the last eight seconds — intonation and words together — and returns a
probability that the turn is finished. It is Pipecat's open model (BSD-2, 8.7 MB, CPU-only ONNX), the same
one Hugging Face's speech-to-speech runs behind its VAD, and it is what stands in here for the closed
"smart_turn" detection in Alibaba's realtime voice models.

The model wants Whisper's log-mel features. The reference implementation computes them with the
`transformers` library, which would add hundreds of megabytes to an image that otherwise holds a small
FastAPI app, so they are reproduced below in numpy. `check_features()` compares the two; they agree to
within float rounding (see the README).
"""
from __future__ import annotations

import io
import time
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
SECONDS = 8
N_FFT = 400
HOP = 160
N_MELS = 80
MODEL_FILE = "smart-turn-v3.2-cpu.onnx"
MODEL_URL = f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/{MODEL_FILE}"


def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    # Slaney: linear below 1 kHz, logarithmic above.
    return np.where(f < 1000.0, 3.0 * f / 200.0, 15.0 + 27.0 * np.log(np.maximum(f, 1e-9) / 1000.0) / np.log(6.4))


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    return np.where(m < 15.0, 200.0 * m / 3.0, 1000.0 * np.power(6.4, (m - 15.0) / 27.0))


def _mel_filters() -> np.ndarray:
    fft_freqs = np.linspace(0, SAMPLE_RATE // 2, 1 + N_FFT // 2)
    filter_freqs = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(8000.0), N_MELS + 2))
    diff = np.diff(filter_freqs)
    slopes = filter_freqs[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    bank = np.maximum(0.0, np.minimum(down, up))
    bank *= (2.0 / (filter_freqs[2:N_MELS + 2] - filter_freqs[:N_MELS]))[None, :]
    return bank  # (201, 80)


_FILTERS = _mel_filters()
_WINDOW = np.hanning(N_FFT + 1)[:-1]  # periodic Hann, as Whisper uses


def features(audio: np.ndarray) -> np.ndarray:
    """The last eight seconds as Whisper log-mel features, shape (1, 80, 800)."""
    x = np.asarray(audio, dtype=np.float32)
    n = SECONDS * SAMPLE_RATE
    x = x[-n:] if x.size > n else np.pad(x, (n - x.size, 0))
    x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)                       # do_normalize=True
    padded = np.pad(x.astype(np.float64), N_FFT // 2, mode="reflect")   # center=True
    frames = np.lib.stride_tricks.sliding_window_view(padded, N_FFT)[::HOP]
    power = np.abs(np.fft.rfft(frames * _WINDOW, axis=1)) ** 2          # (801, 201)
    mel = np.maximum(1e-10, power @ _FILTERS)                            # (801, 80)
    log = np.log10(mel).T[:, :-1]                                        # (80, 800), last frame dropped
    log = np.maximum(log, log.max() - 8.0)
    return ((log + 4.0) / 4.0).astype(np.float32)[None]


def pcm16_wav(blob: bytes) -> np.ndarray:
    """A 16 kHz mono PCM16 WAV — what the page uploads — as float samples."""
    with wave.open(io.BytesIO(blob)) as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("Smart Turn needs 16 kHz mono 16-bit audio")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


class SmartTurn:
    def __init__(self, model_path: str | Path, threshold: float = 0.5) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.threshold = threshold
        self.predict(np.zeros(SAMPLE_RATE, dtype=np.float32))  # the first run is several times slower

    def predict(self, audio: np.ndarray) -> dict:
        t0 = time.perf_counter()
        p = float(np.asarray(self.session.run(None, {self.input: features(audio)})[0]).reshape(-1)[0])
        return {"complete": p > self.threshold, "probability": round(p, 3),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}


def check_features(audio: np.ndarray) -> float:
    """Largest absolute difference from the transformers implementation (needs transformers installed)."""
    from transformers import WhisperFeatureExtractor

    n = SECONDS * SAMPLE_RATE
    x = np.asarray(audio, dtype=np.float32)
    x = x[-n:] if x.size > n else np.pad(x, (n - x.size, 0))
    ref = WhisperFeatureExtractor(chunk_length=SECONDS)(
        x, sampling_rate=SAMPLE_RATE, return_tensors="np", padding="max_length", max_length=n,
        truncation=True, do_normalize=True).input_features
    return float(np.abs(ref - features(audio)).max())
