"""Vinculo entre la ID de Discord (dueña del saldo) y la cuenta de la web (Firebase uid).

Nunca se vincula por nombre de usuario (se puede cambiar o reciclar): solo por ID.
Para probar que la misma persona controla las dos cuentas:
  1) en Discord, /vincular entrega un codigo de un solo uso (dura 10 min);
  2) en la web, estando con su sesion, la persona pega el codigo;
  3) el servidor valida el codigo y guarda la pareja (discord_id <-> firebase_uid).
Reglas: uno a uno (una cuenta web con una sola ID de Discord y al reves); solo un admin desvincula.
"""
import secrets
import time
from contextlib import closing

from utils import limites
from utils.economia import JuegoError

CODIGO_VIDA = 10 * 60
# Sin caracteres que se confunden al leer (0/O, 1/I/L)
_ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LARGO = 8


def _init():
    with closing(limites._conectar()) as c, c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS vinculos ("
            "discord_id INTEGER PRIMARY KEY, firebase_uid TEXT NOT NULL UNIQUE, ts INTEGER NOT NULL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS codigos_vinculo ("
            "codigo TEXT PRIMARY KEY, discord_id INTEGER NOT NULL, expira INTEGER NOT NULL)"
        )


_init()


def crear_codigo(discord_id: int, ahora: int | None = None) -> tuple[str, int]:
    """Genera un codigo nuevo (el anterior de esa persona deja de valer). Devuelve (codigo, expira_ts)."""
    ahora = int(time.time()) if ahora is None else ahora
    with limites.transaccion() as c:
        if c.execute("SELECT 1 FROM vinculos WHERE discord_id = ?", (discord_id,)).fetchone():
            raise JuegoError("ya_vinculado")
        c.execute("DELETE FROM codigos_vinculo WHERE discord_id = ? OR expira < ?", (discord_id, ahora))
        while True:
            codigo = "".join(secrets.choice(_ALFABETO) for _ in range(_LARGO))
            if not c.execute("SELECT 1 FROM codigos_vinculo WHERE codigo = ?", (codigo,)).fetchone():
                break
        expira = ahora + CODIGO_VIDA
        c.execute("INSERT INTO codigos_vinculo (codigo, discord_id, expira) VALUES (?, ?, ?)",
                  (codigo, discord_id, expira))
    return codigo, expira


def canjear(codigo: str, firebase_uid: str, ahora: int | None = None) -> int:
    """Une la cuenta web con la ID de Discord dueña del codigo. Devuelve esa ID."""
    ahora = int(time.time()) if ahora is None else ahora
    codigo = (codigo or "").strip().upper().replace("-", "").replace(" ", "")
    with limites.transaccion() as c:
        fila = c.execute("SELECT discord_id, expira FROM codigos_vinculo WHERE codigo = ?", (codigo,)).fetchone()
        if not fila:
            raise JuegoError("codigo_invalido")
        discord_id, expira = fila
        if expira < ahora:
            c.execute("DELETE FROM codigos_vinculo WHERE codigo = ?", (codigo,))
            raise JuegoError("codigo_vencido")
        if c.execute("SELECT 1 FROM vinculos WHERE firebase_uid = ?", (firebase_uid,)).fetchone():
            raise JuegoError("uid_vinculado")
        if c.execute("SELECT 1 FROM vinculos WHERE discord_id = ?", (discord_id,)).fetchone():
            raise JuegoError("ya_vinculado")
        c.execute("INSERT INTO vinculos (discord_id, firebase_uid, ts) VALUES (?, ?, ?)",
                  (discord_id, firebase_uid, ahora))
        c.execute("DELETE FROM codigos_vinculo WHERE codigo = ?", (codigo,))   # un solo uso
    return discord_id


def discord_de(firebase_uid: str) -> int | None:
    with closing(limites._conectar()) as c:
        fila = c.execute("SELECT discord_id FROM vinculos WHERE firebase_uid = ?", (firebase_uid,)).fetchone()
    return fila[0] if fila else None


def uid_de(discord_id: int) -> str | None:
    with closing(limites._conectar()) as c:
        fila = c.execute("SELECT firebase_uid FROM vinculos WHERE discord_id = ?", (discord_id,)).fetchone()
    return fila[0] if fila else None


def desvincular(discord_id: int) -> bool:
    """Solo para admins. Devuelve True si habia vinculo."""
    with limites.transaccion() as c:
        return c.execute("DELETE FROM vinculos WHERE discord_id = ?", (discord_id,)).rowcount > 0
