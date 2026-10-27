#!/usr/bin/env python3
"""Serveur statique robuste (multi-thread, keep-alive, 0.0.0.0) pour le viewer VRM."""
import os, sys, json, socket, subprocess, time, hashlib, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
os.chdir(ROOT)


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive correct
    timeout = 120

    def log_message(self, fmt, *a):
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % a))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    # ---------- synthèse vocale neuronale (edge-tts) ----------
    def do_GET(self):
        if self.path.startswith("/api/tts"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            text = (q.get("text") or [""])[0].strip()[:600]
            if not text:
                return self._json(400, {"error": "texte vide"})
            try:
                import tts
                tconf = tts.conf()
                eng = (q.get("engine") or [""])[0].strip().lower()
                if eng:
                    tconf["engine"] = eng
                v = (q.get("voice") or [""])[0].strip()
                if v:
                    if tconf.get("engine") == "edge":
                        tconf["edge_voice"] = v
                    else:
                        tconf["voice"] = v
                cache = os.path.join(ROOT, "voice", "cache")
                os.makedirs(cache, exist_ok=True)
                sig = json.dumps(tconf, sort_keys=True, ensure_ascii=False)
                key = hashlib.sha1((sig + text).encode()).hexdigest()[:20]
                hits = [f for f in (key + ".mp3", key + ".ogg", key + ".wav")
                        if os.path.exists(os.path.join(cache, f))]
                if hits:
                    path = os.path.join(cache, hits[0])
                    body = open(path, "rb").read()
                else:
                    body, ext = tts.synth(text, tconf)
                    path = os.path.join(cache, key + "." + ext)
                    tmp = path + ".part"
                    open(tmp, "wb").write(body)
                    os.replace(tmp, path)
            except Exception as e:
                return self._json(503, {"error": "%s: %s" % (type(e).__name__, e)})
            self.send_response(200)
            ctype = "audio/wav" if body[:4] == b"RIFF" else "audio/mpeg"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # petite page d'état, pratique pour tester depuis le navigateur
        if self.path.startswith("/ping"):
            body = b"pong\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    # ---------- API : dialogue avec le LLM ----------
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/stt":
            return self._stt()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            req = json.loads(raw or b"{}")
        except Exception as e:
            return self._json(400, {"error": "JSON invalide : %s" % e})

        if path == "/api/chat":
            try:
                import llm
                msgs = req.get("messages")
                if not msgs:
                    msgs = [{"role": "user", "content": req.get("text", "")}]
                msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                        for m in msgs][-16:]
                return self._json(200, llm.chat(msgs))
            except Exception as e:
                return self._json(500, {"error": "%s: %s" % (type(e).__name__, e)})

        return self._json(404, {"error": "route inconnue : %s" % path})

    # ---------------------------------------------------------------- STT
    def _stt(self):
        """WAV 16 kHz mono (corps brut) -> texte via whisper.cpp."""
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self._json(400, {"error": "audio vide"})
        if n > 30_000_000:
            return self._json(413, {"error": "audio trop gros"})
        raw = self.rfile.read(n)
        if raw[:4] != b"RIFF":
            return self._json(400, {"error": "WAV attendu"})
        binary = None
        for rel in ("whisper-cli", "build/wbuild/bin/whisper-cli", "build/wbuild/bin/main"):
            f = os.path.join(ROOT, rel)
            if os.path.exists(f):
                binary = f
                break
        model = os.path.join(ROOT, "ggml-base-q5_1.bin")
        if not binary or not os.path.exists(model):
            return self._json(503, {"error": "whisper.cpp ou modèle absent"})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        lang = (q.get("lang") or ["auto"])[0][:8] or "auto"
        d = os.path.join(ROOT, "voice", "stt")
        os.makedirs(d, exist_ok=True)
        stamp = int(time.time() * 1000)
        wav = os.path.join(d, "in_%d.wav" % stamp)
        with open(wav, "wb") as f:
            f.write(raw)
        # whisper.cpp exige 16 kHz mono : on convertit si besoin
        try:
            import wave as _w
            with _w.open(wav) as wf:
                rate, chans = wf.getframerate(), wf.getnchannels()
            if rate != 16000 or chans != 1:
                conv = os.path.join(d, "cv_%d.wav" % stamp)
                if subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                                   "-ar", "16000", "-ac", "1", conv]).returncode == 0:
                    os.remove(wav)
                    wav = conv
        except Exception:
            pass
        try:
            t0 = time.time()
            proc = subprocess.run([binary, "-m", model, "-f", wav, "-l", lang,
                                   "-t", "4", "-nt", "-bs", "5"],
                                  capture_output=True, text=True, timeout=180)
            lines = []
            for line in (proc.stdout or "").splitlines():
                ln = line.strip()
                if not ln or ln.startswith(("whisper_", "system_info", "main:",
                                            "ggml_", "error:", "warning:")):
                    continue
                lines.append(ln)
            text = " ".join(" ".join(lines).split())
            return self._json(200, {"text": text, "ms": int((time.time() - t0) * 1000)})
        except Exception as e:
            return self._json(500, {"error": "%s: %s" % (type(e).__name__, e)})
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    def copyfile(self, source, outputfile):
        # gros fichier (17 Mo) : envoi par blocs, tolérant aux coupures
        try:
            while True:
                buf = source.read(256 * 1024)
                if not buf:
                    break
                outputfile.write(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return None


if __name__ == "__main__":
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ip = lan_ip()
    print(f"[serve] racine : {ROOT}")
    print(f"[serve] local  : http://127.0.0.1:{PORT}/")
    if ip:
        print(f"[serve] réseau : http://{ip}:{PORT}/")
    print(f"[serve] état   : http://127.0.0.1:{PORT}/ping")
    with open(os.path.join(ROOT, "server.pid"), "w") as fh:
        fh.write(str(os.getpid()))
    sys.stdout.flush()
    httpd.serve_forever()
