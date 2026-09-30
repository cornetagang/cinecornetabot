import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
LIMITE_SEMANAL = 3
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


_init()


def consumir(user_id: int) -> tuple[bool, int]:
    """Intenta gastar un pedido. Devuelve (se_pudo, pedidos_usados_esta_semana)."""
    semana = _semana()
    with closing(_conectar()) as c, c:
        cur = c.execute(
            "INSERT INTO pedidos (user_id, semana, cantidad) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, semana) DO UPDATE SET cantidad = cantidad + 1 "
            "WHERE cantidad < ?",
            (user_id, semana, LIMITE_SEMANAL),
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