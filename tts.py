#!/usr/bin/env python3
"""Synthèse vocale multi-moteurs, sortie MP3/Ogg prête pour <audio>.

Bloc "tts" de llm.json :

  { "engine": "fish",                       // edge | fish | eleven | none
    "voice":  "fr-FR-DeniseNeural",         // moteur edge
    "key":    "…",                          // fish / eleven
    "reference_id": "…",                    // fish : id de la voix clonée (facultatif)
    "model":  "s1",                         // fish : s1 | speech-1.5 | speech-1.4
    "voice_id": "…",                        // eleven : id de la voix
    "rate": "+0%", "pitch": "+4Hz" }        // edge
"""
import json, os, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "llm.json")


def conf():
    path = CONF if os.path.exists(CONF) else CONF.replace(".json", ".example.json")
    try:
        c = json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        c = {}
    t = c.get("tts") or {}
    if not t.get("engine"):
        t["engine"] = "fish" if t.get("fish_key") else "edge"
    return t


def _http(url, data=None, headers=None, timeout=90):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("HTTP %s — %s" % (e.code, body))
    except urllib.error.URLError as e:
        raise RuntimeError("réseau : %s" % e.reason)


# ---------------------------------------------------------------- fish.audio
def fish(text, t):
    key = t.get("key") or t.get("fish_key")
    if not key:
        raise RuntimeError("clé fish.audio absente (bloc tts de llm.json)")
    body = {"text": text, "format": t.get("format", "mp3"),
            "mp3_bitrate": int(t.get("bitrate", 128)),
            "chunk_length": int(t.get("chunk_length", 200)),
            "normalize": True, "latency": t.get("latency", "normal")}
    if t.get("reference_id"):
        body["reference_id"] = t["reference_id"]
    if t.get("prosody_speed"):
        body["prosody"] = {"speed": float(t["prosody_speed"])}
    h = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
         "model": t.get("model") or "s1"}
    return _http("https://api.fish.audio/v1/tts", json.dumps(body).encode(), h)


# ----------------------------------------------------------------- elevenlabs
def eleven(text, t):
    key = t.get("key") or t.get("eleven_key")
    if not key:
        raise RuntimeError("clé elevenlabs absente")
    vid = t.get("voice_id") or "21m00Tcm4TlvDq8ikWAM"
    body = {"text": text,
            "model_id": t.get("model") or "eleven_multilingual_v2",
            "voice_settings": {"stability": t.get("stability", 0.45),
                               "similarity_boost": t.get("similarity", 0.75),
                               "style": t.get("style", 0.15),
                               "use_speaker_boost": True}}
    h = {"xi-api-key": key, "Content-Type": "application/json",
         "Accept": "audio/mpeg"}
    return _http("https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_128"
                 % vid, json.dumps(body).encode(), h)


# --------------------------------------------------------- kokoro (local, ONNX)
def kokoro(text, t):
    """Kokoro-82M sur l'appareil, via libespeak-ng.so (voir kokoro_local.py)."""
    import io, wave
    import numpy as np
    import kokoro_local
    audio, sr = kokoro_local.say(text,
                                 voice=t.get("voice") or "ff_siwis",
                                 speed=float(t.get("speed", 1.0)),
                                 lang=t.get("lang") or "fr-fr")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


# ------------------------------------------------------- qwen3-tts (DashScope)
AUDIO_MAGIC = (b"ID3", b"OggS", b"RIFF", b"fLaC", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


def _looks_audio(b):
    return len(b) > 2000 and any(b.startswith(m) for m in AUDIO_MAGIC)


def qwen(text, t):
    """Qwen3-TTS via Alibaba Model Studio (DashScope), intl ou cn."""
    key = t.get("key")
    if not key:
        raise RuntimeError("clé DashScope absente (bloc tts de llm.json)")
    base = (t.get("base") or "https://dashscope-intl.aliyuncs.com").rstrip("/")
    model = t.get("model") or "qwen3-tts-flash"
    voice = t.get("qwen_voice") or t.get("voice_name") or "Cherry"
    lang = t.get("language_type") or "French"
    h = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    errs = []

    # 1) mode "compatible OpenAI" : /compatible-mode/v1/audio/speech -> flux binaire
    try:
        body = {"model": model, "input": text, "voice": voice, "response_format": "mp3"}
        data = _http(base + "/compatible-mode/v1/audio/speech", json.dumps(body).encode(), h)
        if _looks_audio(data):
            return data
        errs.append("audio/speech: réponse non audio (%d o) %s" % (len(data), data[:120]))
    except RuntimeError as e:
        errs.append("audio/speech: %s" % e)

    # 2) API native multimodal-generation -> renvoie une URL à télécharger
    try:
        body = {"model": model,
                "input": {"text": text, "voice": voice, "language_type": lang},
                "parameters": {"audio_format": t.get("format", "mp3")}}
        raw = _http(base + "/api/v1/services/aigc/multimodal-generation/generation",
                    json.dumps(body).encode(), h)
        j = json.loads(raw.decode("utf-8", "replace"))
        url = (((j.get("output") or {}).get("audio") or {}).get("url"))
        if not url:
            raise RuntimeError("pas d'URL audio dans la réponse : %s" % json.dumps(j)[:240])
        data = _http(url)
        if _looks_audio(data):
            return data
        raise RuntimeError("fichier téléchargé non audio (%d o)" % len(data))
    except RuntimeError as e:
        errs.append("multimodal: %s" % e)

    raise RuntimeError(" | ".join(errs))


# ------------------------------------------------- gemini 2.5 flash TTS (Google)
def _wav(pcm, rate=24000, ch=1, bits=16):
    import struct
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, ch, rate,
                      rate * ch * bits // 8, ch * bits // 8, bits)
    body = b"WAVE" + fmt + struct.pack("<4sI", b"data", len(pcm)) + pcm
    return struct.pack("<4sI", b"RIFF", len(body)) + body


def gemini(text, t):
    key = t.get("key") or t.get("google_key")
    if not key:
        raise RuntimeError("clé Google AI Studio absente")
    import base64
    model = t.get("model") or "gemini-2.5-flash-preview-tts"
    voice = t.get("voice_name") or "Aoede"
    url = ("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
           % (model, key))
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {
                                     "prebuiltVoiceConfig": {"voiceName": voice}}}}}
    raw = _http(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    j = json.loads(raw.decode("utf-8", "replace"))
    try:
        part = j["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
    except Exception:
        raise RuntimeError("réponse inattendue : %s" % json.dumps(j)[:240])
    return _wav(pcm)


# ------------------------------------------------------------------- edge-tts
def edge(text, t):
    import asyncio, edge_tts
    chunks = []

    async def run():
        c = edge_tts.Communicate(text, t.get("edge_voice") or "fr-FR-DeniseNeural",
                                 rate=t.get("rate") or "+0%",
                                 pitch=t.get("pitch") or "+4Hz")
        async for ch in c.stream():
            if ch["type"] == "audio":
                chunks.append(ch["data"])
    asyncio.run(run())
    return b"".join(chunks)


ENGINES = {"fish": fish, "eleven": eleven, "edge": edge, "qwen": qwen,
           "gemini": gemini, "kokoro": kokoro}


def synth(text, t=None):
    """-> (bytes audio, extension) ; essaie t["fallback"] si le moteur principal échoue."""
    t = t or conf()
    eng = (t.get("engine") or "edge").lower()
    chain = [eng]
    fb = (t.get("fallback") or "").lower()
    if fb and fb != eng:
        chain.append(fb)
    errs = []
    for e in chain:
        if e not in ENGINES:
            errs.append("%s: moteur inconnu" % e)
            continue
        try:
            data = ENGINES[e](text, t)
            if len(data) < 800:
                raise RuntimeError("audio vide (%d octets)" % len(data))
            ext = "ogg" if t.get("format") == "opus" else "mp3"
            if data[:4] == b"RIFF":
                ext = "wav"
            if e != eng:
                sys.stderr.write("[tts] repli sur %s (%s)\n" % (e, errs[-1] if errs else ""))
            return data, ext
        except Exception as ex:
            errs.append("%s: %s" % (e, ex))
    raise RuntimeError(" | ".join(errs))


if __name__ == "__main__":
    import sys
    txt = " ".join(sys.argv[1:]) or "Salut, je suis Lucy."
    audio, ext = synth(txt)
    out = os.path.join(HERE, "voice", "test." + ext)
    open(out, "wb").write(audio)
    print("%s  %d octets  (%s)" % (out, len(audio), conf().get("engine")))
