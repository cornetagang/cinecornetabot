import os
import threading
import aiohttp
import disnake
from disnake.ext import commands
from flask import Flask

# ── Flask keep-alive (Simplificado para evitar errores de Cron-job) ──────────
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
TMDB_API_KEY   = os.environ["TMDB_API_KEY"]  # Usaremos la llave corta (4f325...)

TMDB_BASE      = "https://api.themoviedb.org/3"
TMDB_LANGUAGE  = "es-MX"

# ── Bot ──────────────────────────────────────────────────────────────────────
intents = disnake.Intents.default()
bot = commands.InteractionBot(intents=intents)

# ── Helpers TMDB (Optimizado para Autocompletado) ────────────────────────────
async def buscar_tmdb(busqueda: str) -> list[disnake.OptionChoice]:
    opciones = []
    
    # URL de Búsqueda: directa, usando search/multi para buscar películas y series a la vez
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&language=es-MX&query={busqueda}&include_adult=false"
    
    try:
        # Timeout corto para no hacer esperar a Discord
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Filtrar solo películas y series (el endpoint multi también devuelve personas)
                    resultados = [item for item in data.get("results", []) if item.get("media_type") in ("movie", "tv")]
                    
                    # Ordenar por popularidad y limitar a 25 (límite máximo de Discord)
                    resultados.sort(key=lambda x: x.get("popularity", 0), reverse=True)
                    resultados = resultados[:25]
                    
                    for item in resultados:
                        media_type = item.get("media_type")
                        titulo = item.get("title") or item.get("name") or "Sin título"
                        año = extraer_año(item, media_type)
                        emoji = "🎬" if media_type == "movie" else "📺"
                        
                        etiqueta = f"{emoji} {titulo} ({año})"[:100] # Max 100 caracteres para Discord
                        valor = f"{item['id']}|{media_type}"
                        
                        # Formato de salida: disnake.OptionChoice
                        opciones.append(disnake.OptionChoice(name=etiqueta, value=valor))
                else:
                    print(f"Error en búsqueda: La API de TMDB respondió con código {resp.status}")
                    
    except Exception as e:
        # Manejo de Errores: Para ver exactamente qué falla en la consola de Render
        print(f"Error en búsqueda: {e}")

    return opciones

def extraer_año(item: dict, media_type: str) -> str:
    fecha = item.get("release_date" if media_type == "movie" else "first_air_date", "")
    return fecha[:4] if fecha else "????"

# ── Autocompletado (Optimizado para evitar el error 40060) ───────────────────
@bot.event
async def on_application_command_autocomplete(inter: disnake.ApplicationCommandInteraction):
    if inter.data.name != "pedir": return
    
    busqueda = inter.filled_options.get("titulo", "")
    
    # Respuesta rápida: menos de 3 letras no busca nada
    if len(busqueda) < 3:
        await inter.response.autocomplete([])
        return

    # Llamamos a nuestra función que ya devuelve las opciones formateadas
    opciones = await buscar_tmdb(busqueda)
    await inter.response.autocomplete(opciones)

# ── Comando /pedir ────────────────────────────────────────────────────────────
@bot.slash_command(name="pedir", description="Pide una película o serie.")
async def pedir(
    inter: disnake.ApplicationCommandInteraction,
    titulo: str = commands.Param(description="Nombre de la película o serie")
):
    # Respuesta inmediata para evitar el "La aplicación no respondió"
    await inter.response.send_message("✅ ¡Procesando tu pedido!", ephemeral=True)

    try:
        tmdb_id, media_type = titulo.split("|")
        detalle = await detalle_tmdb(int(tmdb_id), media_type)
        if not detalle: raise Exception()
    except:
        await inter.channel.send(f"{inter.author.mention} ❌ Error al obtener detalles. Selecciona una opción de la lista.")
        return

    nombre = detalle.get("title") or detalle.get("name")
    año = extraer_año(detalle, media_type)
    generos = ", ".join(g["name"] for g in detalle.get("genres", []))
    puntuacion = detalle.get("vote_average", 0)
    poster = detalle.get("poster_path")
    
    embed = disnake.Embed(
        title=f"{'🎬' if media_type == 'movie' else '📺'} {nombre} ({año})",
        url=f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
        description=detalle.get("overview") or "Sin sinopsis.",
        color=0x00d4ff
    )
    embed.add_field(name="📅 Año", value=año, inline=True)
    embed.add_field(name="🎭 Género", value=generos or "N/A", inline=True)
    embed.add_field(name="⭐ Puntuación", value=f"{puntuacion:.1f}/10", inline=True)
    
    if poster:
        embed.set_image(url=f"https://image.tmdb.org/t/p/w500{poster}")

    await inter.channel.send(content=f"🗣️ {inter.author.mention} ha pedido:", embed=embed)

@bot.event
async def on_ready():
    print(f"✅ Bot listo: {bot.user}")

if __name__ == "__main__":
    keep_alive()
    bot.run(DISCORD_TOKEN)
