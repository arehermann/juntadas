#!/usr/bin/env python3
"""Servidor de Juntadas para la Raspberry Pi (versión 2: acceso por invitación).

Solo usa Python 3 (sin instalar nada). Guarda todo en un archivo JSON.
Uso:  python3 juntadas_server.py  [--port 8787] [--data ~/juntadas-datos/datos.json]

Seguridad:
- Cada persona tiene una llave personal secreta (token). El servidor guarda solo su hash.
- Cada pedido (salvo crear la identidad y ver una invitación) lleva la llave en
  "Authorization: Bearer <llave>", y el servidor revisa que la persona sea parte de la juntada.
- No hay listados globales. Los ids y códigos de invitación son largos y aleatorios.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
LOCK = threading.Lock()
DB = {}
DATA_PATH = ""
NEW_PEOPLE = defaultdict(deque)  # ip -> tiempos de identidades creadas (límite anti-abuso)


def now_ms():
    return int(time.time() * 1000)


def new_id(n):
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def h(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- archivo de datos ----------

def empty_db():
    return {"version": 2, "people": {}, "tokens": {}, "events": {}}


def load():
    global DB
    DB = empty_db()
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == 2:
            DB = data
        else:  # datos de la versión de prueba anterior: se guardan aparte
            old = DATA_PATH.replace(".json", "-v1-viejo.json")
            shutil.copy(DATA_PATH, old)
            print("Datos de la versión anterior guardados en", old, flush=True)
    for k, v in empty_db().items():
        DB.setdefault(k, v)
    for e in DB["events"].values():  # juntadas creadas antes de poder sacar días
        e.setdefault("days", day_range(e["start"], e["end"]))


def save():
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False)
    os.replace(tmp, DATA_PATH)
    DB_STATE["dirty"] = True


DB_STATE = {"dirty": False}


def backup_daily():
    """Copia local por día (las últimas 14) junto al archivo de datos."""
    folder = os.path.join(os.path.dirname(DATA_PATH), "copias")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "datos-%s.json" % date.today().isoformat())
    if os.path.exists(DATA_PATH) and not os.path.exists(dest):
        shutil.copy(DATA_PATH, dest)
        for name in sorted(os.listdir(folder))[:-14]:
            os.remove(os.path.join(folder, name))


# ---------- validaciones ----------

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


def hour(v, lo, hi):
    try:
        v = int(v)
    except Exception:
        raise ApiError(400, "Horario inválido")
    if not lo <= v <= hi:
        raise ApiError(400, "Horario inválido")
    return v


SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")


def day_range(start, end):
    d, out = date.fromisoformat(start), []
    while d.isoformat() <= end and len(out) < 32:
        out.append(d.isoformat())
        d = date.fromordinal(d.toordinal() + 1)
    return out


def schedule(body, e=None):
    """Días y horario de una juntada: una lista de días elegidos (o un rango desde/hasta) y horas."""
    if body.get("days") is not None:
        raw = body.get("days")
        if not isinstance(raw, list) or len(raw) > 31:
            raise ApiError(400, "Elegí hasta 31 días")
        days = sorted({iso_day(d, "Un día") for d in raw})
    elif body.get("start") is not None or e is None:
        start, end = iso_day(body.get("start"), "Desde"), iso_day(body.get("end"), "Hasta")
        if end < start:
            start, end = end, start
        days = day_range(start, end)
    else:
        days = list(e["days"])
    if not days:
        raise ApiError(400, "Dejá al menos un día")
    if (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days > 30:
        raise ApiError(400, "Los días tienen que estar dentro de un rango de 31 días")
    hf = hour(body.get("hourFrom", e["hourFrom"] if e else 10), 0, 23)
    ht = hour(body.get("hourTo", e["hourTo"] if e else 24), 1, 24)
    if hf >= ht:
        raise ApiError(400, "El horario \"hasta\" tiene que ser después del \"desde\"")
    return days, hf, ht


def slot_ok(e, s):
    return isinstance(s, str) and SLOT_RE.match(s) and s[:10] in e["days"] and e["hourFrom"] <= int(s[11:]) < e["hourTo"]


# ---------- lógica ----------

def person_view(pid):
    p = DB["people"].get(pid)
    return {"id": pid, "name": p["name"] if p else "?"}


def balances(e):
    b = {m: 0.0 for m in e["members"]}
    for x in e["expenses"]:
        b[x["payer"]] = b.get(x["payer"], 0) + x["amount"]
        share = x["amount"] / len(x["among"])
        for m in x["among"]:
            b[m] = b.get(m, 0) - share
    return b


def event_view(e, pid):
    v = {k: e[k] for k in ("id", "title", "place", "start", "end", "days", "hourFrom", "hourTo",
                           "adminId", "avail", "confirmed", "expenses", "created", "updated")}
    v["members"] = [person_view(m) for m in e["members"]]
    gone = {x["payer"] for x in e["expenses"]} | {m for x in e["expenses"] for m in x["among"]}
    v["former"] = [person_view(m) for m in gone if m not in e["members"]]
    if pid == e["adminId"]:
        v["invite"] = e["invite"]
    return v


def my_events(pid):
    evs = [e for e in DB["events"].values() if pid in e["members"]]
    evs.sort(key=lambda e: e["created"], reverse=True)
    return [event_view(e, pid) for e in evs]


def me_payload(pid):
    p = DB["people"][pid]
    return {"me": {"id": pid, "name": p["name"]}, "events": my_events(pid)}


def get_event_for(eid, pid):
    e = DB["events"].get(eid)
    if not e or pid not in e["members"]:
        raise ApiError(404, "No encontramos esa juntada")
    return e


def require_admin(e, pid):
    if e["adminId"] != pid:
        raise ApiError(403, "Solo quien organiza puede hacer esto")


def touch(e):
    e["updated"] = now_ms()


def check_no_debt(e, pid, who):
    bal = balances(e).get(pid, 0)
    if abs(bal) >= 0.5:
        quien = "Tenés" if who is None else who + " tiene"
        raise ApiError(409, "%s cuentas pendientes en esta juntada. Primero hay que saldarlas (en Gastos, \"Registrar pago\")." % quien)


def create_person(name, ip):
    q = NEW_PEOPLE[ip]
    t = time.time()
    while q and t - q[0] > 3600:
        q.popleft()
    if len(q) >= 30 or len(DB["people"]) >= 5000:
        raise ApiError(429, "Demasiados ingresos nuevos. Probá más tarde.")
    q.append(t)
    pid = new_id(12)
    token = new_id(32)
    DB["people"][pid] = {"id": pid, "name": text(name, "tu nombre", 30), "tokenHash": h(token), "created": now_ms()}
    DB["tokens"][h(token)] = pid
    return pid, token


def route(method, parts, body, pid, ip):
    # Sin llave
    if method == "GET" and parts == ["health"]:
        return 200, {"ok": True}

    if method == "POST" and parts == ["me"]:
        new_pid, token = create_person(body.get("name"), ip)
        out = me_payload(new_pid)
        out["token"] = token
        return 201, out

    if method == "GET" and len(parts) == 3 and parts[0] == "invite":
        e = DB["events"].get(parts[1])
        if not e or not hmac.compare_digest(e["invite"], parts[2]):
            raise ApiError(404, "La invitación no es válida o ya se cambió. Pedile a quien organiza un link nuevo.")
        return 200, {"event": {"id": e["id"], "title": e["title"], "place": e["place"], "start": e["start"],
                               "end": e["end"], "days": e["days"], "admin": person_view(e["adminId"])["name"],
                               "count": len(e["members"]), "already": pid in e["members"] if pid else False}}

    # Con llave
    if not pid:
        raise ApiError(401, "Falta tu llave personal. Entrá desde tu link de invitación o tu link personal.")

    if parts == ["me"]:
        if method == "GET":
            return 200, me_payload(pid)
        if method == "PATCH":
            DB["people"][pid]["name"] = text(body.get("name"), "tu nombre", 30)
            for e in DB["events"].values():
                if pid in e["members"]:
                    touch(e)
            return 200, me_payload(pid)

    if method == "POST" and parts == ["join"]:
        e = DB["events"].get(body.get("eventId") or "")
        if not e or not hmac.compare_digest(e["invite"], str(body.get("code") or "")):
            raise ApiError(404, "La invitación no es válida o ya se cambió. Pedile a quien organiza un link nuevo.")
        if pid not in e["members"]:
            if len(e["members"]) >= 40:
                raise ApiError(400, "La juntada está llena")
            e["members"].append(pid)
            touch(e)
        out = me_payload(pid)
        out["eventId"] = e["id"]
        return 200, out

    if method == "POST" and parts == ["events"]:
        days, hf, ht = schedule(body)
        if sum(1 for e in DB["events"].values() if e["adminId"] == pid) >= 200:
            raise ApiError(400, "Demasiadas juntadas")
        e = {"id": new_id(12), "invite": new_id(16), "adminId": pid, "members": [pid],
             "title": text(body.get("title"), "el nombre de la juntada", 60),
             "place": text(body.get("place"), "el lugar", 80, required=False),
             "start": days[0], "end": days[-1], "days": days, "hourFrom": hf, "hourTo": ht,
             "avail": {}, "confirmed": None, "expenses": [], "created": now_ms()}
        touch(e)
        DB["events"][e["id"]] = e
        out = me_payload(pid)
        out["eventId"] = e["id"]
        return 201, out

    if len(parts) >= 2 and parts[0] == "events":
        e = get_event_for(parts[1], pid)
        sub = parts[2:]

        if method == "PATCH" and sub == []:
            require_admin(e, pid)
            if any(k in body for k in ("days", "start", "end", "hourFrom", "hourTo")):
                days, hf, ht = schedule(body, e)
                c = e["confirmed"]
                if c and (c["date"] not in days or c["from"] < hf or c["to"] > ht):
                    raise ApiError(409, "La fecha confirmada queda afuera. Tocá \"Volver a votar\" antes de sacar ese día u horario.")
                title = text(body.get("title"), "el nombre de la juntada", 60) if "title" in body else e["title"]
                e.update({"days": days, "start": days[0], "end": days[-1], "hourFrom": hf, "hourTo": ht})
                for m in list(e["avail"]):
                    e["avail"][m] = [s_ for s_ in e["avail"][m] if slot_ok(e, s_)]
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
                    if day not in e["days"]:
                        raise ApiError(400, "Ese día no está entre los días de la juntada")
                    f, t = hour(c.get("from"), 0, 23), hour(c.get("to"), 1, 24)
                    if f >= t:
                        raise ApiError(400, "El horario \"hasta\" tiene que ser después del \"desde\"")
                    e["confirmed"] = {"date": day, "from": f, "to": t}
            touch(e)
            return 200, me_payload(pid)

        if method == "DELETE" and sub == []:
            require_admin(e, pid)
            if any(abs(v) >= 0.5 for v in balances(e).values()):
                raise ApiError(409, "Hay cuentas pendientes en esta juntada. Primero hay que saldarlas.")
            del DB["events"][e["id"]]
            return 200, me_payload(pid)

        if method == "POST" and sub == ["invite"]:
            require_admin(e, pid)
            e["invite"] = new_id(16)
            touch(e)
            return 200, me_payload(pid)

        if method == "POST" and sub == ["members"]:
            # quien organiza suma a alguien con quien ya comparte otra juntada
            require_admin(e, pid)
            other = body.get("personId")
            shares = any(pid in x["members"] and other in x["members"] for x in DB["events"].values() if x is not e)
            if other not in DB["people"] or not shares:
                raise ApiError(404, "No encontramos a esa persona")
            if other not in e["members"]:
                if len(e["members"]) >= 40:
                    raise ApiError(400, "La juntada está llena")
                e["members"].append(other)
                touch(e)
            return 200, me_payload(pid)

        if method == "DELETE" and len(sub) == 2 and sub[0] == "members":
            target = sub[1]
            if target not in e["members"]:
                raise ApiError(404, "No encontramos a esa persona")
            if target == e["adminId"]:
                raise ApiError(400, "Quien organiza no puede salir. Si ya no hace falta, borrá la juntada.")
            if target != pid:
                require_admin(e, pid)
            name = None if target == pid else DB["people"][target]["name"]
            check_no_debt(e, target, name)
            e["members"].remove(target)
            e["avail"].pop(target, None)
            touch(e)
            return 200, me_payload(pid)

        if method == "PUT" and sub == ["avail"]:
            slots = body.get("slots")
            if not isinstance(slots, list) or len(slots) > 31 * 24:
                raise ApiError(400, "Horarios inválidos")
            e["avail"][pid] = sorted({s for s in slots if slot_ok(e, s)})
            touch(e)
            return 200, me_payload(pid)

        if method == "POST" and sub == ["expenses"]:
            try:
                amount = round(float(str(body.get("amount")).replace(",", ".")), 2)
            except Exception:
                raise ApiError(400, "Monto inválido")
            if not (0 < amount < 1e10):
                raise ApiError(400, "Monto inválido")
            payer = body.get("payer")
            among = body.get("among")
            if among is None:  # por defecto, entre todas
                among = list(e["members"])
            among = [m for m in dict.fromkeys(among) if m in e["members"]]
            if payer not in e["members"] or not among:
                raise ApiError(400, "Elegí quién pagó y entre quiénes se divide")
            if len(e["expenses"]) >= 500:
                raise ApiError(400, "Demasiados gastos")
            kind = "pago" if body.get("kind") == "pago" else "gasto"
            if kind == "pago" and (len(among) != 1 or among[0] == payer):
                raise ApiError(400, "Pago inválido")
            e["expenses"].append({"id": new_id(10), "kind": kind,
                                  "desc": text(body.get("desc"), "qué se compró", 60) if kind == "gasto" else "Pago",
                                  "amount": amount, "payer": payer, "among": among,
                                  "by": pid, "ts": now_ms()})
            touch(e)
            return 201, me_payload(pid)

        if method == "DELETE" and len(sub) == 2 and sub[0] == "expenses":
            x = next((x for x in e["expenses"] if x["id"] == sub[1]), None)
            if not x:
                raise ApiError(404, "No encontramos ese gasto")
            if pid not in (x.get("by"), e["adminId"]):
                raise ApiError(403, "Solo quien lo cargó o quien organiza puede borrarlo")
            e["expenses"].remove(x)
            touch(e)
            return 200, me_payload(pid)

    raise ApiError(404, "Ruta desconocida")


class Handler(BaseHTTPRequestHandler):
    server_version = "Juntadas/2"

    def log_message(self, fmt, *args):
        # no se registran llaves ni datos: solo método, ruta sin ids y estado
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
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
        print("%s %s %s %d" % (datetime.now().strftime("%H:%M:%S"), self.command,
                               re.sub(r"/[A-Za-z0-9]{8,}", "/…", self.path.split("?")[0]), status), flush=True)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def handle_any(self):
        path = self.path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]
        if not parts:
            return self.reply(200, {"ok": True, "app": "juntadas", "version": 2})
        if parts[0] != "api":
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
        auth = self.headers.get("Authorization") or ""
        ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
        try:
            with LOCK:
                pid = None
                if auth.startswith("Bearer "):
                    pid = DB["tokens"].get(h(auth[7:].strip()))
                    if not pid:
                        raise ApiError(401, "Tu llave personal no es válida. Entrá de nuevo con tu link personal.")
                status, out = route(self.command, parts[1:], body, pid, ip)
                if self.command != "GET":
                    save()
                    backup_daily()
            self.reply(status, out)
        except ApiError as err:
            self.reply(err.status, {"error": err.msg})
        except (ValueError, TypeError, KeyError, AttributeError):
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
    try:
        import respaldo  # copia diaria a un repo privado de GitHub (opcional)
        respaldo.start(DATA_PATH, LOCK, DB_STATE)
    except ImportError:
        pass
    print("Juntadas escuchando en http://127.0.0.1:%d  (datos: %s)" % (a.port, DATA_PATH), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
