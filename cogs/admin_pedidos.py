import os
import disnake
from disnake.ext import commands

from utils import economia, limites, vinculos

# IDs de Discord de los admins, separados por coma, en la variable ADMIN_IDS.
# Ej: fly secrets set ADMIN_IDS="949026753723134094,111111111111111111"
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

SIN_MENCIONES = disnake.AllowedMentions.none()


class AdminPedidos(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    async def cog_slash_command_check(self, inter: disnake.ApplicationCommandInteraction) -> bool:
        if inter.author.id not in ADMIN_IDS:
            await inter.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
            return False
        return True

    @commands.slash_command(name="gestionar", description="Gestionar los pedidos semanales de la gente.")
    async def gestionar(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @gestionar.sub_command(name="ver", description="Ver cuantos pedidos le quedan a un usuario.")
    async def ver(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario a consultar"),
    ):
        e = limites.estado(usuario.id)
        await inter.response.send_message(
            f"{usuario.mention}: usó {e['usados']}, apuestas {e['ajuste']:+d}, "
            f"base {e['base']} → le quedan **{e['disponibles']}**.",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="devolver", description="Devolverle pedidos a un usuario.")
    async def devolver(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario al que le devuelves pedidos"),
        cantidad: int = commands.Param(default=1, ge=1, le=limites.LIMITE_SEMANAL,
                                       description="Cuantos pedidos devolver"),
    ):
        nuevo = limites.ajustar(usuario.id, limites.obtener(usuario.id) - cantidad)
        await inter.response.send_message(
            f"Listo: a {usuario.mention} ahora le quedan {limites.disponibles(usuario.id)} "
            f"({nuevo} usados).",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="quitar", description="Descontarle pedidos a un usuario.")
    async def quitar(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario al que le descuentas pedidos"),
        cantidad: int = commands.Param(default=1, ge=1, le=limites.LIMITE_SEMANAL,
                                       description="Cuantos pedidos descontar"),
    ):
        nuevo = limites.ajustar(usuario.id, limites.obtener(usuario.id) + cantidad)
        await inter.response.send_message(
            f"Listo: a {usuario.mention} ahora le quedan {limites.disponibles(usuario.id)} "
            f"({nuevo} usados).",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="reset", description="Reiniciar los pedidos de un usuario (vuelve a tener todos).")
    async def reset(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario a reiniciar"),
    ):
        limites.ajustar(usuario.id, 0)
        await inter.response.send_message(
            f"Pedidos de {usuario.mention} reiniciados: tiene {limites.disponibles(usuario.id)} disponibles.",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="resumen", description="Quien ha pedido esta semana y cuantos le quedan.")
    async def resumen(self, inter: disnake.ApplicationCommandInteraction):
        filas = limites.listar_estado()
        if not filas:
            await inter.response.send_message("Nadie ha pedido esta semana.", ephemeral=True)
            return

        lineas = [
            f"<@{uid}>: {usados} usados, apuestas {ajuste:+d} (quedan {disp})"
            for uid, usados, ajuste, disp in filas
        ]
        texto = "\n".join(lineas)
        if len(texto) > 1900:
            texto = texto[:1900] + "\n..."
        reset = limites.proximo_reset()
        await inter.response.send_message(
            f"**Pedidos de esta semana** (se renuevan <t:{reset}:R>)\n{texto}",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="historial", description="Ver las ultimas jugadas de un usuario (apuestas y ajustes).")
    async def historial(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario a consultar"),
    ):
        filas = economia.historial(usuario.id, 15)
        if not filas:
            await inter.response.send_message("Ese usuario no tiene jugadas.", ephemeral=True)
            return
        lineas = [f"<t:{ts}:R> · **{delta:+d}** · {tipo} · {detalle} · saldo {saldo}"
                  for ts, delta, tipo, detalle, saldo in filas]
        await inter.response.send_message(
            f"**Movimientos de {usuario.mention}**\n" + "\n".join(lineas),
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="desvincular", description="Quitar el vinculo de un usuario con la web.")
    async def desvincular(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario a desvincular"),
    ):
        hubo = vinculos.desvincular(usuario.id)
        await inter.response.send_message(
            f"{usuario.mention} desvinculado de la web." if hubo else f"{usuario.mention} no tenía vínculo.",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )


def setup(bot: commands.InteractionBot):
    bot.add_cog(AdminPedidos(bot))