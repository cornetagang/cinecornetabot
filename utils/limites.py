import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
LIMITE_SEMANAL = 3
DB_PATH = os.environ.get("DB_PATH", "pedidos.db")

# Modelo: por persona y semana se guardan
#   cantidad -> pedidos USADOS con /pedir (como siempre)
#   ajuste   -> pedidos ganados (+) o perdidos (-) en juegos / ajustes de admin
# disponibles = LIMITE_SEMANAL + ajuste - cantidad
# Todo se reinicia solo cada lunes (la fila es de la semana), sin ningun cron.


def _conectar() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA busy_timeout = 10000")
    return c


@contextmanager
def transaccion():
    """Transaccion con BEGIN IMMEDIATE: las escrituras se hacen de a una, incluso si
    escriben a la vez el bot (Discord) y la API web. Si algo falla, se deshace todo."""
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    try:
        c.execute("PRAGMA busy_timeout = 10000")
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        try:
            c.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        c.close()


def _inicio_semana() -> datetime:
    """Lunes 00:00 (hora de Chile) de la semana actual."""
    ahora = datetime.now(TZ)
    lunes = ahora - timedelta(days=ahora.weekday())
    return lunes.replace(hour=0, minute=0, second=0, microsecond=0)


def _semana() -> str:
    return _inicio_semana().date().isoformat()


def inicio_dia() -> int:
    """Timestamp unix de hoy 00:00 (hora de Chile)."""
    return int(datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def proximo_reset() -> int:
    """Timestamp unix del proximo lunes 00:00, para usar en <t:...> de Discord."""
    return int((_inicio_semana() + timedelta(days=7)).timestamp())


def _init():
    carpeta = os.path.dirname(DB_PATH)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    with closing(_conectar()) as c, c:
        c.execute("PRAGMA journal_mode = WAL")
        c.execute(
            "CREATE TABLE IF NOT EXISTS pedidos ("
            "user_id INTEGER NOT NULL, "
            "semana TEXT NOT NULL, "
            "cantidad INTEGER NOT NULL, "
            "PRIMARY KEY (user_id, semana))"
        )
        # Migracion: bases creadas antes de las apuestas no tienen la columna 'ajuste'.
        columnas = [fila[1] for fila in c.execute("PRAGMA table_info(pedidos)")]
        if "ajuste" not in columnas:
            c.execute("ALTER TABLE pedidos ADD COLUMN ajuste INTEGER NOT NULL DEFAULT 0")


_init()


# ---------- lectura ----------

def _fila(c: sqlite3.Connection, user_id: int, semana: str) -> tuple[int, int]:
    """(usados, ajuste) de la semana; (0, 0) si no hay fila."""
    row = c.execute(
        "SELECT cantidad, ajuste FROM pedidos WHERE user_id = ? AND semana = ?",
        (user_id, semana),
    ).fetchone()
    return (row[0], row[1]) if row else (0, 0)


def _disponibles(c: sqlite3.Connection, user_id: int) -> int:
    usados, ajuste = _fila(c, user_id, _semana())
    return LIMITE_SEMANAL + ajuste - usados


def estado(user_id: int) -> dict:
    with closing(_conectar()) as c:
        usados, ajuste = _fila(c, user_id, _semana())
    return {
        "usados": usados,
        "ajuste": ajuste,
        "base": LIMITE_SEMANAL,
        "disponibles": LIMITE_SEMANAL + ajuste - usados,
    }


def disponibles(user_id: int) -> int:
    """Pedidos que le quedan esta semana (base + premios - usados)."""
    return estado(user_id)["disponibles"]


def obtener(user_id: int) -> int:
    """Pedidos USADOS por el usuario esta semana (0 si no ha pedido)."""
    return estado(user_id)["usados"]


# ---------- escritura ----------

def consumir(user_id: int) -> tuple[bool, int]:
    """Intenta gastar un pedido. Devuelve (se_pudo, pedidos_usados_esta_semana)."""
    semana = _semana()
    with transaccion() as c:
        usados, ajuste = _fila(c, user_id, semana)
        if LIMITE_SEMANAL + ajuste - usados <= 0:
            return False, usados
        c.execute(
            "INSERT INTO pedidos (user_id, semana, cantidad, ajuste) VALUES (?, ?, 1, 0) "
            "ON CONFLICT(user_id, semana) DO UPDATE SET cantidad = cantidad + 1",
            (user_id, semana),
        )
        return True, usados + 1


def devolver(user_id: int) -> None:
    """Devuelve un pedido (por si falla el envio al canal)."""
    with transaccion() as c:
        c.execute(
            "UPDATE pedidos SET cantidad = cantidad - 1 "
            "WHERE user_id = ? AND semana = ? AND cantidad > 0",
            (user_id, _semana()),
        )


def ajustar(user_id: int, usados: int) -> int:
    """Fija cuantos pedidos lleva usados esta semana (0 = reset total).
    Devuelve el valor final, acotado entre 0 y lo que tiene permitido (base + premios)."""
    semana = _semana()
    with transaccion() as c:
        _, ajuste = _fila(c, user_id, semana)
        usados = max(0, min(usados, max(0, LIMITE_SEMANAL + ajuste)))
        c.execute(
            "INSERT INTO pedidos (user_id, semana, cantidad, ajuste) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(user_id, semana) DO UPDATE SET cantidad = excluded.cantidad",
            (user_id, semana, usados),
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


def listar_estado() -> list[tuple[int, int, int, int]]:
    """Lista (user_id, usados, ajuste, disponibles) de quien se movio esta semana."""
    with closing(_conectar()) as c:
        filas = c.execute(
            "SELECT user_id, cantidad, ajuste FROM pedidos "
            "WHERE semana = ? AND (cantidad > 0 OR ajuste != 0) "
            "ORDER BY cantidad DESC, user_id",
            (_semana(),),
        ).fetchall()
    return [(uid, usados, ajuste, LIMITE_SEMANAL + ajuste - usados) for uid, usados, ajuste in filas]
