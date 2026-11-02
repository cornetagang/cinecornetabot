import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
LIMITE_POR_DEFECTO = 3   # se usa hasta que un admin lo cambie con /gestionar limite
MAX_LIMITE = 50          # tope de seguridad para el comando
DB_PATH = os.environ.get("DB_PATH", "pedidos.db")


def _conectar() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _inicio_semana() -> datetime:
    """Lunes 00:00 (hora de Chile) de la semana actual."""
    ahora = datetime.now(TZ)
    lunes = ahora - timedelta(days=ahora.weekday())
    return lunes.replace(hour=0, minute=0, second=0, microsecond=0)


def _semana() -> str:
    return _inicio_semana().date().isoformat()


def proximo_reset() -> int:
    """Timestamp unix del proximo lunes 00:00, para usar en <t:...> de Discord."""
    return int((_inicio_semana() + timedelta(days=7)).timestamp())


def _init():
    carpeta = os.path.dirname(DB_PATH)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    with closing(_conectar()) as c, c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS pedidos ("
            "user_id INTEGER NOT NULL, "
            "semana TEXT NOT NULL, "
            "cantidad INTEGER NOT NULL, "
            "PRIMARY KEY (user_id, semana))"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS config ("
            "clave TEXT PRIMARY KEY, "
            "valor TEXT NOT NULL)"
        )


_init()


# ---------- Limite semanal configurable ----------

def limite_semanal() -> int:
    """Limite de pedidos por semana (configurable por admins)."""
    with closing(_conectar()) as c:
        row = c.execute(
            "SELECT valor FROM config WHERE clave = 'limite_semanal'"
        ).fetchone()
    if row and row[0].isdigit():
        return int(row[0])
    return LIMITE_POR_DEFECTO


def fijar_limite(nuevo: int) -> int:
    """Guarda el nuevo limite semanal. Devuelve el valor final (entre 1 y MAX_LIMITE)."""
    nuevo = max(1, min(nuevo, MAX_LIMITE))
    with closing(_conectar()) as c, c:
        c.execute(
            "INSERT INTO config (clave, valor) VALUES ('limite_semanal', ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (str(nuevo),),
        )
    return nuevo


# ---------- Pedidos ----------

def obtener(user_id: int) -> int:
    """Pedidos usados por el usuario esta semana (0 si no ha pedido)."""
    with closing(_conectar()) as c:
        row = c.execute(
            "SELECT cantidad FROM pedidos WHERE user_id = ? AND semana = ?",
            (user_id, _semana()),
        ).fetchone()
    return row[0] if row else 0


def consumir(user_id: int) -> tuple[bool, int]:
    """Intenta gastar un pedido. Devuelve (se_pudo, pedidos_usados_esta_semana)."""
    semana = _semana()
    limite = limite_semanal()
    with closing(_conectar()) as c, c:
        cur = c.execute(
            "INSERT INTO pedidos (user_id, semana, cantidad) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, semana) DO UPDATE SET cantidad = cantidad + 1 "
            "WHERE cantidad < ?",
            (user_id, semana, limite),
        )
        ok = cur.rowcount == 1
        usados = c.execute(
            "SELECT cantidad FROM pedidos WHERE user_id = ? AND semana = ?",
            (user_id, semana),
        ).fetchone()[0]
    return ok, usados


def devolver(user_id: int) -> None:
    """Devuelve un pedido (por si falla el envio al canal)."""
    with closing(_conectar()) as c, c:
        c.execute(
            "UPDATE pedidos SET cantidad = cantidad - 1 "
            "WHERE user_id = ? AND semana = ? AND cantidad > 0",
            (user_id, _semana()),
        )


def ajustar(user_id: int, usados: int) -> int:
    """Fija cuantos pedidos lleva usados esta semana (0 = reset total).
    Devuelve el valor final, acotado entre 0 y MAX_LIMITE."""
    usados = max(0, min(usados, MAX_LIMITE))
    with closing(_conectar()) as c, c:
        c.execute(
            "INSERT INTO pedidos (user_id, semana, cantidad) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, semana) DO UPDATE SET cantidad = excluded.cantidad",
            (user_id, _semana(), usados),
        )
    return usados


def listar() -> list[tuple[int, int]]:
    """Lista (user_id, usados) de esta semana, de mayor a menor."""
    with closing(_conectar()) as c:
        return c.execute(
            "SELECT user_id, cantidad FROM pedidos "
            "WHERE semana = ? AND cantidad > 0 ORDER BY cantidad DESC, user_id",
            (_semana(),),
        ).fetchall()
