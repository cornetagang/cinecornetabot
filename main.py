import os
import aiohttp
import disnake
from disnake.ext import commands

# ── Configuración ────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.environ["DISCORD_TOKEN"]
OMDB_API_KEY   = os.environ["OMDB_API_KEY"]
CANAL_PEDIDOS  = int(os.environ.get("CANAL_PEDIDOS_ID", "0"))

OMDB_BASE      = "http://www.omdbapi.com/"

# ── Bot ──────────────────────────────────────────────────────────────────────
intents = disnake.Intents.default()
bot = commands.InteractionBot(intents=intents)


# ── Helpers OMDb ─────────────────────────────────────────────────────────────
async def buscar_omdb(query: str) -> list[dict]:
    """Busca títulos en OMDb con s= y devuelve hasta 10 resultados."""
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
    """Obtiene los detalles completos de un título por su imdbID."""
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
    """Emoji según el tipo de contenido de OMDb."""
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

    resultados = await buscar_omdb(input_usuario)
    opciones = []

    for item in resultados:
        titulo  = item.get("Title", "Sin título")
        año     = item.get("Year", "????")
        imdb_id = item.get("imdbID", "")
        emoji   = tipo_emoji(item.get("Type", ""))

        # Etiqueta visible en Discord (máx. 100 chars)
        etiqueta = f"{emoji} {titulo} ({año})"[:100]

        # Valor interno = imdbID, que recibirá el comando al ejecutarse
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
    await inter.response.defer(ephemeral=True)

    # `titulo` contiene el imdbID elegido desde el autocompletado
    imdb_id = titulo
    detalle = await detalle_omdb(imdb_id)

    if not detalle:
        await inter.followup.send(
            "❌ No pude obtener los detalles del título. Intenta de nuevo.",
            ephemeral=True,
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
        color=disnake.Color.gold(),
    )
    embed.add_field(name="📅 Año",     value=año,     inline=True)
    embed.add_field(name="🎭 Género",  value=genero,  inline=True)
    embed.add_field(name="🔑 IMDB ID", value=imdb_id, inline=True)
    embed.set_footer(
        text=(
            f"Pedido por {inter.author}"
            f" • {inter.guild.name if inter.guild else 'DM'}"
        ),
        icon_url=inter.author.display_avatar.url,
    )
    if poster and poster != "N/A":
        embed.set_thumbnail(url=poster)

    # ── Envío al canal de pedidos ─────────────────────────────────────────────
    canal = bot.get_channel(CANAL_PEDIDOS)
    if canal is None:
        try:
            canal = await bot.fetch_channel(CANAL_PEDIDOS)
        except Exception:
            await inter.followup.send(
                "❌ No encontré el canal de pedidos. "
                "Revisa `CANAL_PEDIDOS_ID` en las variables de entorno.",
                ephemeral=True,
            )
            return

    await canal.send(embed=embed)
    await inter.followup.send(
        f"✅ Tu pedido de **{nombre} ({año})** fue enviado correctamente.",
        ephemeral=True,
    )


# ── Eventos ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print(f"📡 Canal de pedidos configurado: {CANAL_PEDIDOS}")


# ── Inicio ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)