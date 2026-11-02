import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
# Valor por DEFECTO del limite semanal. El valor real se guarda en la base (tabla 'config')
# y se cambia con /gestionar limite. Usa limite_semanal() para leer el valor vigente.
LIMITE_SEMANAL = 3
DB_PATH = os.environ.get("DB_PATH", "pedidos.db")

# Modelo: por persona y semana se guardan
#   cantidad -> pedidos USADOS con /pedir (como siempre)
#   ajuste   -> pedidos ganados (+) o perdidos (-) en juegos / ajustes de admin
# disponibles = limite_semanal + ajuste - cantidad   (nunca menos de 0 al mostrarlo)
# Todo se reinicia solo cada lunes (la fila es de la semana), sin ningun cron.
# El limite semanal es global: al cambiarlo aplica a todos, desde ese momento, y se queda
# asi (tambien las semanas siguientes) hasta que lo vuelvas a cambiar.


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
        # Configuracion global (clave/valor). Hoy solo guarda 'limite_semanal'.
        c.execute(
            "CREATE TABLE IF NOT EXISTS config ("
            "clave TEXT PRIMARY KEY, valor TEXT NOT NULL)"
        )


_init()


# ---------- limite semanal global ----------

def _limite(c: sqlite3.Connection) -> int:
    """Limite semanal vigente (el guardado en la base, o el de defecto si nunca se cambio)."""
    row = c.execute("SELECT valor FROM config WHERE clave = 'limite_semanal'").fetchone()
    try:
        return int(row[0]) if row else LIMITE_SEMANAL
    except ValueError:
        return LIMITE_SEMANAL


def limite_semanal() -> int:
    with closing(_conectar()) as c:
        return _limite(c)


def fijar_limite(nuevo: int) -> int:
    """Cambia el limite semanal base de TODOS. Devuelve el limite anterior."""
    with transaccion() as c:
        anterior = _limite(c)
        c.execute(
            "INSERT INTO config (clave, valor) VALUES ('limite_semanal', ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (str(nuevo),),
        )
    return anterior


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
    return max(0, _limite(c) + ajuste - usados)


def estado(user_id: int) -> dict:
    with closing(_conectar()) as c:
        usados, ajuste = _fila(c, user_id, _semana())
        base = _limite(c)
    return {
        "usados": usados,
        "ajuste": ajuste,
        "base": base,
        "disponibles": max(0, base + ajuste - usados),
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
        if _limite(c) + ajuste - usados <= 0:
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
    """Fija cuantos pedidos lleva usados esta semana (0 = reset de usados).
    Devuelve el valor final, acotado entre 0 y lo que tiene permitido (base + premios).
    OJO: solo toca los usados. Para devolver/quitar/reset desde admin usa las funciones
    de utils.economia (devolver_admin, quitar_admin, reset_admin), que ademas lo dejan
    anotado en el historial."""
    semana = _semana()
    with transaccion() as c:
        _, ajuste = _fila(c, user_id, semana)
        usados = max(0, min(usados, max(0, _limite(c) + ajuste)))
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
        base = _limite(c)
        filas = c.execute(
            "SELECT user_id, cantidad, ajuste FROM pedidos "
            "WHERE semana = ? AND (cantidad > 0 OR ajuste != 0) "
            "ORDER BY cantidad DESC, user_id",
            (_semana(),),
        ).fetchall()
    return [(uid, usados, ajuste, max(0, base + ajuste - usados)) for uid, usados, ajuste in filas]
