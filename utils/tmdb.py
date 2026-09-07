import os
import aiohttp
import disnake

TMDB_API_KEY = os.environ["TMDB_API_KEY"]
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_LANGUAGE = "es-MX"

# Discord corta la interaccion de autocompletado a los 3s. Reusamos una sola
# sesion de aiohttp (evita el handshake TLS en cada tecla tipeada) y le damos
# un timeout bien por debajo de esos 3s para dejar margen al resto del viaje
# (Discord -> bot -> Discord).
_session: aiohttp.ClientSession | None = None
AUTOCOMPLETE_TIMEOUT = aiohttp.ClientTimeout(total=1.5)
DETALLE_TIMEOUT = aiohttp.ClientTimeout(total=5)


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


def extraer_año(item: dict, media_type: str) -> str:
    fecha = item.get("release_date" if media_type == "movie" else "first_air_date", "")
    return fecha[:4] if fecha else "????"


async def buscar_tmdb(busqueda: str) -> list[disnake.OptionChoice]:
    opciones = []

    if len(busqueda) < 2:
        return opciones

    url = (
        f"{TMDB_BASE}/search/multi"
        f"?api_key={TMDB_API_KEY}"
        f"&language={TMDB_LANGUAGE}"
        f"&query={busqueda}"
        f"&include_adult=false"
    )

    try:
        session = _get_session()
        async with session.get(url, timeout=AUTOCOMPLETE_TIMEOUT) as resp:
            if resp.status != 200:
                texto = await resp.text()
                print(f"[TMDB] respuesta inesperada: {texto[:300]}")
                return opciones

            data = await resp.json()

            resultados = [
                item for item in data.get("results", [])
                if item.get("media_type") in ("movie", "tv")
            ]
            resultados.sort(key=lambda x: x.get("popularity", 0), reverse=True)
            resultados = resultados[:25]

            for item in resultados:
                media_type = item.get("media_type")
                titulo = item.get("title") or item.get("name") or "Sin titulo"
                año = extraer_año(item, media_type)
                emoji = "🎬" if media_type == "movie" else "📺"

                etiqueta = f"{emoji} {titulo} ({año})"[:100]
                valor = f"{item['id']}|{media_type}"

                opciones.append(disnake.OptionChoice(name=etiqueta, value=valor))

    except Exception as e:
        # Timeout, conexion caida, JSON invalido, etc. Devolvemos lista vacia
        # en vez de dejar la interaccion colgada: Discord mostraria
        # "Error al cargar las opciones" igual, pero al menos no explota.
        print(f"[TMDB] Error en busqueda: {e}")

    return opciones


async def detalle_tmdb(tmdb_id: int, media_type: str) -> dict | None:
    url = (
        f"{TMDB_BASE}/{media_type}/{tmdb_id}"
        f"?api_key={TMDB_API_KEY}"
        f"&language={TMDB_LANGUAGE}"
    )
    try:
        session = _get_session()
        async with session.get(url, timeout=DETALLE_TIMEOUT) as resp:
            if resp.status == 200:
                return await resp.json()
            texto = await resp.text()
            print(f"[TMDB] detalle error: {texto[:300]}")
    except Exception as e:
        print(f"[TMDB] Error en detalle: {e}")
    return None
