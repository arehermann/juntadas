#!/usr/bin/env python3
"""Abre un túnel gratis de Cloudflare hacia el servidor de Juntadas
y anota la dirección nueva en GitHub (archivo backend.json del repo),
para que la página sepa dónde encontrar la Raspberry.

Lee la configuración de ~/.juntadas.conf:
  GITHUB_TOKEN=github_pat_...
  GITHUB_REPO=arehermann/juntadas
  PUERTO=8787
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONF = os.path.expanduser("~/.juntadas.conf")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def read_conf():
    conf = {}
    if os.path.exists(CONF):
        for line in open(CONF, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()
    return conf


def gh(method, url, token, data=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(data).encode() if data else None)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "juntadas-pi")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def publish(url, conf):
    token, repo = conf.get("GITHUB_TOKEN"), conf.get("GITHUB_REPO", "arehermann/juntadas")
    if not token:
        print("Falta GITHUB_TOKEN en", CONF, "- no puedo avisarle a GitHub.", flush=True)
        return
    api = "https://api.github.com/repos/%s/contents/backend.json" % repo
    body = json.dumps({"url": url, "updated": int(time.time())}, indent=2) + "\n"
    for attempt in range(8):
        try:
            sha = None
            try:
                sha = gh("GET", api, token)["sha"]
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
            payload = {"message": "Raspberry: nueva dirección del servidor",
                       "content": base64.b64encode(body.encode()).decode()}
            if sha:
                payload["sha"] = sha
            gh("PUT", api, token, payload)
            print("GitHub actualizado con", url, flush=True)
            return
        except Exception as e:  # sin internet todavía, conflicto, etc.
            print("No pude actualizar GitHub (%s). Reintento..." % e, flush=True)
            time.sleep(min(60, 5 * (attempt + 1)))


def main():
    conf = read_conf()
    port = conf.get("PUERTO", "8787")
    cmd = ["cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:" + port]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    published = False
    for line in proc.stdout:
        sys.stdout.write(line)
        if not published:
            m = URL_RE.search(line)
            if m:
                published = True
                print("Dirección del túnel:", m.group(0), flush=True)
                publish(m.group(0), conf)
    sys.exit(proc.wait() or 1)


if __name__ == "__main__":
    main()
