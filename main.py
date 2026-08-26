import os
import disnake
from disnake.ext import commands

from keep_alive import keep_alive
from utils.catalog import iniciar_refresco_periodico

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

intents = disnake.Intents.default()
bot = commands.InteractionBot(intents=intents)

# Cogs a cargar. Sumar acá cuando agreguemos comandos nuevos.
EXTENSIONS = [
    "cogs.pedir",
]

_catalogo_task_iniciada = False


@bot.event
async def on_ready():
    global _catalogo_task_iniciada
    print(f"[BOT] Listo como {bot.user} (id: {bot.user.id})")

    # on_ready puede dispararse mas de una vez (reconexiones), por eso el flag:
    # solo queremos UN loop de refresco corriendo, no uno nuevo por cada reconexion.
    if not _catalogo_task_iniciada:
        _catalogo_task_iniciada = True
        bot.loop.create_task(iniciar_refresco_periodico())


def cargar_extensiones():
    for ext in EXTENSIONS:
        try:
            bot.load_extension(ext)
            print(f"[BOT] Extension cargada: {ext}")
        except Exception as e:
            print(f"[BOT] Error cargando {ext}: {e}")
            raise


if __name__ == "__main__":
    # Mantiene vivo el servicio web gratuito de Render (evita el sleep por inactividad).
    # Ojo: esto NO evita bloqueos de red a nivel Discord/Cloudflare, solo el spin-down de Render.
    keep_alive()

    cargar_extensiones()

    # disnake ya reconecta solo ante caidas de gateway/timeouts.
    # Si esto lanza excepcion (token invalido, etc.) dejamos que Render reinicie el proceso
    # segun su politica de restart, en vez de reintentar a mano con el mismo objeto bot
    # (bot.run() no se puede llamar dos veces sobre la misma instancia: el loop queda cerrado).
    bot.run(DISCORD_TOKEN)
