import asyncio
import time
from datetime import datetime, timezone

import disnake
from disnake.ext import commands

from utils import economia, limites, vinculos
from utils.economia import JuegoError

# Reglas para retar (solo PvP): evitan cuentas recien creadas usadas para pasarse pedidos.
CUENTA_MIN_DIAS = economia._env_int("CUENTA_MIN_DIAS", 30)    # antiguedad de la cuenta de Discord
MIEMBRO_MIN_DIAS = economia._env_int("MIEMBRO_MIN_DIAS", 7)   # antiguedad en este servidor

PAUSA_GIRO = 2.5      # segundos de "suspenso" antes de mostrar el resultado
ESPERA_ENTRE_JUGADAS = 4

EMOJI_COLOR = {"rojo": "🔴", "negro": "⚫", "verde": "🟢"}

# etiqueta del menu -> valor interno. "x2" = recibes el doble de lo apostado; "x3" = el triple.
OPCIONES = {
    "🔴 Rojo (x2)": "rojo",
    "⚫ Negro (x2)": "negro",
    "Par (x2)": "par",
    "Impar (x2)": "impar",
    "Bajo 1-18 (x2)": "bajo",
    "Alto 19-36 (x2)": "alto",
    "1ª docena 1-12 (x3)": "docena1",
    "2ª docena 13-24 (x3)": "docena2",
    "3ª docena 25-36 (x3)": "docena3",
}
NOMBRE_APUESTA = {valor: etiqueta.split(" (")[0] for etiqueta, valor in OPCIONES.items()}

SIN_MENCIONES = disnake.AllowedMentions.none()


def texto_error(e: JuegoError) -> str:
    d, c = e.datos, e.codigo
    if c == "saldo":
        return f"No te alcanza: te quedan {d.get('disponibles', 0)} pedido(s) esta semana."
    if c == "tope":
        return (f"Con lo que tienes ahora, el premio pasaría el máximo de {d['maximo']} pedidos "
                "disponibles. Baja la apuesta.")
    if c == "cantidad":
        return f"La apuesta va de 1 a {d['maximo']} pedido(s)."
    if c == "apuesta":
        return "Esa apuesta no existe."
    if c == "a_ti_mismo":
        return "No puedes retarte a ti mismo."
    if c == "saldo_duelo":
        return f"<@{d['quien']}> no tiene pedidos suficientes para esta apuesta (le quedan {d['disponibles']})."
    if c == "tope_duelo":
        return f"<@{d['quien']}> pasaría el máximo de {d['maximo']} pedidos si gana. Baja la apuesta."
    if c == "limite_rival":
        return f"Ya hicieron {d['maximo']} duelos hoy entre ustedes dos. Mañana pueden seguir."
    if c == "limite_dia":
        return f"<@{d['quien']}> ya hizo {d['maximo']} duelos hoy."
    if c == "ya_vinculado":
        return "Tu cuenta de Discord ya está vinculada con la web. Si necesitas cambiarla, pídeselo a un admin."
    return "No se pudo completar la jugada."


def _no_elegible(miembro: disnake.Member, propio: bool) -> str | None:
    """Motivo por el que no puede participar en duelos (None = puede)."""
    ahora = datetime.now(timezone.utc)
    quien = "Tu cuenta" if propio else f"La cuenta de {miembro.display_name}"
    if (ahora - miembro.created_at).days < CUENTA_MIN_DIAS:
        return f"{quien} de Discord es muy nueva: los duelos piden al menos {CUENTA_MIN_DIAS} días de antigüedad."
    if miembro.joined_at and (ahora - miembro.joined_at).days < MIEMBRO_MIN_DIAS:
        sujeto = "Llevas" if propio else f"{miembro.display_name} lleva"
        return f"{sujeto} muy poco en el servidor: los duelos piden al menos {MIEMBRO_MIN_DIAS} días."
    return None


def _embed_casa(r: dict | None, autor: disnake.abc.User, apuesta: str, cantidad: int) -> disnake.Embed:
    nombre = NOMBRE_APUESTA[apuesta]
    if r is None:
        return disnake.Embed(
            title="🎰 Ruleta",
            description=f"{autor.mention} apuesta **{cantidad}** a **{nombre}**\n\n🎡 La bola está girando...",
            color=0xF1C40F,
        )
    emoji = EMOJI_COLOR[r["color"]]
    if r["gano"]:
        linea = f"🎉 **¡Ganaste!** +{r['delta']} pedido(s)"
        color = 0x2ECC71
    else:
        linea = f"💸 Perdiste **{cantidad}** pedido(s)"
        color = 0xE74C3C
    embed = disnake.Embed(
        title="🎰 Ruleta",
        description=(f"{autor.mention} apostó **{cantidad}** a **{nombre}**\n\n"
                     f"{emoji} Salió el **{r['numero']}** ({r['color']})\n{linea}"),
        color=color,
    )
    embed.set_footer(text=f"Te quedan {r['disponibles']} pedidos esta semana (máximo {economia.MAX_DISPONIBLES})")
    return embed


def _embed_duelo(retador: int, retado: int, cantidad: int, r: dict | None) -> disnake.Embed:
    encabezado = f"🔴 <@{retador}>  vs  ⚫ <@{retado}>  ·  **{cantidad}** pedido(s)"
    if r is None:
        return disnake.Embed(title="⚔️ Duelo", description=f"{encabezado}\n\n🎡 La bola está girando...", color=0xF1C40F)
    emoji = EMOJI_COLOR[r["color"]]
    if r["ganador"] is None:
        resultado, color = "🟢 **¡Cayó el 0!** Empate: nadie pierde ni gana nada.", 0x95A5A6
    else:
        perdedor = retado if r["ganador"] == retador else retador
        resultado = f"🏆 <@{r['ganador']}> gana **+{cantidad}**  ·  <@{perdedor}> pierde **{cantidad}**"
        color = 0x2ECC71
    embed = disnake.Embed(
        title="⚔️ Duelo",
        description=f"{encabezado}\n\n{emoji} Salió el **{r['numero']}** ({r['color']})\n{resultado}",
        color=color,
    )
    embed.set_footer(text="Rojo gana el retador · Negro gana el retado · Verde empate")
    return embed


class DueloView(disnake.ui.View):
    """Aceptar / Rechazar. El saldo se mueve SOLO si aceptan, y todo en una transaccion."""

    def __init__(self, inter: disnake.ApplicationCommandInteraction, retado: disnake.Member, cantidad: int):
        super().__init__(timeout=90)
        self.inter = inter
        self.retador = inter.author
        self.retado = retado
        self.cantidad = cantidad

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id not in (self.retador.id, self.retado.id):
            await inter.response.send_message("Este duelo no es tuyo.", ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Aceptar duelo", style=disnake.ButtonStyle.green, emoji="⚔️")
    async def aceptar(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if inter.author.id != self.retado.id:
            await inter.response.send_message("Solo quien fue retado puede aceptar.", ephemeral=True)
            return
        self.stop()
        try:
            r = await asyncio.to_thread(economia.jugar_duelo, self.retador.id, self.retado.id, self.cantidad)
        except JuegoError as e:
            await inter.response.edit_message(
                content=f"{texto_error(e)}\nEl duelo se canceló y no se movió ningún pedido.",
                embed=None, view=None, allowed_mentions=SIN_MENCIONES,
            )
            return
        # El resultado ya esta decidido y guardado; lo que sigue es solo mostrarlo.
        await inter.response.edit_message(
            content=None, embed=_embed_duelo(self.retador.id, self.retado.id, self.cantidad, None), view=None
        )
        await asyncio.sleep(PAUSA_GIRO)
        try:
            await inter.edit_original_response(
                embed=_embed_duelo(self.retador.id, self.retado.id, self.cantidad, r)
            )
        except disnake.HTTPException:
            pass

    @disnake.ui.button(label="Rechazar", style=disnake.ButtonStyle.red, emoji="✖️")
    async def rechazar(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        self.stop()
        quien = "canceló el reto" if inter.author.id == self.retador.id else "rechazó el reto"
        await inter.response.edit_message(
            content=f"✖️ {inter.author.mention} {quien}. No se movió ningún pedido.",
            embed=None, view=None, allowed_mentions=SIN_MENCIONES,
        )

    async def on_timeout(self):
        try:
            await self.inter.edit_original_response(
                content="⏰ El reto expiró sin respuesta. No se movió ningún pedido.", view=None
            )
        except disnake.HTTPException:
            pass


class Ruleta(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self._ultima_jugada: dict[int, float] = {}

    def _espera(self, user_id: int) -> int:
        """Segundos que faltan para poder jugar de nuevo (0 = puede). Frena el spam."""
        ahora = time.monotonic()
        falta = self._ultima_jugada.get(user_id, 0) + ESPERA_ENTRE_JUGADAS - ahora
        if falta > 0:
            return int(falta) + 1
        self._ultima_jugada[user_id] = ahora
        return 0

    # ---------- contra la casa ----------

    @commands.slash_command(name="ruleta", description="Apuesta tus pedidos contra la casa.")
    async def ruleta(
        self,
        inter: disnake.ApplicationCommandInteraction,
        apuesta: str = commands.Param(description="A qué le apuestas", choices=OPCIONES),
        cantidad: int = commands.Param(description="Cuántos pedidos apuestas", ge=1, le=economia.APUESTA_MAX),
    ):
        falta = self._espera(inter.author.id)
        if falta:
            await inter.response.send_message(f"Espera {falta} s antes de volver a jugar.", ephemeral=True)
            return
        try:
            r = await asyncio.to_thread(economia.jugar_casa, inter.author.id, apuesta, cantidad)
        except JuegoError as e:
            self._ultima_jugada.pop(inter.author.id, None)
            await inter.response.send_message(texto_error(e), ephemeral=True)
            return

        # Resultado ya guardado: se muestra con un poco de suspenso.
        await inter.response.send_message(embed=_embed_casa(None, inter.author, apuesta, cantidad))
        await asyncio.sleep(PAUSA_GIRO)
        try:
            await inter.edit_original_response(embed=_embed_casa(r, inter.author, apuesta, cantidad))
        except disnake.HTTPException:
            pass

    # ---------- contra jugadores ----------

    @commands.slash_command(
        name="duelo",
        description="Reta a alguien: apuestan pedidos y uno se lleva todo.",
        dm_permission=False,   # solo en servidores: hace falta saber desde cuando es miembro cada uno
    )
    async def duelo(
        self,
        inter: disnake.ApplicationCommandInteraction,
        rival: disnake.Member = commands.Param(description="A quién retas"),
        cantidad: int = commands.Param(description="Cuántos pedidos apuesta cada uno", ge=1, le=economia.DUELO_MAX),
    ):
        if inter.guild is None or not isinstance(inter.author, disnake.Member):
            await inter.response.send_message("Los duelos solo funcionan dentro del servidor.", ephemeral=True)
            return
        if rival.bot or rival.id == inter.author.id:
            await inter.response.send_message("Elige a otra persona (no a ti ni a un bot).", ephemeral=True)
            return
        motivo = _no_elegible(inter.author, True) or _no_elegible(rival, False)
        if motivo:
            await inter.response.send_message(motivo, ephemeral=True)
            return
        try:
            await asyncio.to_thread(economia.validar_duelo, inter.author.id, rival.id, cantidad)
        except JuegoError as e:
            await inter.response.send_message(texto_error(e), ephemeral=True, allowed_mentions=SIN_MENCIONES)
            return

        view = DueloView(inter, rival, cantidad)
        await inter.response.send_message(
            content=(f"⚔️ {inter.author.mention} reta a {rival.mention} a un duelo por **{cantidad}** pedido(s) cada uno.\n"
                     f"El que gane se lleva el pozo. {rival.mention}, ¿aceptas?"),
            view=view,
            allowed_mentions=disnake.AllowedMentions(users=[rival]),
        )

    # ---------- consulta ----------

    @commands.slash_command(name="saldo", description="Mira cuántos pedidos tienes esta semana.")
    async def saldo(self, inter: disnake.ApplicationCommandInteraction):
        e = limites.estado(inter.author.id)
        premios = f"{e['ajuste']:+d}" if e["ajuste"] else "0"
        embed = disnake.Embed(title="🎟️ Tus pedidos", color=0x00D4FF)
        embed.add_field(name="Disponibles", value=f"**{e['disponibles']}**", inline=True)
        embed.add_field(name="Base semanal", value=str(e["base"]), inline=True)
        embed.add_field(name="Apuestas (+/-)", value=premios, inline=True)
        embed.add_field(name="Usados en /pedir", value=str(e["usados"]), inline=True)
        embed.add_field(name="Máximo", value=str(economia.MAX_DISPONIBLES), inline=True)
        embed.add_field(name="Renueva", value=f"<t:{limites.proximo_reset()}:R>", inline=True)
        embed.set_footer(text="Web: " + ("vinculada ✅" if vinculos.uid_de(inter.author.id) else "sin vincular (usa /vincular)"))
        await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command(name="historial", description="Tus últimas jugadas.")
    async def historial(self, inter: disnake.ApplicationCommandInteraction):
        filas = economia.historial(inter.author.id, 10)
        if not filas:
            await inter.response.send_message("Todavía no tienes jugadas.", ephemeral=True)
            return
        lineas = [f"<t:{ts}:R> · **{delta:+d}** · {tipo} · {detalle} · saldo {saldo}"
                  for ts, delta, tipo, detalle, saldo in filas]
        await inter.response.send_message("**Tus últimos movimientos**\n" + "\n".join(lineas), ephemeral=True)

    @commands.slash_command(name="vincular", description="Vincula tu cuenta de Discord con la web.")
    async def vincular(self, inter: disnake.ApplicationCommandInteraction):
        try:
            codigo, expira = await asyncio.to_thread(vinculos.crear_codigo, inter.author.id)
        except JuegoError as e:
            await inter.response.send_message(texto_error(e), ephemeral=True)
            return
        await inter.response.send_message(
            f"Tu código es **`{codigo}`** (vence <t:{expira}:R>, sirve una sola vez).\n"
            "Pégalo en la ruleta de la web, con tu sesión iniciada, para vincular tu cuenta. "
            "No lo compartas con nadie.",
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot):
    bot.add_cog(Ruleta(bot))
