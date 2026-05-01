import os
import threading
import aiohttp
import disnake
from disnake.ext import commands
from flask import Flask
from deep_translator import GoogleTranslator

# ── Flask keep-alive ─────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot Online", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

# ── Configuración ────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OMDB_API_KEY  = os.environ["OMDB_API_KEY"]

OMDB_BASE     = "http://www.omdbapi.com/"

# ── Bot ──────────────────────────────────────────────────────────────────────
intents = disnake.Intents.default()
bot = commands.InteractionBot(intents=intents)


# ── Traducción ────────────────────────────────────────────────────────────────
def traducir_a_ingles(texto: str) -> str:
    """Traduce el texto de español a inglés. Si falla, devuelve el original."""
    try:
        return GoogleTranslator(source="es", target="en").translate(texto)
    except Exception:
        return texto


# ── Helpers OMDb ─────────────────────────────────────────────────────────────
async def buscar_omdb(query: str) -> list[dict]:
    params = {"apikey": OMDB_API_KEY, "s": query}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                OMDB_BASE,
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if data.get("Response") != "True":
                    return []
                return data.get("Search", [])[:10]
        except Exception:
            return []


async def detalle_omdb(imdb_id: str) -> dict | None:
    params = {"apikey": OMDB_API_KEY, "i": imdb_id, "plot": "short"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                OMDB_BASE,
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data if data.get("Response") == "True" else None
        except Exception:
            return None


def tipo_emoji(tipo: str) -> str:
    return {"movie": "🎬", "series": "📺", "episode": "📎"}.get(
        tipo.lower(), "🎞️"
    )


# ── Autocompletado ────────────────────────────────────────────────────────────
async def autocompletar_titulo(
    inter: disnake.ApplicationCommandInteraction,
    input_usuario: str,
) -> list[disnake.OptionChoice]:
    if len(input_usuario) < 2:
        return []

    # Traducir antes de buscar en OMDb
    query_en = traducir_a_ingles(input_usuario)

    resultados = await buscar_omdb(query_en)
    opciones = []
    for item in resultados:
        titulo   = item.get("Title", "Sin título")
        año      = item.get("Year", "????")
        imdb_id  = item.get("imdbID", "")
        emoji    = tipo_emoji(item.get("Type", ""))
        etiqueta = f"{emoji} {titulo} ({año})"[:100]
        opciones.append(disnake.OptionChoice(name=etiqueta, value=imdb_id))

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
    # Respuesta inmediata para evitar el error "La aplicación no respondió"
    await inter.response.send_message("✅ ¡Pedido enviado!", ephemeral=True)

    # `titulo` ya es el imdbID elegido desde el autocompletado
    imdb_id = titulo
    detalle = await detalle_omdb(imdb_id)

    if not detalle:
        await inter.channel.send(
            f"{inter.author.mention} ❌ No pude obtener los detalles del título. Intenta de nuevo."
        )
        return

    nombre   = detalle.get("Title",  "Desconocido")
    año      = detalle.get("Year",   "Desconocido")
    tipo     = detalle.get("Type",   "")
    genero   = detalle.get("Genre",  "N/A")
    sinopsis = detalle.get("Plot",   "Sin sinopsis disponible.")
    poster   = detalle.get("Poster", "N/A")
    emoji    = tipo_emoji(tipo)

    # ── Embed ────────────────────────────────────────────────────────────────
    embed = disnake.Embed(
        title=f"{emoji} {nombre} ({año})",
        url=f"https://www.imdb.com/title/{imdb_id}/",
        description=sinopsis,
        color=0x00d4ff,
    )
    embed.add_field(name="📅 Año",     value=año,     inline=True)
    embed.add_field(name="🎭 Género",  value=genero,  inline=True)
    embed.add_field(name="🆔 IMDB ID", value=imdb_id, inline=True)
    embed.set_footer(
        text=f"Pedido por {inter.author} • {inter.guild.name if inter.guild else 'DM'}",
        icon_url=inter.author.display_avatar.url,
    )
    if poster and poster != "N/A":
        embed.set_image(url=poster)

    await inter.channel.send(
        content=f"🔔 **{inter.author.mention} ha pedido:**",
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
