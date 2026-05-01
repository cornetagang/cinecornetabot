import os
import threading
import aiohttp
import disnake
from disnake.ext import commands
from flask import Flask

# ── Flask keep-alive ─────────────────────────────────────────────────────────
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

# ── Configuración ────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.environ["DISCORD_TOKEN"]
TMDB_API_KEY   = os.environ["TMDB_API_KEY"]

TMDB_BASE      = "https://api.themoviedb.org/3"
TMDB_LANGUAGE  = "es-MX"
TMDB_HEADERS   = {
    "Authorization": f"Bearer {TMDB_API_KEY}",
    "accept": "application/json",
}

# ── Bot ──────────────────────────────────────────────────────────────────────
intents = disnake.Intents.default()
bot = commands.InteractionBot(intents=intents)


# ── Helpers TMDB ─────────────────────────────────────────────────────────────
async def buscar_tmdb(query: str) -> list[dict]:
    """
    Busca en /search/movie y /search/tv por separado,
    mezcla los resultados y los ordena por popularidad descendente.
    """
    params_base = {
        "query":         query,
        "language":      TMDB_LANGUAGE,
        "page":          1,
        "include_adult": "false",
    }
    resultados = []
    async with aiohttp.ClientSession() as session:
        for media_type in ("movie", "tv"):
            try:
                async with session.get(
                    f"{TMDB_BASE}/search/{media_type}",
                    headers=TMDB_HEADERS,
                    params=params_base,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    for item in data.get("results", [])[:10]:
                        item["media_type"] = media_type
                        resultados.append(item)
            except Exception:
                continue

    # Ordenar por popularidad y devolver los 25 mejores (límite de Discord)
    resultados.sort(key=lambda x: x.get("popularity", 0), reverse=True)
    return resultados[:25]


async def detalle_tmdb(tmdb_id: int, media_type: str) -> dict | None:
    """Obtiene detalles completos de una película o serie por su ID."""
    params = {"language": TMDB_LANGUAGE}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{TMDB_BASE}/{media_type}/{tmdb_id}",
                headers=TMDB_HEADERS,
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception:
            return None


def tipo_emoji(media_type: str) -> str:
    return {"movie": "🎬", "tv": "📺"}.get(media_type, "🎞️")


def extraer_año(item: dict, media_type: str) -> str:
    fecha = item.get("release_date" if media_type == "movie" else "first_air_date", "")
    return fecha[:4] if fecha else "????"


# ── Autocompletado ────────────────────────────────────────────────────────────
async def autocompletar_titulo(
    inter: disnake.ApplicationCommandInteraction,
    input_usuario: str,
) -> list[disnake.OptionChoice]:
    if len(input_usuario) < 2:
        return []

    resultados = await buscar_tmdb(input_usuario)
    opciones = []
    for item in resultados:
        media_type = item.get("media_type", "movie")
        titulo     = item.get("title") or item.get("name") or "Sin título"
        año        = extraer_año(item, media_type)
        tmdb_id    = item.get("id", 0)
        emoji      = tipo_emoji(media_type)

        etiqueta = f"{emoji} {titulo} ({año})"[:100]
        valor    = f"{tmdb_id}|{media_type}"

        opciones.append(disnake.OptionChoice(name=etiqueta, value=valor))

    return opciones


# ── Comando /pedir ────────────────────────────────────────────────────────────
@bot.slash_command(
    name="pedir",
    description="Pide una película o serie para que sea añadida al servidor.",
)
async def pedir(
    inter: disnake.ApplicationCommandInteraction,
    titulo: str = commands.Param(
        description="Escribe el nombre de la película o serie.",
        autocomplete=autocompletar_titulo,
    ),
):
    await inter.response.send_message("✅ ¡Pedido enviado!", ephemeral=True)

    try:
        tmdb_id_str, media_type = titulo.split("|")
        tmdb_id = int(tmdb_id_str)
    except ValueError:
        await inter.channel.send(
            f"{inter.author.mention} ❌ Selecciona una opción válida del autocompletado."
        )
        return

    detalle = await detalle_tmdb(tmdb_id, media_type)

    if not detalle:
        await inter.channel.send(
            f"{inter.author.mention} ❌ No pude obtener los detalles del título. Intenta de nuevo."
        )
        return

    nombre     = detalle.get("title") or detalle.get("name") or "Desconocido"
    año        = extraer_año(detalle, media_type)
    generos    = ", ".join(g["name"] for g in detalle.get("genres", [])) or "N/A"
    puntuacion = detalle.get("vote_average", 0)
    votos      = detalle.get("vote_count", 0)
    sinopsis   = detalle.get("overview") or "Sin sinopsis disponible."
    poster     = detalle.get("poster_path")
    emoji      = tipo_emoji(media_type)

    tmdb_url   = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
    poster_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None

    embed = disnake.Embed(
        title=f"{emoji} {nombre} ({año})",
        url=tmdb_url,
        description=sinopsis,
        color=0x00d4ff,
    )
    embed.add_field(name="📅 Año",        value=año,                                    inline=True)
    embed.add_field(name="🎭 Género",     value=generos,                                inline=True)
    embed.add_field(name="⭐ Puntuación", value=f"{puntuacion:.1f}/10 ({votos} votos)", inline=True)
    embed.set_footer(
        text=f"Pedido por {inter.author} • {inter.guild.name if inter.guild else 'DM'}",
        icon_url=inter.author.display_avatar.url,
    )
    if poster_url:
        embed.set_image(url=poster_url)

    await inter.channel.send(
        content=f"🗣️ {inter.author.mention} ha pedido: {emoji} {nombre} ({año})",
        embed=embed,
    )


# ── Eventos ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")


# ── Inicio ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    keep_alive()
    bot.run(DISCORD_TOKEN)
