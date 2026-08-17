import disnake
from disnake.ext import commands

from utils.tmdb import buscar_tmdb, detalle_tmdb, extraer_año


class Pedir(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    async def autocomplete_titulo(
        self,
        inter: disnake.ApplicationCommandInteraction,
        input: str,
    ) -> list[disnake.OptionChoice]:
        return await buscar_tmdb(input)

    @commands.slash_command(name="pedir", description="Pide una pelicula o serie.")
    async def pedir(
        self,
        inter: disnake.ApplicationCommandInteraction,
        titulo: str = commands.Param(
            description="Nombre de la pelicula o serie",
            autocomplete=autocomplete_titulo,
        ),
        idioma: str = commands.Param(
            description="En que idioma la quieres ver?",
            choices=["Latino", "Subtitulada", "Indiferente"],
        ),
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

        nombre = detalle.get("title") or detalle.get("name")
        anio = extraer_año(detalle, media_type)
        generos = ", ".join(g["name"] for g in detalle.get("genres", []))
        puntuacion = detalle.get("vote_average", 0)
        poster = detalle.get("poster_path")
        emoji = "🎬" if media_type == "movie" else "📺"

        texto_idioma = {
            "Latino": "en [Latino]",
            "Subtitulada": "[Subtitulada]",
        }.get(idioma, "[Idioma Indiferente]")

        embed = disnake.Embed(
            title=f"{emoji} {nombre} ({anio})",
            url=f"https://www.themoviedb.org/{media_type}/{tmdb_id_str}",
            description=detalle.get("overview") or "Sin sinopsis.",
            color=0x00D4FF,
        )
        embed.add_field(name="Año", value=anio, inline=True)
        embed.add_field(name="Genero", value=generos or "N/A", inline=True)
        embed.add_field(name="Puntuacion", value=f"{puntuacion:.1f}/10", inline=True)

        if poster:
            embed.set_image(url=f"https://image.tmdb.org/t/p/w500{poster}")

        await inter.channel.send(
            content=f"{inter.author.mention} ha pedido: {emoji} {nombre} ({anio}) {texto_idioma}",
            embed=embed,
        )


def setup(bot: commands.InteractionBot):
    bot.add_cog(Pedir(bot))
