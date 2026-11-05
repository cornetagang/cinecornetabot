"""Apuestas con pedidos: ruleta contra la casa y duelos entre jugadores.

Reglas de oro:
  * Todo movimiento de saldo pasa por _aplicar(), dentro de UNA transaccion, y deja una fila
    en 'movimientos' (quien, cuanto, por que, saldo resultante). Asi se puede auditar todo.
  * El resultado se decide y se guarda en el mismo instante (no hay apuestas "a medias" que
    se puedan perder si el bot se reinicia). La animacion en Discord es solo decoracion posterior.
  * El azar sale de `secrets` (no predecible), siempre en el servidor.
"""
import os
import secrets
import sqlite3
import time
from contextlib import closing

from utils import limites


def _env_int(nombre: str, defecto: int) -> int:
    try:
        return int(os.environ.get(nombre, defecto))
    except ValueError:
        return defecto


# --- Reglas configurables (variables de entorno en Fly) ---
MAX_DISPONIBLES = _env_int("MAX_DISPONIBLES", 0)      # tope de pedidos disponibles por semana (0 = sin tope)
APUESTA_MAX = _env_int("APUESTA_MAX", 0)              # maximo por giro contra la casa (0 = sin tope, solo el saldo)
DUELO_MAX = _env_int("DUELO_MAX", 2)                  # maximo por duelo
DUELOS_RIVAL_DIA = _env_int("DUELOS_RIVAL_DIA", 2)    # duelos al dia entre la misma pareja
DUELOS_DIA = _env_int("DUELOS_DIA", 6)                # duelos al dia por persona

ROJOS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})

# apuesta -> (funcion que dice si gana, multiplicador de la ganancia neta)
# 1:1 -> apuestas 2 y, si ganas, recibes 4 (ganancia neta +2). Docenas pagan 2:1.
APUESTAS = {
    "rojo": (lambda n: n in ROJOS, 1),
    "negro": (lambda n: n != 0 and n not in ROJOS, 1),
    "par": (lambda n: n != 0 and n % 2 == 0, 1),
    "impar": (lambda n: n % 2 == 1, 1),
    "bajo": (lambda n: 1 <= n <= 18, 1),
    "alto": (lambda n: 19 <= n <= 36, 1),
    "docena1": (lambda n: 1 <= n <= 12, 2),
    "docena2": (lambda n: 13 <= n <= 24, 2),
    "docena3": (lambda n: 25 <= n <= 36, 2),
    # columnas: pagan 2:1 (col1 = 1,4,7...34 / col2 = 2,5,8...35 / col3 = 3,6,9...36)
    "col1": (lambda n: n != 0 and n % 3 == 1, 2),
    "col2": (lambda n: n != 0 and n % 3 == 2, 2),
    "col3": (lambda n: n != 0 and n % 3 == 0, 2),
}
# pleno: un numero exacto (0 a 36), paga 35:1. Se apuesta como "n0" ... "n36".
for _i in range(37):
    APUESTAS[f"n{_i}"] = ((lambda n, _i=_i: n == _i), 35)


class JuegoError(Exception):
    """Error esperado de una jugada: lleva un codigo para que Discord / la web armen el mensaje."""

    def __init__(self, codigo: str, **datos):
        super().__init__(codigo)
        self.codigo = codigo
        self.datos = datos


def girar() -> int:
    """Ruleta europea: 0 a 36, uniforme y no predecible."""
    return secrets.randbelow(37)


def color(n: int) -> str:
    return "verde" if n == 0 else ("rojo" if n in ROJOS else "negro")


# ---------- tablas ----------

def _init():
    with closing(limites._conectar()) as c, c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS movimientos ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, user_id INTEGER NOT NULL, delta INTEGER NOT NULL, "
            "tipo TEXT NOT NULL, detalle TEXT NOT NULL, saldo INTEGER NOT NULL)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS ix_mov_user ON movimientos (user_id, id)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS duelos ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, "
            "retador INTEGER NOT NULL, retado INTEGER NOT NULL, cantidad INTEGER NOT NULL, "
            "numero INTEGER NOT NULL, ganador INTEGER)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS ix_duelos_ts ON duelos (ts)")


_init()


# ---------- movimiento de saldo (unica puerta) ----------

def _aplicar(c: sqlite3.Connection, user_id: int, delta: int, tipo: str, detalle: str,
             admin: bool = False) -> int:
    """Suma/resta pedidos disponibles (dentro de una transaccion abierta). Devuelve el saldo nuevo.
    `admin=True` se salta el tope MAX_DISPONIBLES (el admin manda)."""
    semana = limites._semana()
    usados, ajuste = limites._fila(c, user_id, semana)
    nuevo = limites._limite(c) + ajuste - usados + delta
    if nuevo < 0:
        raise JuegoError("saldo", disponibles=max(0, nuevo - delta))
    if MAX_DISPONIBLES and not admin and delta > 0 and nuevo > MAX_DISPONIBLES:
        raise JuegoError("tope", maximo=MAX_DISPONIBLES, disponibles=nuevo - delta)
    c.execute(
        "INSERT INTO pedidos (user_id, semana, cantidad, ajuste) VALUES (?, ?, 0, ?) "
        "ON CONFLICT(user_id, semana) DO UPDATE SET ajuste = ajuste + excluded.ajuste",
        (user_id, semana, delta),
    )
    c.execute(
        "INSERT INTO movimientos (ts, user_id, delta, tipo, detalle, saldo) VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), user_id, delta, tipo, detalle[:200], nuevo),
    )
    return nuevo


def mover_admin(user_id: int, delta: int, motivo: str) -> int:
    """Ajuste manual de un admin sobre el 'ajuste' (queda en el registro)."""
    with limites.transaccion() as c:
        return _aplicar(c, user_id, delta, "admin", motivo, admin=True)


def _admin(c: sqlite3.Connection, user_id: int, d_usados: int, d_ajuste: int, detalle: str) -> int:
    """Mueve 'usados' y/o 'ajuste' a la vez y deja UNA fila en movimientos.
    delta registrado = cuanto cambian los disponibles. Devuelve el saldo resultante."""
    semana = limites._semana()
    c.execute(
        "INSERT INTO pedidos (user_id, semana, cantidad, ajuste) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, semana) DO UPDATE SET "
        "cantidad = cantidad + excluded.cantidad, ajuste = ajuste + excluded.ajuste",
        (user_id, semana, d_usados, d_ajuste),
    )
    saldo = limites._disponibles(c, user_id)
    c.execute(
        "INSERT INTO movimientos (ts, user_id, delta, tipo, detalle, saldo) VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), user_id, d_ajuste - d_usados, "admin", detalle[:200], saldo),
    )
    return saldo


def devolver_admin(user_id: int, cantidad: int) -> int:
    """Le devuelve `cantidad` pedidos: primero recupera los que gasto con /pedir y lo que sobre
    lo suma al ajuste (asi tambien repone los perdidos en apuestas). Devuelve el saldo nuevo."""
    with limites.transaccion() as c:
        usados, _ = limites._fila(c, user_id, limites._semana())
        de_usados = min(cantidad, usados)
        de_ajuste = cantidad - de_usados
        return _admin(c, user_id, -de_usados, de_ajuste, f"devolver {cantidad} (admin)")


def quitar_admin(user_id: int, cantidad: int) -> tuple[int, int]:
    """Le descuenta hasta `cantidad` pedidos (nunca bajo 0). Devuelve (quitados, saldo nuevo)."""
    with limites.transaccion() as c:
        disp = limites._disponibles(c, user_id)
        quitados = min(cantidad, disp)
        if quitados <= 0:
            return 0, disp
        return quitados, _admin(c, user_id, quitados, 0, f"quitar {quitados} (admin)")


def reset_admin(user_id: int) -> int:
    """Deja a la persona como al empezar la semana: usados 0 y ajuste 0 (borra tambien lo
    ganado/perdido en apuestas). Devuelve el saldo nuevo (= limite semanal)."""
    with limites.transaccion() as c:
        usados, ajuste = limites._fila(c, user_id, limites._semana())
        if usados == 0 and ajuste == 0:
            return limites._disponibles(c, user_id)
        return _admin(c, user_id, -usados, -ajuste, "reset (admin)")


# ---------- ruleta contra la casa ----------

def jugar_casa(user_id: int, apuesta: str, cantidad: int, _numero: int | None = None) -> dict:
    """Un giro contra la casa. `_numero` solo existe para las pruebas."""
    if apuesta not in APUESTAS:
        raise JuegoError("apuesta")
    if cantidad < 1 or (APUESTA_MAX and cantidad > APUESTA_MAX):
        raise JuegoError("cantidad", maximo=APUESTA_MAX)
    gana, multiplicador = APUESTAS[apuesta]

    with limites.transaccion() as c:
        disp = limites._disponibles(c, user_id)
        if disp < cantidad:
            raise JuegoError("saldo", disponibles=disp)
        # El premio nunca puede pasar el tope: se rechaza ANTES de girar (si se recortara
        # despues, se estaria cobrando una apuesta mas dificil de ganar de lo que se ofrecio).
        if MAX_DISPONIBLES and disp + cantidad * multiplicador > MAX_DISPONIBLES:
            raise JuegoError("tope", maximo=MAX_DISPONIBLES, disponibles=disp)

        numero = girar() if _numero is None else _numero
        gano = gana(numero)
        delta = cantidad * multiplicador if gano else -cantidad
        nuevo = _aplicar(c, user_id, delta, "casa", f"{apuesta} x{cantidad} -> {numero}")

    return {
        "numero": numero, "color": color(numero), "gano": gano, "apuesta": apuesta,
        "cantidad": cantidad, "delta": delta, "disponibles": nuevo,
    }


# ---------- duelos ----------

def _contar_duelos(c: sqlite3.Connection, desde: int, a: int, b: int | None = None) -> int:
    if b is None:  # duelos de una persona hoy
        return c.execute(
            "SELECT COUNT(*) FROM duelos WHERE ts >= ? AND (retador = ? OR retado = ?)", (desde, a, a)
        ).fetchone()[0]
    return c.execute(
        "SELECT COUNT(*) FROM duelos WHERE ts >= ? AND "
        "((retador = ? AND retado = ?) OR (retador = ? AND retado = ?))",
        (desde, a, b, b, a),
    ).fetchone()[0]


def _validar_duelo(c: sqlite3.Connection, retador: int, retado: int, cantidad: int) -> None:
    if retador == retado:
        raise JuegoError("a_ti_mismo")
    if not 1 <= cantidad <= DUELO_MAX:
        raise JuegoError("cantidad", maximo=DUELO_MAX)
    for uid in (retador, retado):
        disp = limites._disponibles(c, uid)
        if disp < cantidad:
            raise JuegoError("saldo_duelo", quien=uid, disponibles=disp)
        # Cualquiera de los dos puede ganar: a los dos les tiene que caber la ganancia.
        if MAX_DISPONIBLES and disp + cantidad > MAX_DISPONIBLES:
            raise JuegoError("tope_duelo", quien=uid, maximo=MAX_DISPONIBLES)
    desde = limites.inicio_dia()
    if _contar_duelos(c, desde, retador, retado) >= DUELOS_RIVAL_DIA:
        raise JuegoError("limite_rival", maximo=DUELOS_RIVAL_DIA)
    for uid in (retador, retado):
        if _contar_duelos(c, desde, uid) >= DUELOS_DIA:
            raise JuegoError("limite_dia", quien=uid, maximo=DUELOS_DIA)


def validar_duelo(retador: int, retado: int, cantidad: int) -> None:
    """Chequeo previo (sin tocar saldos) para dar feedback antes de lanzar el reto."""
    with closing(limites._conectar()) as c:
        _validar_duelo(c, retador, retado, cantidad)


def jugar_duelo(retador: int, retado: int, cantidad: int, _numero: int | None = None) -> dict:
    """Resuelve un duelo ya aceptado. Rojo gana el retador, negro el retado, 0 = empate
    (no se mueve nada). 18 contra 18: es parejo."""
    with limites.transaccion() as c:
        _validar_duelo(c, retador, retado, cantidad)
        numero = girar() if _numero is None else _numero
        ganador = None if numero == 0 else (retador if numero in ROJOS else retado)
        c.execute(
            "INSERT INTO duelos (ts, retador, retado, cantidad, numero, ganador) VALUES (?, ?, ?, ?, ?, ?)",
            (int(time.time()), retador, retado, cantidad, numero, ganador),
        )
        saldos = {retador: limites._disponibles(c, retador), retado: limites._disponibles(c, retado)}
        if ganador is not None:
            perdedor = retado if ganador == retador else retador
            saldos[ganador] = _aplicar(c, ganador, cantidad, "duelo", f"gana vs {perdedor} ({numero})")
            saldos[perdedor] = _aplicar(c, perdedor, -cantidad, "duelo", f"pierde vs {ganador} ({numero})")

    return {
        "numero": numero, "color": color(numero), "ganador": ganador, "cantidad": cantidad,
        "retador": retador, "retado": retado, "saldos": saldos,
    }


# ---------- consulta ----------

def historial(user_id: int, limite: int = 10) -> list[tuple[int, int, str, str, int]]:
    """Ultimos movimientos: (ts, delta, tipo, detalle, saldo_despues)."""
    with closing(limites._conectar()) as c:
        return c.execute(
            "SELECT ts, delta, tipo, detalle, saldo FROM movimientos "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limite),
        ).fetchall()
