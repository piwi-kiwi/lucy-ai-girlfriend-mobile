#!/usr/bin/env python3
"""Kokoro-82M en local (ONNX), sans dépendance à `phonemizer` (cassé sous Android).

phonemizer >= 3.4 passe par `dlinfo`, absent de la libc d'Android ; on parle donc
directement à libespeak-ng.so via ctypes, et on injecte un faux module `phonemizer`
avant d'importer kokoro_onnx (qui ne s'en sert que pour la phonémisation).
"""
import ctypes, os, re, sys, types, threading, warnings
warnings.filterwarnings('ignore', message='Unsupported platform')

LIB = os.environ.get("ESPEAK_LIB") or "/data/data/com.termux/files/usr/lib/libespeak-ng.so"
DATA = (os.environ.get("ESPEAK_DATA")
        or "/data/data/com.termux/files/usr/share/espeak-ng-data")
MODEL = os.environ.get("KOKORO_MODEL") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       "kokoro", "kokoro-v1.0.onnx")
VOICES = os.environ.get("KOKORO_VOICES") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "kokoro", "voices-v1.0.bin")
SAMPLE_RATE = 24000

_lib = ctypes.CDLL(LIB)
_lib.espeak_Initialize.restype = ctypes.c_int
_lib.espeak_Initialize(2, 0, DATA.encode(), 0)          # 2 = sortie synchrone
_lib.espeak_SetVoiceByName.restype = ctypes.c_int
_lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
_lib.espeak_TextToPhonemes.argtypes = [ctypes.POINTER(ctypes.c_char_p),
                                       ctypes.c_int, ctypes.c_int]
_lock = threading.Lock()
_cur_voice = None
_VOICE = {}


def _voice_for(lang):
    """Choisit une voix espeak-ng disponible pour la langue demandée."""
    if lang in _VOICE:
        return _VOICE[lang]
    cand = [lang, lang.replace("_", "-"), lang.split("-")[0]]
    for c in cand:
        if c and _lib.espeak_SetVoiceByName(c.encode()) == 0:
            _VOICE[lang] = c
            return c
    _VOICE[lang] = "en"
    return "en"


def phonemize(text, language="fr-fr", preserve_punctuation=True, with_stress=True, **_):
    """IPA espeak-ng, filtré comme le fait phonemizer."""
    global _cur_voice
    with _lock:
        v = _voice_for(language)
        if v != _cur_voice:
            _lib.espeak_SetVoiceByName(v.encode())
            _cur_voice = v
        buf = ctypes.c_char_p(text.encode("utf-8"))
        out = []
        while True:
            r = _lib.espeak_TextToPhonemes(ctypes.byref(buf), 1, 0x02)
            if not r:
                break
            out.append(r.decode("utf-8", "replace"))
    s = "".join(out)
    s = re.sub(r"\([a-z]{2,3}\)", "", s)                # marqueurs (en) (fr)
    s = s.replace("\u2019", "'").replace("‿", " ")
    if not preserve_punctuation:
        s = re.sub(r"[.,;:!?…\-–—\"«»“”]", "", s)
    if not with_stress:
        s = re.sub(r"[ˈˌ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _install_shim():
    """Faux `phonemizer` + faux `espeakng_loader` pour kokoro_onnx."""
    fake = types.ModuleType("phonemizer")
    fake.phonemize = phonemize
    fake.__path__ = []
    sys.modules["phonemizer"] = fake

    for name in ("phonemizer.backend", "phonemizer.backend.espeak"):
        m = types.ModuleType(name)
        m.__path__ = []
        sys.modules[name] = m
    wrapper = types.ModuleType("phonemizer.backend.espeak.wrapper")

    class EspeakWrapper:
        @classmethod
        def set_data_path(cls, p): pass

        @classmethod
        def set_library(cls, p): pass

    wrapper.EspeakWrapper = EspeakWrapper
    sys.modules["phonemizer.backend.espeak.wrapper"] = wrapper
    sys.modules["phonemizer.backend"].espeak = sys.modules["phonemizer.backend.espeak"]
    sys.modules["phonemizer.backend.espeak"].wrapper = wrapper

    loader = types.ModuleType("espeakng_loader")
    loader.get_library_path = lambda: LIB
    loader.get_data_path = lambda: DATA
    sys.modules["espeakng_loader"] = loader


_K = None


def engine(model=None, voices=None):
    """Charge (une seule fois) le modèle Kokoro."""
    global _K
    if _K is not None:
        return _K
    _install_shim()
    import os
    from kokoro_onnx import Kokoro
    _K = Kokoro(model or MODEL, voices or VOICES)
    return _K


def say(text, voice="ff_siwis", speed=1.0, lang="fr-fr"):
    """-> numpy float32 mono 24 kHz"""
    k = engine()
    audio, sr = k.create(text, voice=voice, speed=speed, lang=lang)
    return audio, sr


if __name__ == "__main__":
    import sys, wave
    txt = " ".join(sys.argv[1:]) or "Salut, je suis Lucy, ça gaze ?"
    a, sr = say(txt)
    out = "voice/kokoro_test.wav"
    import numpy as np
    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm)
    print(f"{out}  {len(a)/sr:.2f}s  {sr} Hz  ({len(a)} échantillons)")
