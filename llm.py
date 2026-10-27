#!/usr/bin/env python3
"""Petit client LLM multi-fournisseurs, sans dépendance externe (urllib).

Configuration dans llm.json (à côté de ce fichier) :

    {
      "provider": "openai",                  // openai | anthropic | gemini | ollama
      "base":     "https://api.openai.com/v1",
      "key":      "sk-...",
      "model":    "gpt-4o-mini",
      "max_tokens": 220,
      "temperature": 0.8,
      "system":   "..."                      // facultatif
    }

`provider` peut être deviné depuis l'URL si absent.
"""
import json, os, urllib.request, urllib.error, time

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "llm.json")
CONF_EXAMPLE = os.path.join(HERE, "llm.example.json")


def _conf_path():
    """llm.json s'il existe, sinon llm.example.json (dépôt public : pas de clé)."""
    for p in (CONF, CONF_EXAMPLE):
        if os.path.exists(p):
            return p
    return CONF

SYSTEM_DEFAULT = (
    "Tu es Lucy, une jeune femme cyberpunk qui vit dans un petit appartement "
    "de Night City. Tu parles français, avec un ton direct, curieux et un peu "
    "taquin. Tu réponds en 1 ou 2 phrases maximum, à l'oral (pas de listes, pas "
    "de markdown, pas d'emoji, pas d'astérisques). Tu peux bouger, regarder ton "
    "interlocuteur, hésiter. Tu ne dis jamais que tu es une IA."
)


def load_conf():
    try:
        with open(_conf_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        return {"_error": f"llm.json illisible : {e}"}


def _post(url, payload, headers, timeout=70):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {e.code} — {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"réseau : {e.reason}")


def _openai(conf, messages, system):
    base = conf.get("base", "https://api.openai.com/v1").rstrip("/")
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    r = _post(base + "/chat/completions", {
        "model": conf.get("model", "gpt-4o-mini"),
        "messages": msgs,
        "temperature": conf.get("temperature", 0.8),
        "max_tokens": conf.get("max_tokens", 220),
    }, {"Authorization": "Bearer " + conf.get("key", "")})
    return r["choices"][0]["message"]["content"]


def _anthropic(conf, messages, system):
    base = conf.get("base", "https://api.anthropic.com/v1").rstrip("/")
    r = _post(base + "/messages", {
        "model": conf.get("model", "claude-3-5-haiku-20241022"),
        "system": system,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "temperature": conf.get("temperature", 0.8),
        "max_tokens": conf.get("max_tokens", 220),
    }, {"x-api-key": conf.get("key", ""), "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in r.get("content", []))


def _gemini(conf, messages, system):
    base = conf.get("base", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    model = conf.get("model", "gemini-1.5-flash")
    url = f"{base}/models/{model}:generateContent?key={conf.get('key','')}"
    body = {
        "contents": [{"role": "user" if m["role"] == "user" else "model",
                      "parts": [{"text": m["content"]}]} for m in messages],
        "generationConfig": {"temperature": conf.get("temperature", 0.8),
                             "maxOutputTokens": conf.get("max_tokens", 220)},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    r = _post(url, body, {})
    return "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])


def _piwi(conf, messages, system):
    """API native piwiai (SSE uniquement) : POST /api/chat?token=…"""
    base = conf.get("base", "https://api.piwiai.com").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/api/chat?token={conf.get('key','')}"
    payload = {
        "messages": messages,
        "system_prompt": system,
        "max_tokens": conf.get("max_tokens", 200),
        "temperature": conf.get("temperature", 0.8),
        "stream": True,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    out = []
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    if chunk == "[DONE]":
                        break
                    continue
                try:
                    j = json.loads(chunk)
                except Exception:
                    continue
                if "token" in j:
                    out.append(j["token"])
                elif "error" in j:
                    raise RuntimeError(str(j["error"]))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code} — {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"réseau : {e.reason}")
    return "".join(out)


def _ollama(conf, messages, system):
    base = conf.get("base", "http://127.0.0.1:11434").rstrip("/")
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    r = _post(base + "/api/chat", {
        "model": conf.get("model", "llama3"),
        "messages": msgs,
        "stream": False,
        "options": {"temperature": conf.get("temperature", 0.8),
                    "num_predict": conf.get("max_tokens", 220)},
    }, {}, timeout=180)
    return r["message"]["content"]


PROVIDERS = {"openai": _openai, "anthropic": _anthropic, "gemini": _gemini,
             "ollama": _ollama, "piwi": _piwi}


def guess_provider(conf):
    p = (conf.get("provider") or "").lower()
    if p in PROVIDERS:
        return p
    base = (conf.get("base") or "").lower()
    key = conf.get("key") or ""
    if "piwiai" in base or key.startswith("piwi_"):
        return "piwi"
    if "anthropic" in base or key.startswith("sk-ant"):
        return "anthropic"
    if "generativelanguage" in base or "googleapis" in base or key.startswith("AIza"):
        return "gemini"
    if "11434" in base or "ollama" in base:
        return "ollama"
    return "openai"


def chat(messages, conf=None):
    """messages : [{'role':'user'|'assistant','content':'...'}] -> texte de réponse."""
    conf = conf or load_conf()
    if conf is None:
        raise RuntimeError("aucun llm.json : API non configurée")
    if "_error" in conf:
        raise RuntimeError(conf["_error"])
    provider = guess_provider(conf)
    if not (conf.get("key") or "").strip() and provider not in ("ollama",):
        raise RuntimeError(
            "no API key: copy llm.example.json to llm.json and fill in 'key' "
            "(or set provider to 'ollama' for a local model). See README.")
    system = conf.get("system") or SYSTEM_DEFAULT
    t0 = time.time()
    text = PROVIDERS[provider](conf, messages, system)
    text = (text or "").strip()
    import re as _re
    text = _re.sub(r"<thinking>.*?</thinking>", "", text, flags=_re.S).strip()
    # nettoyage : markdown, guillemets de dialogue, préfixes "Lucy :"
    for junk in ("**", "__", "`", "###"):
        text = text.replace(junk, "")
    if text.lower().startswith("lucy :"):
        text = text[6:].strip()
    text = text.strip().strip('"').strip()
    return {"text": text, "provider": provider, "model": conf.get("model"),
            "ms": int((time.time() - t0) * 1000)}


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Salut, tu fais quoi ?"
    print(chat([{"role": "user", "content": q}]))
