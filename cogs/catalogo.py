import os
import disnake
from disnake.ext import commands

from utils.catalog import catalogo, slugify

SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://cornetagang.github.io/cinecorneta/"
)


class Catalogo(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        name="catalogo",
        description="Busca si una pelicula o serie ya esta en Cine Corneta.",
    )
    async def catalogo_cmd(
        self,
        inter: disnake.ApplicationCommandInteraction,
        titulo: str = commands.Param(
            description="Nombre de la pelicula o serie",
        ),
    ):
        try:
            tipo, slug = titulo.split("|", 1)
        except ValueError:
            await inter.response.send_message(
                "Por favor selecciona una opcion de la lista desplegable.",
                ephemeral=True,
            )
            return

        item = next(
            (
                i
                for i in catalogo.items
                if i["tipo"] == tipo and slugify(i["titulo_original"]) == slug
            ),
            None,
        )

        if not item:
            await inter.response.send_message(
                "No encontré ese título, probá buscando de nuevo desde el "
                "autocompletado.",
                ephemeral=True,
            )
            return

        tipo_url = "pelicula" if item["tipo"] == "movie" else "serie"
        link = f"{SITE_BASE_URL}#{tipo_url}/{slug}"
        emoji = "🎬" if item["tipo"] == "movie" else "📺"

        embed = disnake.Embed(
            title=f"{emoji} {item['titulo_es']}",
            url=link,
            description=item.get("synopsis") or "Sin sinopsis.",
            color=0x00D4FF,
        )
        if item.get("anio"):
            embed.add_field(name="Año", value=str(item["anio"]), inline=True)
        if item.get("poster"):
            embed.set_image(url=item["poster"])

        await inter.response.send_message(embed=embed)

    @catalogo_cmd.autocomplete("titulo")
    async def autocomplete_catalogo(
        self,
        inter: disnake.ApplicationCommandInteraction,
        input: str,
    ) -> list[disnake.OptionChoice]:
        if len(input) < 2:
            return []

        resultados = catalogo.buscar(input, limite=25)
        opciones = []
        for item in resultados:
            emoji = "🎬" if item["tipo"] == "movie" else "📺"
            anio = f" ({item['anio']})" if item.get("anio") else ""
            etiqueta = f"{emoji} {item['titulo_es']}{anio}"[:100]
            valor = f"{item['tipo']}|{slugify(item['titulo_original'])}"[:100]
            opciones.append(disnake.OptionChoice(name=etiqueta, value=valor))
        return opciones


def setup(bot: commands.InteractionBot):
    bot.add_cog(Catalogo(bot))
