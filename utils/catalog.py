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
    """minusculas, sin tildes/puntuacion, espacios colapsados. Uso: matching/busqueda."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return texto.strip()


def slugify(texto: str) -> str:
    """minusculas, sin tildes/puntuacion, palabras unidas por guion.
    Debe coincidir EXACTO con el slugify() de script.js en la web,
    o los links del bot no van a resolver del lado del sitio."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    return texto.strip("-")


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
        self.titulos: set[str] = set()  # para el chequeo de duplicados en /pedir
        self.items: list[dict] = []  # para /catalogo (busqueda + link + poster)
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
            nuevos_items: list[dict] = []

            def agregar(item: dict, tipo: str, campo_slug: str):
                titulo_original = item.get(campo_slug, "")
                if not titulo_original:
                    return
                nuevos_titulos.add(normalizar(titulo_original))
                nuevos_items.append({
                    "tipo": tipo,  # "movie" | "serie"
                    "titulo_original": titulo_original,  # usado para el link
                    "titulo_es": item.get("title") or titulo_original,
                    "poster": item.get("poster", ""),
                    "synopsis": item.get("synopsis", ""),
                    "anio": item.get("year", ""),
                })

            for item in datos.get("allMovies", {}).values():
                agregar(item, "movie", "id")

            for item in datos.get("series", {}).values():
                agregar(item, "serie", "secondTitle")

            for clave, contenido in datos.items():
                if not clave.startswith("saga:"):
                    continue
                for item in contenido.values():
                    tipo = item.get("type")
                    if tipo == "movie":
                        agregar(item, "movie", "id")
                    elif tipo == "serie":
                        agregar(item, "serie", "secondTitle")

            self.titulos = nuevos_titulos
            self.items = nuevos_items
            self._ultima_actualizacion = time.monotonic()
            print(f"[Catalogo] Actualizado: {len(self.titulos)} titulos en cache "
                  f"({len(saga_ids)} sagas incluidas)")

    def esta_vencido(self) -> bool:
        return (time.monotonic() - self._ultima_actualizacion) > REFRESH_INTERVAL

    def contiene(self, titulo_original: str) -> bool:
        return normalizar(titulo_original) in self.titulos

    def buscar(self, consulta: str, limite: int = 25) -> list[dict]:
        """Busqueda simple por substring sobre titulo en espanol + original."""
        q = normalizar(consulta)
        if not q:
            return []

        coincidencias = []
        for item in self.items:
            texto = normalizar(f"{item['titulo_es']} {item['titulo_original']}")
            if q in texto:
                empieza_con = texto.startswith(q)
                coincidencias.append((0 if empieza_con else 1, item))

        coincidencias.sort(key=lambda par: par[0])
        return [item for _, item in coincidencias[:limite]]


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
