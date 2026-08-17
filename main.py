import os
import disnake
from disnake.ext import commands

from keep_alive import keep_alive

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

intents = disnake.Intents.default()
bot = commands.InteractionBot(intents=intents)

# Cogs a cargar. Sumar acá cuando agreguemos comandos nuevos.
EXTENSIONS = [
    "cogs.pedir",
]


@bot.event
async def on_ready():
    print(f"[BOT] Listo como {bot.user} (id: {bot.user.id})")


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
