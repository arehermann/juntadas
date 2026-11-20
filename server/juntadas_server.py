#!/usr/bin/env python3
"""Servidor de Juntadas para la Raspberry Pi.

Solo usa Python 3 (sin instalar nada). Guarda todo en un archivo JSON.
Uso:  python3 juntadas_server.py  [--port 8787] [--data ~/juntadas-datos/datos.json]
"""
import argparse
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
COLORS = ["#E3B76B", "#D99AAE", "#8FC4A4", "#B3A1DB", "#8FB8DE", "#E0A383",
          "#C9B37E", "#9FC9C9", "#C4A0C4", "#A9B98A", "#DDA0A0", "#94A8D6"]

LOCK = threading.Lock()
DB = {"groups": {}}
DATA_PATH = ""


def new_id(n=10):
    return secrets.token_urlsafe(n)[:n].replace("-", "x").replace("_", "y")


def load():
    global DB
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, encoding="utf-8") as f:
            DB = json.load(f)
    DB.setdefault("groups", {})


def save():
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False)
    os.replace(tmp, DATA_PATH)


def backup_daily():
    """Guarda una copia por día (las últimas 14) junto al archivo de datos."""
    folder = os.path.join(os.path.dirname(DATA_PATH), "copias")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "datos-%s.json" % date.today().isoformat())
    if os.path.exists(DATA_PATH) and not os.path.exists(dest):
        shutil.copy(DATA_PATH, dest)
        old = sorted(os.listdir(folder))[:-14]
        for name in old:
            os.remove(os.path.join(folder, name))


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


def text(v, field, maxlen=80, required=True):
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ApiError(400, "%s inválido" % field)
    v = " ".join(v.split())[:maxlen]
    if required and not v:
        raise ApiError(400, "Falta %s" % field)
    return v


def iso_day(v, field):
    try:
        return date.fromisoformat(v).isoformat()
    except Exception:
        raise ApiError(400, "%s no es una fecha" % field)


SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")


def get_group(gid):
    g = DB["groups"].get(gid)
    if not g:
        raise ApiError(404, "No existe ese grupo")
    return g


def get_event(g, eid):
    for e in g["events"]:
        if e["id"] == eid:
            return e
    raise ApiError(404, "No existe esa juntada")


def member_ids(g):
    return {m["id"] for m in g["members"]}


def add_member(g, name):
    name = text(name, "el nombre", 30)
    for m in g["members"]:
        if m["name"].lower() == name.lower():
            return m
    if len(g["members"]) >= 40:
        raise ApiError(400, "El grupo está lleno")
    m = {"id": new_id(8), "name": name, "color": COLORS[len(g["members"]) % len(COLORS)]}
    g["members"].append(m)
    return m


def touch(g):
    g["updated"] = int(time.time() * 1000)


# ---------- rutas ----------

def route(method, parts, body):
    # parts: lista sin "api"
    if method == "GET" and parts == ["health"]:
        return 200, {"ok": True}

    if method == "POST" and parts == ["groups"]:
        if len(DB["groups"]) >= 500:
            raise ApiError(400, "Límite de grupos alcanzado")
        g = {"id": new_id(12), "name": text(body.get("name"), "el nombre del grupo", 40),
             "members": [], "events": [], "created": int(time.time() * 1000)}
        m = add_member(g, body.get("memberName"))
        touch(g)
        DB["groups"][g["id"]] = g
        return 201, {"group": g, "memberId": m["id"]}

    if len(parts) < 2 or parts[0] != "groups":
        raise ApiError(404, "Ruta desconocida")
    g = get_group(parts[1])
    rest = parts[2:]

    if method == "GET" and rest == []:
        return 200, {"group": g}

    if method == "PATCH" and rest == []:
        g["name"] = text(body.get("name"), "el nombre del grupo", 40)
        touch(g)
        return 200, {"group": g}

    if method == "POST" and rest == ["members"]:
        m = add_member(g, body.get("name"))
        touch(g)
        return 201, {"group": g, "memberId": m["id"]}

    if method == "POST" and rest == ["events"]:
        start = iso_day(body.get("start"), "Desde")
        end = iso_day(body.get("end"), "Hasta")
        if end < start:
            start, end = end, start
        if (date.fromisoformat(end) - date.fromisoformat(start)).days > 30:
            raise ApiError(400, "Elegí un rango de hasta 31 días")
        hf, ht = int(body.get("hourFrom", 10)), int(body.get("hourTo", 24))
        if not (0 <= hf < ht <= 24):
            raise ApiError(400, "Horario inválido")
        if len(g["events"]) >= 200:
            raise ApiError(400, "Demasiadas juntadas en este grupo")
        e = {"id": new_id(8), "title": text(body.get("title"), "el nombre de la juntada", 60),
             "place": text(body.get("place"), "el lugar", 80, required=False),
             "start": start, "end": end, "hourFrom": hf, "hourTo": ht,
             "createdBy": body.get("createdBy") if body.get("createdBy") in member_ids(g) else None,
             "avail": {}, "confirmed": None, "expenses": [], "created": int(time.time() * 1000)}
        g["events"].insert(0, e)
        touch(g)
        return 201, {"group": g, "eventId": e["id"]}

    if len(rest) >= 2 and rest[0] == "events":
        e = get_event(g, rest[1])
        sub = rest[2:]

        if method == "PATCH" and sub == []:
            if "title" in body:
                e["title"] = text(body.get("title"), "el nombre de la juntada", 60)
            if "place" in body:
                e["place"] = text(body.get("place"), "el lugar", 80, required=False)
            if "confirmed" in body:
                c = body["confirmed"]
                if c is None:
                    e["confirmed"] = None
                else:
                    day = iso_day(c.get("date"), "La fecha")
                    f, t = int(c.get("from")), int(c.get("to"))
                    if not (0 <= f < t <= 24):
                        raise ApiError(400, "Horario inválido")
                    e["confirmed"] = {"date": day, "from": f, "to": t}
            touch(g)
            return 200, {"group": g}

        if method == "DELETE" and sub == []:
            g["events"] = [x for x in g["events"] if x["id"] != e["id"]]
            touch(g)
            return 200, {"group": g}

        if method == "PUT" and len(sub) == 2 and sub[0] == "avail":
            mid = sub[1]
            if mid not in member_ids(g):
                raise ApiError(404, "No existe esa persona")
            slots = body.get("slots")
            if not isinstance(slots, list) or len(slots) > 31 * 24:
                raise ApiError(400, "Horarios inválidos")
            clean = sorted({s for s in slots if isinstance(s, str) and SLOT_RE.match(s)
                            and e["start"] <= s[:10] <= e["end"]})
            e["avail"][mid] = clean
            touch(g)
            return 200, {"group": g}

        if method == "POST" and sub == ["expenses"]:
            ids = member_ids(g)
            try:
                amount = round(float(str(body.get("amount")).replace(",", ".")), 2)
            except Exception:
                raise ApiError(400, "Monto inválido")
            if not (0 < amount < 1e10):
                raise ApiError(400, "Monto inválido")
            payer = body.get("payer")
            among = [m for m in (body.get("among") or []) if m in ids]
            if payer not in ids or not among:
                raise ApiError(400, "Elegí quién pagó y entre quiénes se divide")
            if len(e["expenses"]) >= 300:
                raise ApiError(400, "Demasiados gastos")
            x = {"id": new_id(8), "desc": text(body.get("desc"), "qué se compró", 60),
                 "amount": amount, "payer": payer, "among": sorted(set(among), key=among.index),
                 "ts": int(time.time() * 1000)}
            e["expenses"].append(x)
            touch(g)
            return 201, {"group": g}

        if method == "DELETE" and len(sub) == 2 and sub[0] == "expenses":
            e["expenses"] = [x for x in e["expenses"] if x["id"] != sub[1]]
            touch(g)
            return 200, {"group": g}

    raise ApiError(404, "Ruta desconocida")


class Handler(BaseHTTPRequestHandler):
    server_version = "Juntadas/1"

    def log_message(self, fmt, *args):
        print("%s %s" % (datetime.now().strftime("%H:%M:%S"), fmt % args), flush=True)

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def reply(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def handle_any(self):
        path = self.path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]
        if path in ("/", ""):
            return self.reply(200, {"ok": True, "app": "juntadas"})
        if not parts or parts[0] != "api":
            return self.reply(404, {"error": "Ruta desconocida"})
        body = {}
        if self.command in ("POST", "PUT", "PATCH"):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self.reply(413, {"error": "Demasiados datos"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except Exception:
                return self.reply(400, {"error": "JSON inválido"})
        try:
            with LOCK:
                status, out = route(self.command, parts[1:], body)
                if self.command != "GET":
                    save()
                    backup_daily()
            self.reply(status, out)
        except ApiError as err:
            self.reply(err.status, {"error": err.msg})
        except (ValueError, TypeError):
            self.reply(400, {"error": "Datos inválidos"})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any


def main():
    global DATA_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("JUNTADAS_PORT", 8787)))
    ap.add_argument("--data", default=os.environ.get(
        "JUNTADAS_DATA", os.path.expanduser("~/juntadas-datos/datos.json")))
    a = ap.parse_args()
    DATA_PATH = os.path.abspath(os.path.expanduser(a.data))
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    load()
    print("Juntadas escuchando en http://127.0.0.1:%d  (datos: %s)" % (a.port, DATA_PATH), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
