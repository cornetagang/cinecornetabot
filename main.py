import os
import threading
import aiohttp
import disnake
from disnake.ext import commands
from flask import Flask

# Flask keep-alive
app = Flask(__name__)

@app.route("/")
def index():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

# Configuracion
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
TMDB_API_KEY  = os.environ["TMDB_API_KEY"]

TMDB_BASE     = "https://api.themoviedb.org/3"
TMDB_LANGUAGE = "es-MX"

# Bot
intents = disnake.Intents.default()
bot = commands.InteractionBot(intents=intents)

# Helpers TMDB
def extraer_año(item: dict, media_type: str) -> str:
    fecha = item.get("release_date" if media_type == "movie" else "first_air_date", "")
    return fecha[:4] if fecha else "????"


async def buscar_tmdb(busqueda: str) -> list[disnake.OptionChoice]:
    opciones = []

    if len(busqueda) < 3:
        return opciones

    url = (
        f"{TMDB_BASE}/search/multi"
        f"?api_key={TMDB_API_KEY}"
        f"&language={TMDB_LANGUAGE}"
        f"&query={busqueda}"
        f"&include_adult=false"
    )

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            async with session.get(url) as resp:
                print(f"[TMDB] busqueda='{busqueda}' -> status={resp.status}")
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
                    año    = extraer_año(item, media_type)
                    emoji  = "🎬" if media_type == "movie" else "📺"

                    etiqueta = f"{emoji} {titulo} ({año})"[:100]
                    valor    = f"{item['id']}|{media_type}"

                    opciones.append(disnake.OptionChoice(name=etiqueta, value=valor))

    except Exception as e:
        print(f"[TMDB] Error en busqueda: {e}")

    print(f"[TMDB] opciones devueltas: {len(opciones)}")
    return opciones


async def detalle_tmdb(tmdb_id: int, media_type: str) -> dict | None:
    url = (
        f"{TMDB_BASE}/{media_type}/{tmdb_id}"
        f"?api_key={TMDB_API_KEY}"
        f"&language={TMDB_LANGUAGE}"
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url) as resp:
                print(f"[TMDB] detalle id={tmdb_id} type={media_type} -> status={resp.status}")
                if resp.status == 200:
                    return await resp.json()
                texto = await resp.text()
                print(f"[TMDB] detalle error: {texto[:300]}")
    except Exception as e:
        print(f"[TMDB] Error en detalle: {e}")
    return None


# Autocompletado
async def autocomplete_titulo(
    inter: disnake.ApplicationCommandInteraction,
    input: str
) -> list[disnake.OptionChoice]:
    return await buscar_tmdb(input)


# Comando /pedir
@bot.slash_command(name="pedir", description="Pide una pelicula o serie.")
async def pedir(
    inter: disnake.ApplicationCommandInteraction,
    titulo: str = commands.Param(
        description="Nombre de la pelicula o serie",
        autocomplete=autocomplete_titulo
    ),
    idioma: str = commands.Param(
        description="En que idioma la quieres ver?",
        choices=["Latino", "Subtitulada", "Indiferente"]
    )
):
    await inter.response.send_message("Procesando tu pedido!", ephemeral=True)

    try:
        tmdb_id_str, media_type = titulo.split("|")
        detalle = await detalle_tmdb(int(tmdb_id_str), media_type)
        if not detalle:
            raise ValueError("Sin detalle")
    except Exception as e:
        print(f"[pedir] Error parseando titulo '{titulo}': {e}")
        await inter.channel.send(
            f"{inter.author.mention} Error al obtener detalles. "
            "Por favor selecciona una opcion de la lista desplegable."
        )
        return

    nombre     = detalle.get("title") or detalle.get("name")
    anio       = extraer_año(detalle, media_type)
    generos    = ", ".join(g["name"] for g in detalle.get("genres", []))
    puntuacion = detalle.get("vote_average", 0)
    poster     = detalle.get("poster_path")
    emoji      = "🎬" if media_type == "movie" else "📺"

    if idioma == "Latino":
        texto_idioma = "en [Latino]"
    elif idioma == "Subtitulada":
        texto_idioma = "[Subtitulada]"
    else:
        texto_idioma = "[Idioma Indiferente]"

    embed = disnake.Embed(
        title=f"{emoji} {nombre} ({anio})",
        url=f"https://www.themoviedb.org/{media_type}/{tmdb_id_str}",
        description=detalle.get("overview") or "Sin sinopsis.",
        color=0x00D4FF,
    )
    embed.add_field(name="Año",        value=anio,                   inline=True)
    embed.add_field(name="Genero",     value=generos or "N/A",       inline=True)
    embed.add_field(name="Puntuacion", value=f"{puntuacion:.1f}/10", inline=True)

    if poster:
        embed.set_image(url=f"https://image.tmdb.org/t/p/w500{poster}")

    await inter.channel.send(
        content=f"{inter.author.mention} ha pedido: {emoji} {nombre} ({anio}) {texto_idioma}",
        embed=embed
    )


@bot.event
async def on_ready():
    print(f"Bot listo: {bot.user}")


if __name__ == "__main__":
    keep_alive()
    bot.run(DISCORD_TOKEN)
