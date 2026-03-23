import math
import os
import struct
import subprocess
import tempfile

_SOUNDS = {}


def _make_wav(tone_sequence, sample_rate=44100, volume=0.85):
    """
    Generate WAV bytes from a list of (freq_hz, duration_sec).
    Use freq=0 for silence gaps between beeps.
    Pure stdlib — no external libraries needed.
    """
    samples = []
    for freq, dur in tone_sequence:
        n = int(sample_rate * dur)
        for i in range(n):
            if freq == 0:
                samples.append(0.0)
            else:
                t = i / sample_rate
                # Smooth envelope: 5ms attack, 10ms release — avoids clicks
                env = 1.0
                atk = int(0.005 * sample_rate)
                rel = int(0.010 * sample_rate)
                if i < atk:
                    env = i / atk
                elif i > n - rel:
                    env = (n - i) / rel
                samples.append(math.sin(2 * math.pi * freq * t) * env * volume)

    pcm = struct.pack(
        f"<{len(samples)}h",
        *[max(-32768, min(32767, int(s * 32767))) for s in samples]
    )
    data_size = len(pcm)
    byte_rate  = sample_rate * 2        # mono 16-bit
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate,
        byte_rate, 2, 16,
        b"data", data_size
    )
    return header + pcm


def _write_temp_wav(wav_bytes):
    """Write WAV bytes to a named temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="scanner_alert_")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(wav_bytes)
    return path


def _play_wav(path):
    """
    Play a WAV file using the best available player.
    Tries: ffplay → aplay → paplay → pw-play.
    Non-blocking — fires and forgets.
    """
    players = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        ["aplay",  "-q", path],
        ["paplay", path],
        ["pw-play", path],
    ]
    for cmd in players:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return  # First one that launches wins
        except FileNotFoundError:
            continue  # Try next player


# Pre-bake all 4 alert WAVs at import time — stored in memory as temp files.
# Generated once, reused for every alert. Deleted on process exit.

def _init_sounds():
    """Generate and cache all alert WAVs. Called once at startup."""
    global _SOUNDS
    definitions = {
        # STRONG BUY: 3-beep aggressive ascending burst
        "PRE-BREAKOUT": [
            (660, 0.12), (0, 0.04), (880, 0.12), (0, 0.04), (1100, 0.20),
        ],
        "STRONG BUY": [
            (880, 0.14), (0, 0.04),
            (1100, 0.14), (0, 0.04),
            (1320, 0.24),
        ],
        # STRONG SELL: 3-beep aggressive descending burst
        "STRONG SELL": [
            (1100, 0.14), (0, 0.04),
            (880,  0.14), (0, 0.04),
            (660,  0.24),
        ],
        # BUY: clean double ascending
        "BUY": [
            (700, 0.16), (0, 0.05),
            (950, 0.22),
        ],
        # SELL: clean double descending
        "SELL": [
            (950, 0.16), (0, 0.05),
            (700, 0.22),
        ],
    }
    for name, seq in definitions.items():
        try:
            wav   = _make_wav(seq)
            path  = _write_temp_wav(wav)
            _SOUNDS[name] = path
        except Exception as e:
            _SOUNDS[name] = None   # Graceful fallback — never crash at startup

_init_sounds()   # Run once when module loads
