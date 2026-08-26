import re
import time
import asyncio
import unicodedata
import aiohttp

BASE_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwAJT7ElT1guBUiZpzKaHoI7dr4Zy3D9ZNS9_taqAWZyhGgTq5ttDdWBekVA_kjgnU/exec"
)

# Cada cuanto se refresca el catalogo completo en segundo plano.
REFRESH_INTERVAL = 20 * 60  # 20 minutos


def normalizar(texto: str) -> str:
    """minusculas, sin tildes/puntuacion, espacios colapsados."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return texto.strip()


async def _fetch(session: aiohttp.ClientSession, data_key: str) -> dict:
    url = f"{BASE_URL}?data={data_key}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                print(f"[Catalogo] '{data_key}' -> status {resp.status}")
                return {}
            # content_type=None: el Apps Script a veces no manda application/json
            return await resp.json(content_type=None)
    except Exception as e:
        print(f"[Catalogo] Error obteniendo '{data_key}': {e}")
        return {}


class CatalogoCache:
    def __init__(self):
        self.titulos: set[str] = set()
        self._ultima_actualizacion: float = 0.0
        self._lock = asyncio.Lock()

    async def actualizar(self):
        async with self._lock:
            async with aiohttp.ClientSession() as session:
                sagas_list = await _fetch(session, "sagas_list")
                saga_ids = list(sagas_list.keys())

                tareas = {
                    "allMovies": _fetch(session, "allMovies"),
                    "series": _fetch(session, "series"),
                }
                for saga_id in saga_ids:
                    tareas[f"saga:{saga_id}"] = _fetch(session, saga_id)

                claves = list(tareas.keys())
                resultados = await asyncio.gather(*tareas.values())
                datos = dict(zip(claves, resultados))

            nuevos_titulos: set[str] = set()

            for item in datos.get("allMovies", {}).values():
                nuevos_titulos.add(normalizar(item.get("id", "")))

            for item in datos.get("series", {}).values():
                nuevos_titulos.add(normalizar(item.get("secondTitle", "")))

            for clave, contenido in datos.items():
                if not clave.startswith("saga:"):
                    continue
                for item in contenido.values():
                    tipo = item.get("type")
                    if tipo == "movie":
                        nuevos_titulos.add(normalizar(item.get("id", "")))
                    elif tipo == "serie":
                        nuevos_titulos.add(normalizar(item.get("secondTitle", "")))

            nuevos_titulos.discard("")
            self.titulos = nuevos_titulos
            self._ultima_actualizacion = time.monotonic()
            print(f"[Catalogo] Actualizado: {len(self.titulos)} titulos en cache "
                  f"({len(saga_ids)} sagas incluidas)")

    def esta_vencido(self) -> bool:
        return (time.monotonic() - self._ultima_actualizacion) > REFRESH_INTERVAL

    def contiene(self, titulo_original: str) -> bool:
        return normalizar(titulo_original) in self.titulos


# Instancia unica compartida por todo el bot.
catalogo = CatalogoCache()


async def iniciar_refresco_periodico():
    """Loop de fondo: actualiza el catalogo al arrancar y despues cada REFRESH_INTERVAL."""
    while True:
        try:
            await catalogo.actualizar()
        except Exception as e:
            print(f"[Catalogo] Error en refresco periodico: {e}")
        await asyncio.sleep(REFRESH_INTERVAL)
