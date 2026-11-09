"""El 21 (estilo "Banned Footage" de RE7): duelo 1v1 entre usuarios, por salas con codigo.

Reglas:
  * Baraja del 1 al 11 (sin repetir). Cada ronda se baraja una baraja nueva.
  * Cada jugador recibe 2 cartas: la PRIMERA va boca abajo (solo la ve su dueno), la otra boca arriba.
  * Por turnos: "pedir" (una carta boca arriba) o "plantarse". Si alguien se pasa de 21 pierde la ronda
    al instante. Si un jugador se planta y el otro tambien en su turno siguiente, se destapa y gana
    quien este mas cerca de 21 (igual = ronda empatada).
  * Mejor de 3 rondas: gana quien llegue a 2 victorias, o quien tenga mas tras 3 rondas. Si igualan,
    cada uno recupera su apuesta.
  * Cada jugador tiene TURNO_SEG segundos por turno; si se pasa, se planta solo. Con FALTAS_MAX turnos
    seguidos sin jugar pierde la partida. Tambien se puede abandonar.

Dinero (misma regla de oro que economia.py): todo movimiento pasa por economia._aplicar(), dentro de
UNA transaccion, con una fila en 'movimientos' (tipo "21").
  * La apuesta se RETIENE cuando entra el rival (no al crear la sala), a los dos a la vez.
  * Al terminar, el ganador recibe el bote (2 x apuesta); en empate se devuelve a cada uno.
  * Los cobros/devoluciones saltan el tope MAX_DISPONIBLES: es plata ya apostada, no puede fallar.
  * Los pedidos son SEMANALES: la apuesta se descuenta de los de esa semana. Si la partida termina ya
    en otra semana (cruzo el lunes 00:00), no se paga nada: esos pedidos vencieron con su semana, y
    pagarlos en la nueva seria pasar pedidos de una semana a otra.

Estado: todo vive en SQLite (tabla v21_partidas). El servidor decide todo; el navegador solo pide
acciones. Los plazos se resuelven "perezosamente" (al consultar/actuar o con barrer()), asi no hace
falta ningun proceso en segundo plano para que una partida abandonada se cierre.
"""
import json
import secrets
import sqlite3
import time
from contextlib import closing

from utils import limites
from utils.economia import JuegoError, _aplicar, _env_int

# --- Reglas configurables (variables de entorno en Fly) ---
APUESTA_MAX_21 = _env_int("V21_APUESTA_MAX", 0)   # maximo por partida (0 = sin tope, solo el saldo)
TURNO_SEG = _env_int("V21_TURNO_SEG", 60)         # segundos por turno
SALA_ESPERA_SEG = _env_int("V21_SALA_SEG", 600)   # una sala sin rival se borra a los 10 min
FALTAS_MAX = _env_int("V21_FALTAS_MAX", 3)        # turnos seguidos sin jugar antes de perder

OBJETIVO = 21
RONDAS = 3
VICTORIAS = 2
PAUSA_RONDA_SEG = 5   # tiempo extra al empezar una ronda, para que se vea el resultado de la anterior

# Sin 0/O/1/I/L para que no se confundan al dictarlo.
ALFABETO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LARGO_CODIGO = 4

ACTIVAS = ("espera", "jugando")


def _ahora() -> int:
    return int(time.time())


# ---------- tabla ----------

def _init():
    with closing(limites._conectar()) as c, c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS v21_partidas ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT NOT NULL, "
            "estado TEXT NOT NULL, creador INTEGER NOT NULL, rival INTEGER, "
            "apuesta INTEGER NOT NULL, creada INTEGER NOT NULL, actualizada INTEGER NOT NULL, "
            "datos TEXT NOT NULL DEFAULT '{}')"
        )
        c.execute("CREATE INDEX IF NOT EXISTS ix_v21_codigo ON v21_partidas (codigo, id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_v21_estado ON v21_partidas (estado)")


_init()


# ---------- utilidades ----------

def _barajar() -> list[int]:
    """Baraja del 1 al 11, mezclada con `secrets` (no predecible). Se saca de atras con pop()."""
    mazo = list(range(1, 12))
    for i in range(len(mazo) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        mazo[i], mazo[j] = mazo[j], mazo[i]
    return mazo


def normalizar_codigo(codigo: str) -> str:
    return "".join(str(codigo).split()).upper()


def _codigo_nuevo(c: sqlite3.Connection) -> str:
    for _ in range(50):
        codigo = "".join(secrets.choice(ALFABETO) for _ in range(LARGO_CODIGO))
        usado = c.execute(
            "SELECT 1 FROM v21_partidas WHERE codigo = ? AND estado IN ('espera', 'jugando')", (codigo,)
        ).fetchone()
        if not usado:
            return codigo
    raise JuegoError("sin_codigos")  # solo si hay una cantidad absurda de salas abiertas


_COLS = "id, codigo, estado, creador, rival, apuesta, creada, actualizada, datos"


def _fila_a_partida(f) -> dict:
    return {
        "id": f[0], "codigo": f[1], "estado": f[2], "creador": f[3], "rival": f[4],
        "apuesta": f[5], "creada": f[6], "actualizada": f[7], "datos": json.loads(f[8] or "{}"),
    }


def _cargar(c: sqlite3.Connection, codigo: str) -> dict | None:
    """La partida MAS RECIENTE con ese codigo (un codigo se puede reutilizar cuando la anterior termino)."""
    f = c.execute(
        f"SELECT {_COLS} FROM v21_partidas WHERE codigo = ? ORDER BY id DESC LIMIT 1", (codigo,)
    ).fetchone()
    return _fila_a_partida(f) if f else None


def _guardar(c: sqlite3.Connection, p: dict, ahora: int) -> None:
    p["actualizada"] = ahora
    c.execute(
        "UPDATE v21_partidas SET estado = ?, rival = ?, actualizada = ?, datos = ? WHERE id = ?",
        (p["estado"], p["rival"], ahora, json.dumps(p["datos"], separators=(",", ":")), p["id"]),
    )


def _otro(p: dict, uid: int) -> int:
    return p["rival"] if uid == p["creador"] else p["creador"]


def _total(mano: list[int]) -> int:
    return sum(mano)


def _activa_de(c: sqlite3.Connection, uid: int) -> str | None:
    f = c.execute(
        "SELECT codigo FROM v21_partidas WHERE (creador = ? OR rival = ?) "
        "AND estado IN ('espera', 'jugando') ORDER BY id DESC LIMIT 1", (uid, uid),
    ).fetchone()
    return f[0] if f else None


# ---------- logica de la partida (opera sobre el dict `p`, dentro de una transaccion) ----------

def _repartir(p: dict, primero: int, t: int) -> None:
    """Empieza una ronda: baraja nueva, 2 cartas a cada uno (la primera, boca abajo)."""
    d = p["datos"]
    mazo = _barajar()
    d["manos"] = {str(p["creador"]): [mazo.pop(), mazo.pop()], str(p["rival"]): [mazo.pop(), mazo.pop()]}
    d["mazo"] = mazo
    d["primero"] = primero
    d["turno"] = primero
    d["turno_hasta"] = t + TURNO_SEG
    d["ultimo_paso"] = False


def _terminar(c: sqlite3.Connection, p: dict, ganador: int | None, motivo: str) -> None:
    """Cierra la partida y paga: bote al ganador, o devolucion a cada uno si no hay ganador."""
    d = p["datos"]
    p["estado"] = "terminada"
    d["resultado"] = {"ganador": ganador, "motivo": motivo}
    a, b, apuesta, cod = p["creador"], p["rival"], p["apuesta"], p["codigo"]
    if d.get("semana") != limites._semana():
        d["resultado"]["vencida"] = True   # la semana de la apuesta ya paso: no se mueve nada
        return
    if ganador is None:
        _aplicar(c, a, apuesta, "21", f"empate {cod}: devolucion", admin=True)
        _aplicar(c, b, apuesta, "21", f"empate {cod}: devolucion", admin=True)
    else:
        perdedor = b if ganador == a else a
        _aplicar(c, ganador, 2 * apuesta, "21", f"gana {cod} vs {perdedor} ({motivo})", admin=True)


def _cerrar_ronda(c: sqlite3.Connection, p: dict, t: int) -> None:
    d = p["datos"]
    a, b = p["creador"], p["rival"]
    ta, tb = _total(d["manos"][str(a)]), _total(d["manos"][str(b)])
    if ta > OBJETIVO:
        ganador, motivo = b, "pasado"
    elif tb > OBJETIVO:
        ganador, motivo = a, "pasado"
    elif ta > tb:
        ganador, motivo = a, "puntos"
    elif tb > ta:
        ganador, motivo = b, "puntos"
    else:
        ganador, motivo = None, "empate"

    if ganador is not None:
        d["victorias"][str(ganador)] += 1
    d["ultima_ronda"] = {
        "ronda": d["ronda"], "ganador": ganador, "motivo": motivo,
        "manos": {str(a): list(d["manos"][str(a)]), str(b): list(d["manos"][str(b)])},
    }

    va, vb = d["victorias"][str(a)], d["victorias"][str(b)]
    if va >= VICTORIAS or vb >= VICTORIAS or d["ronda"] >= RONDAS:
        final = a if va > vb else (b if vb > va else None)
        _terminar(c, p, final, "rondas")
        return
    d["ronda"] += 1
    _repartir(p, _otro(p, d["primero"]), t + PAUSA_RONDA_SEG)


def _ejecutar(c: sqlite3.Connection, p: dict, uid: int, accion: str, t: int, auto: bool = False) -> None:
    """Un turno: 'pedir' o 'plantarse'. `t` es la hora de referencia para el siguiente plazo."""
    d = p["datos"]
    if d["turno"] != uid:
        raise JuegoError("no_es_tu_turno")
    if not auto:
        d["faltas"][str(uid)] = 0

    if accion == "pedir":
        if not d["mazo"]:
            raise JuegoError("sin_cartas")
        mano = d["manos"][str(uid)]
        mano.append(d["mazo"].pop())
        d["ultimo_paso"] = False
        if _total(mano) > OBJETIVO:
            _cerrar_ronda(c, p, t)
            return
    elif accion == "plantarse":
        if d["ultimo_paso"]:  # el rival ya se habia plantado: se destapa
            _cerrar_ronda(c, p, t)
            return
        d["ultimo_paso"] = True
    else:
        raise JuegoError("accion")
    d["turno"] = _otro(p, uid)
    d["turno_hasta"] = t + TURNO_SEG


def _resolver_tiempo(c: sqlite3.Connection, p: dict, ahora: int) -> bool:
    """Aplica los plazos vencidos (planta solo; tras FALTAS_MAX seguidas, pierde). True si cambio algo."""
    cambio = False
    while p["estado"] == "jugando" and ahora >= p["datos"]["turno_hasta"]:
        d = p["datos"]
        uid = d["turno"]
        d["faltas"][str(uid)] += 1
        cambio = True
        if d["faltas"][str(uid)] >= FALTAS_MAX:
            _terminar(c, p, _otro(p, uid), "tiempo")
            break
        # Se juega "como si" hubiera vencido en su hora, no ahora: una partida abandonada se cierra igual.
        _ejecutar(c, p, uid, "plantarse", d["turno_hasta"], auto=True)
    return cambio


def _caducar_sala(p: dict, ahora: int) -> bool:
    if p["estado"] == "espera" and ahora >= p["creada"] + SALA_ESPERA_SEG:
        p["estado"] = "cancelada"
        return True
    return False


def _barrer(c: sqlite3.Connection, ahora: int) -> int:
    """Cierra salas sin rival vencidas y partidas abandonadas. Devuelve cuantas toco."""
    n = 0
    for (codigo,) in c.execute("SELECT codigo FROM v21_partidas WHERE estado IN ('espera', 'jugando')").fetchall():
        p = _cargar(c, codigo)
        if p is None or p["estado"] not in ACTIVAS:
            continue
        if _caducar_sala(p, ahora) or (p["estado"] == "jugando" and _resolver_tiempo(c, p, ahora)):
            _guardar(c, p, ahora)
            n += 1
    return n


# ---------- lo que ve cada jugador ----------

def _vista(p: dict, uid: int, ahora: int) -> dict:
    """Estado de la partida visto por `uid`. NUNCA incluye la baraja ni la carta oculta del rival."""
    rival = _otro(p, uid)
    v = {
        "codigo": p["codigo"], "estado": p["estado"], "apuesta": p["apuesta"],
        "yo": str(uid), "rival": str(rival) if rival else None,
    }
    if p["estado"] in ("espera", "cancelada"):
        if p["estado"] == "espera":
            v["vence_en"] = max(0, p["creada"] + SALA_ESPERA_SEG - ahora)
        return v

    d = p["datos"]
    mi, riv = d["manos"][str(uid)], d["manos"][str(rival)]
    v.update({
        "bote": 2 * p["apuesta"], "ronda": d["ronda"], "rondas": RONDAS, "victorias_para_ganar": VICTORIAS,
        "victorias": {"yo": d["victorias"][str(uid)], "rival": d["victorias"][str(rival)]},
        "mi_mano": list(mi), "mi_total": _total(mi),           # mi_mano[0] es MI carta boca abajo
        "rival_mano": list(riv[1:]), "rival_visible": _total(riv[1:]),
        "rival_tiene_oculta": True,                              # solo se sabe que existe
    })
    ur = d.get("ultima_ronda")
    if ur:
        def quien(g):
            return None if g is None else ("yo" if g == uid else "rival")
        v["ultima_ronda"] = {
            "ronda": ur["ronda"], "ganador": quien(ur["ganador"]), "motivo": ur["motivo"],
            "mi_mano": ur["manos"][str(uid)], "rival_mano": ur["manos"][str(rival)],
        }
    if p["estado"] == "jugando":
        mio = d["turno"] == uid
        v.update({
            "turno": "yo" if mio else "rival",
            "turno_restante": max(0, d["turno_hasta"] - ahora),
            "rival_planto": bool(d["ultimo_paso"] and mio),
            "puedo_pedir": mio and bool(d["mazo"]),
            "puedo_plantarme": mio,
        })
    else:
        r = d["resultado"]
        g = r["ganador"]
        v["resultado"] = {
            "ganador": "empate" if g is None else ("yo" if g == uid else "rival"),
            "motivo": r["motivo"],
            "delta": 0 if (g is None or r.get("vencida")) else (p["apuesta"] if g == uid else -p["apuesta"]),
            "vencida": bool(r.get("vencida")),   # cruzo el lunes: no se pago nada
        }
    return v


# ---------- API publica (la llama api.py) ----------

def crear_sala(uid: int, apuesta: int) -> dict:
    """Crea una sala en espera y devuelve su codigo. No se retira nada hasta que entre el rival."""
    if not isinstance(apuesta, int) or isinstance(apuesta, bool) or apuesta < 1 \
            or (APUESTA_MAX_21 and apuesta > APUESTA_MAX_21):
        raise JuegoError("apuesta_invalida", maximo=APUESTA_MAX_21)
    ahora = _ahora()
    with limites.transaccion() as c:
        _barrer(c, ahora)
        ya = _activa_de(c, uid)
        if ya:
            raise JuegoError("ya_en_partida", sala=ya)
        disp = limites._disponibles(c, uid)
        if disp < apuesta:
            raise JuegoError("saldo", disponibles=disp)
        codigo = _codigo_nuevo(c)
        c.execute(
            "INSERT INTO v21_partidas (codigo, estado, creador, rival, apuesta, creada, actualizada, datos) "
            "VALUES (?, 'espera', ?, NULL, ?, ?, ?, '{}')", (codigo, uid, apuesta, ahora, ahora),
        )
        return {"codigo": codigo, "apuesta": apuesta, "vence_en": SALA_ESPERA_SEG}


def unirse(uid: int, codigo: str) -> dict:
    """Entra a una sala por su codigo: retiene la apuesta a los dos y reparte la primera ronda."""
    codigo = normalizar_codigo(codigo)
    ahora = _ahora()
    cancelada_por_saldo = False
    with limites.transaccion() as c:
        _barrer(c, ahora)
        p = _cargar(c, codigo)
        if p is None or p["estado"] == "cancelada":
            raise JuegoError("sala_no_existe")
        if p["estado"] != "espera":
            raise JuegoError("sala_llena")
        if p["creador"] == uid:
            raise JuegoError("tu_sala")
        ya = _activa_de(c, uid)
        if ya:
            raise JuegoError("ya_en_partida", sala=ya)
        disp_rival = limites._disponibles(c, uid)
        if disp_rival < p["apuesta"]:
            raise JuegoError("saldo", disponibles=disp_rival, apuesta=p["apuesta"])
        if limites._disponibles(c, p["creador"]) < p["apuesta"]:
            # El creador gasto sus pedidos mientras esperaba: la sala ya no vale.
            p["estado"] = "cancelada"
            _guardar(c, p, ahora)
            cancelada_por_saldo = True
        else:
            _aplicar(c, p["creador"], -p["apuesta"], "21", f"apuesta {codigo} vs {uid}")
            _aplicar(c, uid, -p["apuesta"], "21", f"apuesta {codigo} vs {p['creador']}")
            p["rival"] = uid
            p["estado"] = "jugando"
            p["datos"] = {
                "ronda": 1, "semana": limites._semana(), "victorias": {str(p["creador"]): 0, str(uid): 0},
                "faltas": {str(p["creador"]): 0, str(uid): 0}, "ultima_ronda": None, "resultado": None,
            }
            _repartir(p, secrets.choice([p["creador"], uid]), ahora)
            _guardar(c, p, ahora)
            return _vista(p, uid, ahora)
    if cancelada_por_saldo:
        raise JuegoError("sala_invalida")


def estado(uid: int, codigo: str) -> dict:
    """Estado actual para `uid`. Solo abre transaccion de escritura si hay un plazo que resolver."""
    codigo = normalizar_codigo(codigo)
    ahora = _ahora()
    with closing(limites._conectar()) as c:
        p = _cargar(c, codigo)
    if p is None:
        raise JuegoError("sala_no_existe")
    if uid not in (p["creador"], p["rival"]):
        raise JuegoError("no_participas")

    d = p["datos"]
    vencida = (p["estado"] == "espera" and ahora >= p["creada"] + SALA_ESPERA_SEG) or \
              (p["estado"] == "jugando" and ahora >= d["turno_hasta"])
    if not vencida:
        return _vista(p, uid, ahora)

    with limites.transaccion() as c:
        p = _cargar(c, codigo)
        if _caducar_sala(p, ahora) or (p["estado"] == "jugando" and _resolver_tiempo(c, p, ahora)):
            _guardar(c, p, ahora)
        return _vista(p, uid, ahora)


def accion(uid: int, codigo: str, que: str) -> dict:
    """'pedir', 'plantarse' o 'abandonar'. Si el plazo ya vencio, primero se resuelve el plazo."""
    codigo = normalizar_codigo(codigo)
    if que not in ("pedir", "plantarse", "abandonar"):
        raise JuegoError("accion")
    ahora = _ahora()
    with limites.transaccion() as c:
        p = _cargar(c, codigo)
        if p is None:
            raise JuegoError("sala_no_existe")
        if uid not in (p["creador"], p["rival"]):
            raise JuegoError("no_participas")
        if p["estado"] != "jugando":
            return _vista(p, uid, ahora)
        _resolver_tiempo(c, p, ahora)
        if p["estado"] == "jugando":
            if que == "abandonar":
                _terminar(c, p, _otro(p, uid), "abandono")
            else:
                _ejecutar(c, p, uid, que, ahora)
        _guardar(c, p, ahora)
        return _vista(p, uid, ahora)


def cancelar(uid: int, codigo: str) -> dict:
    """El creador cierra su sala mientras nadie ha entrado."""
    codigo = normalizar_codigo(codigo)
    ahora = _ahora()
    with limites.transaccion() as c:
        p = _cargar(c, codigo)
        if p is None:
            raise JuegoError("sala_no_existe")
        if p["creador"] != uid:
            raise JuegoError("no_participas")
        if p["estado"] != "espera":
            raise JuegoError("sala_llena")
        p["estado"] = "cancelada"
        _guardar(c, p, ahora)
        return _vista(p, uid, ahora)


def sala_actual(uid: int) -> str | None:
    """Codigo de la partida/sala activa de la persona (para retomarla al recargar la web)."""
    ahora = _ahora()
    with limites.transaccion() as c:
        _barrer(c, ahora)
        return _activa_de(c, uid)


def barrer() -> int:
    """Para una tarea periodica del bot (por ejemplo cada minuto). Opcional: todo se resuelve igual al consultar."""
    with limites.transaccion() as c:
        return _barrer(c, _ahora())
