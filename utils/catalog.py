import re
import time
import asyncio
import unicodedata
import aiohttp

BASE_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwAJT7ElT1guBUiZpzKaHoI7dr4Zy3D9ZNS9_taqAWZyhGgTq5ttDdWBekVA_kjgnU/exec"
)

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
            return await resp.json(content_type=None)
    except Exception as e:
        print(f"[Catalogo] Error obteniendo '{data_key}': {e}")
        return {}


class CatalogoCache:
    def __init__(self):
        # titulo normalizado -> años con los que aparece en el catalogo
        self.titulos: dict[str, set[str]] = {}
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

            # Si lo principal vino vacio, algo fallo: no pisamos el cache bueno.
            if not datos.get("allMovies") and not datos.get("series"):
                print("[Catalogo] Respuesta vacia, se conserva el cache anterior")
                return

            nuevos: dict[str, set[str]] = {}

            def agregar(item: dict, campo: str):
                titulo = item.get(campo, "")
                if titulo:
                    año = str(item.get("year", "") or "").strip()
                    nuevos.setdefault(normalizar(titulo), set()).add(año)

            for item in datos["allMovies"].values():
                agregar(item, "id")
            for item in datos["series"].values():
                agregar(item, "secondTitle")

            for clave, contenido in datos.items():
                if not clave.startswith("saga:"):
                    continue
                for item in contenido.values():
                    tipo = item.get("type")
                    if tipo == "movie":
                        agregar(item, "id")
                    elif tipo == "serie":
                        agregar(item, "secondTitle")

            self.titulos = nuevos
            self._ultima_actualizacion = time.monotonic()
            print(f"[Catalogo] Actualizado: {len(self.titulos)} titulos en cache "
                  f"({len(saga_ids)} sagas incluidas)")

    def contiene(self, titulo_original: str, anio: str = "") -> bool:
        años = self.titulos.get(normalizar(titulo_original))
        if not años:
            return False
        if not anio.isdigit():
            return True
        for a in años:
            if not a[:4].isdigit():
                return True  # el catalogo no tiene año: no se puede descartar
            if abs(int(a[:4]) - int(anio)) <= 1:
                return True
        return False


catalogo = CatalogoCache()


async def iniciar_refresco_periodico():
    while True:
        try:
            await catalogo.actualizar()
        except Exception as e:
            print(f"[Catalogo] Error en refresco periodico: {e}")
        await asyncio.sleep(REFRESH_INTERVAL)