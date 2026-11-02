import os
import disnake
from disnake.ext import commands

from utils import limites

# IDs de Discord de los admins, separados por coma, en la variable ADMIN_IDS.
# Ej: fly secrets set ADMIN_IDS="949026753723134094,111111111111111111"
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

SIN_MENCIONES = disnake.AllowedMentions.none()


def _restantes(usados: int) -> int:
    return max(0, limites.limite_semanal() - usados)


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

    @gestionar.sub_command(name="limite", description="Ver o cambiar el limite de pedidos por semana.")
    async def limite(
        self,
        inter: disnake.ApplicationCommandInteraction,
        cantidad: int = commands.Param(
            default=None, ge=1, le=limites.MAX_LIMITE,
            description="Nuevo limite semanal (si lo dejas vacio, solo muestra el actual)",
        ),
    ):
        if cantidad is None:
            await inter.response.send_message(
                f"El limite actual es de **{limites.limite_semanal()}** pedidos por semana.",
                ephemeral=True,
            )
            return

        anterior = limites.limite_semanal()
        nuevo = limites.fijar_limite(cantidad)
        await inter.response.send_message(
            f"Limite semanal cambiado: **{anterior}** → **{nuevo}** pedidos por persona. "
            "Se aplica de inmediato, y los pedidos ya usados esta semana se mantienen.",
            ephemeral=True,
        )

    @gestionar.sub_command(name="ver", description="Ver cuantos pedidos le quedan a un usuario.")
    async def ver(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario a consultar"),
    ):
        usados = limites.obtener(usuario.id)
        await inter.response.send_message(
            f"{usuario.mention}: usó {usados}/{limites.limite_semanal()}, le quedan {_restantes(usados)}.",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="devolver", description="Devolverle pedidos a un usuario.")
    async def devolver(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario al que le devuelves pedidos"),
        cantidad: int = commands.Param(default=1, ge=1, le=limites.MAX_LIMITE,
                                       description="Cuantos pedidos devolver"),
    ):
        nuevo = limites.ajustar(usuario.id, limites.obtener(usuario.id) - cantidad)
        await inter.response.send_message(
            f"Listo: a {usuario.mention} ahora le quedan {_restantes(nuevo)} "
            f"({nuevo}/{limites.limite_semanal()} usados).",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="quitar", description="Descontarle pedidos a un usuario.")
    async def quitar(
        self,
        inter: disnake.ApplicationCommandInteraction,
        usuario: disnake.User = commands.Param(description="Usuario al que le descuentas pedidos"),
        cantidad: int = commands.Param(default=1, ge=1, le=limites.MAX_LIMITE,
                                       description="Cuantos pedidos descontar"),
    ):
        actual = limites.obtener(usuario.id)
        # No pasa del limite, pero tampoco le baja el uso si ya estaba por encima.
        objetivo = max(actual, min(actual + cantidad, limites.limite_semanal()))
        nuevo = limites.ajustar(usuario.id, objetivo)
        await inter.response.send_message(
            f"Listo: a {usuario.mention} ahora le quedan {_restantes(nuevo)} "
            f"({nuevo}/{limites.limite_semanal()} usados).",
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
        limite = limites.limite_semanal()
        await inter.response.send_message(
            f"Pedidos de {usuario.mention} reiniciados: tiene {limite}/{limite} disponibles.",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )

    @gestionar.sub_command(name="resumen", description="Quien ha pedido esta semana y cuantos le quedan.")
    async def resumen(self, inter: disnake.ApplicationCommandInteraction):
        filas = limites.listar()
        if not filas:
            await inter.response.send_message("Nadie ha pedido esta semana.", ephemeral=True)
            return

        limite = limites.limite_semanal()
        lineas = [
            f"<@{uid}>: {usados}/{limite} usados (quedan {max(0, limite - usados)})"
            for uid, usados in filas
        ]
        texto = "\n".join(lineas)
        if len(texto) > 1900:
            texto = texto[:1900] + "\n..."
        reset = limites.proximo_reset()
        await inter.response.send_message(
            f"**Pedidos de esta semana** (límite: {limite}, se renuevan <t:{reset}:R>)\n{texto}",
            ephemeral=True,
            allowed_mentions=SIN_MENCIONES,
        )


def setup(bot: commands.InteractionBot):
    bot.add_cog(AdminPedidos(bot))
