"""Copia automática de los datos a un repositorio PRIVADO de GitHub.

Una vez por día (si hubo cambios) sube datos.json al repo indicado en
~/.juntadas.conf como BACKUP_REPO (por ejemplo arehermann/juntadas-datos).
Git guarda la historia, así que se puede volver a cualquier día anterior.

Nunca usar el repo público "juntadas" para esto: el servidor se niega.
Las llaves personales no están en los datos (solo su huella), así que una copia
filtrada no sirve para entrar como otra persona.
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request

CONF = os.path.expanduser("~/.juntadas.conf")
EVERY = 24 * 3600
CHECK = 30 * 60


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
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def push(path, lock, conf):
    token, repo = conf.get("GITHUB_TOKEN"), conf.get("BACKUP_REPO")
    info = gh("GET", "https://api.github.com/repos/" + repo, token)
    if not info.get("private"):
        raise RuntimeError("el repo %s NO es privado; no subo datos ahí" % repo)
    with lock:
        raw = open(path, "rb").read()
    api = "https://api.github.com/repos/%s/contents/datos.json" % repo
    sha = None
    try:
        sha = gh("GET", api, token)["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    payload = {"message": "Copia de Juntadas " + time.strftime("%Y-%m-%d %H:%M"),
               "content": base64.b64encode(raw).decode()}
    if sha:
        payload["sha"] = sha
    gh("PUT", api, token, payload)


def loop(path, lock, state):
    last = 0
    time.sleep(120)
    while True:
        conf = read_conf()
        if conf.get("BACKUP_REPO") and conf.get("GITHUB_TOKEN") and os.path.exists(path):
            if state.get("dirty") and time.time() - last >= EVERY:
                try:
                    push(path, lock, conf)
                    last = time.time()
                    state["dirty"] = False
                    print("Copia subida a", conf["BACKUP_REPO"], flush=True)
                except Exception as e:
                    print("No pude subir la copia a GitHub (%s). Reintento más tarde." % e, flush=True)
        time.sleep(CHECK)


def start(path, lock, state):
    state.setdefault("dirty", True)  # al arrancar, sube una copia del estado actual
    state["dirty"] = True
    threading.Thread(target=loop, args=(path, lock, state), daemon=True).start()
