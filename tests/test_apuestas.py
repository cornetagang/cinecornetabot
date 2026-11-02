"""Pruebas de las apuestas. Ejecutar desde la raiz del proyecto:  python -m unittest discover -s tests -v"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "prueba.db")
os.environ["BOT_API_SECRET"] = "secreto-de-prueba"
os.environ.setdefault("TMDB_API_KEY", "x")
os.environ.setdefault("DISCORD_TOKEN", "x")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import economia, limites, vinculos  # noqa: E402
from utils.economia import JuegoError  # noqa: E402

A, B, C = 111, 222, 333
ROJO, NEGRO = 1, 2        # numeros: 1 es rojo, 2 es negro


def limpiar():
    c = sqlite3.connect(limites.DB_PATH)
    for t in ("pedidos", "movimientos", "duelos", "vinculos", "codigos_vinculo"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    c.close()


def suma_movimientos(uid):
    c = sqlite3.connect(limites.DB_PATH)
    r = c.execute("SELECT COALESCE(SUM(delta),0) FROM movimientos WHERE user_id=?", (uid,)).fetchone()[0]
    c.close()
    return r


class Base(unittest.TestCase):
    def setUp(self):
        limpiar()


class TestLimitesYMigracion(Base):
    def test_migracion_conserva_datos_de_la_base_vieja(self):
        ruta = os.path.join(_tmp, "vieja.db")
        c = sqlite3.connect(ruta)
        c.execute("CREATE TABLE pedidos (user_id INTEGER NOT NULL, semana TEXT NOT NULL, cantidad INTEGER NOT NULL, PRIMARY KEY (user_id, semana))")
        c.execute("INSERT INTO pedidos VALUES (?, ?, ?)", (A, limites._semana(), 2))
        c.commit(); c.close()
        original = limites.DB_PATH
        try:
            limites.DB_PATH = ruta
            limites._init()
            limites._init()                       # idempotente
            self.assertEqual(limites.estado(A), {"usados": 2, "ajuste": 0, "base": 3, "disponibles": 1})
        finally:
            limites.DB_PATH = original

    def test_pedidos_clasicos_siguen_igual(self):
        self.assertEqual(limites.disponibles(A), 3)
        for i in range(3):
            self.assertEqual(limites.consumir(A), (True, i + 1))
        self.assertEqual(limites.consumir(A), (False, 3))
        limites.devolver(A)
        self.assertEqual(limites.disponibles(A), 1)

    def test_premios_permiten_pedir_mas_alla_de_3(self):
        economia.mover_admin(A, +2, "regalo")
        for _ in range(5):
            self.assertTrue(limites.consumir(A)[0])
        self.assertFalse(limites.consumir(A)[0])

    def test_ajustar_no_pasa_de_lo_permitido(self):
        self.assertEqual(limites.ajustar(A, 99), 3)
        economia.mover_admin(B, +2, "regalo")
        self.assertEqual(limites.ajustar(B, 99), 5)
        self.assertEqual(limites.ajustar(B, -4), 0)


class TestCasa(Base):
    def test_gana_rojo_paga_1_a_1(self):
        r = economia.jugar_casa(A, "rojo", 2, _numero=ROJO)
        self.assertTrue(r["gano"]); self.assertEqual(r["delta"], 2); self.assertEqual(r["disponibles"], 5)
        self.assertEqual(limites.disponibles(A), 5)

    def test_pierde_y_el_cero_pierde_todo(self):
        r = economia.jugar_casa(A, "rojo", 2, _numero=NEGRO)
        self.assertFalse(r["gano"]); self.assertEqual(r["disponibles"], 1)
        for apuesta in ("rojo", "negro", "par", "impar", "bajo", "alto", "docena1", "docena2", "docena3"):
            limpiar()
            self.assertFalse(economia.jugar_casa(A, apuesta, 1, _numero=0)["gano"], apuesta)

    def test_docena_paga_2_a_1(self):
        r = economia.jugar_casa(A, "docena2", 1, _numero=14)
        self.assertEqual((r["delta"], r["disponibles"]), (2, 5))

    def test_cada_apuesta_cubre_lo_que_dice(self):
        casos = {"rojo": {1, 36}, "negro": {2, 35}, "par": {2, 36}, "impar": {1, 35}, "bajo": {1, 18}, "alto": {19, 36},
                 "docena1": {1, 12}, "docena2": {13, 24}, "docena3": {25, 36}}
        for apuesta, ganan in casos.items():
            for n in ganan:
                self.assertTrue(economia.APUESTAS[apuesta][0](n), (apuesta, n))
        self.assertEqual(sum(economia.APUESTAS["rojo"][0](n) for n in range(37)), 18)
        self.assertEqual(sum(economia.APUESTAS["negro"][0](n) for n in range(37)), 18)
        self.assertEqual(sum(economia.APUESTAS["par"][0](n) for n in range(37)), 18)
        self.assertEqual(sum(economia.APUESTAS["impar"][0](n) for n in range(37)), 18)
        self.assertEqual(sum(economia.APUESTAS["docena3"][0](n) for n in range(37)), 12)

    def test_validaciones(self):
        with self.assertRaises(JuegoError) as e: economia.jugar_casa(A, "numero17", 1)
        self.assertEqual(e.exception.codigo, "apuesta")
        for malo in (0, -1, economia.APUESTA_MAX + 1):
            with self.assertRaises(JuegoError) as e: economia.jugar_casa(A, "rojo", malo)
            self.assertEqual(e.exception.codigo, "cantidad")

    def test_sin_saldo(self):
        economia.jugar_casa(A, "rojo", 2, _numero=NEGRO)       # 3 -> 1
        with self.assertRaises(JuegoError) as e: economia.jugar_casa(A, "rojo", 2)
        self.assertEqual(e.exception.codigo, "saldo")
        self.assertEqual(limites.disponibles(A), 1)             # nada se cobro

    def test_el_tope_nunca_se_pasa_y_se_rechaza_antes_de_girar(self):
        economia.jugar_casa(A, "rojo", 2, _numero=ROJO)         # 3 -> 5
        antes = limites.disponibles(A)
        with self.assertRaises(JuegoError) as e: economia.jugar_casa(A, "rojo", 2)     # ganar daria 7 > 6
        self.assertEqual(e.exception.codigo, "tope")
        with self.assertRaises(JuegoError): economia.jugar_casa(A, "docena1", 1)       # 5 + 2 = 7 > 6
        self.assertEqual(limites.disponibles(A), antes)         # no se cobro nada
        r = economia.jugar_casa(A, "rojo", 1, _numero=ROJO)     # 5 + 1 = 6: justo el tope
        self.assertEqual(r["disponibles"], 6)

    def test_queda_registro_y_cuadra_con_el_saldo(self):
        economia.jugar_casa(A, "rojo", 2, _numero=ROJO)
        economia.jugar_casa(A, "negro", 1, _numero=ROJO)
        hist = economia.historial(A)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0][1], -1)                        # el mas reciente primero
        self.assertEqual(hist[0][4], limites.disponibles(A))
        self.assertEqual(suma_movimientos(A), limites.estado(A)["ajuste"])

    def test_concurrencia_nunca_deja_saldo_negativo_ni_descuadra(self):
        errores, ok = [], []

        def jugar():
            try:
                economia.jugar_casa(A, "rojo", 1, _numero=NEGRO)   # siempre pierde 1
                ok.append(1)
            except JuegoError:
                errores.append(1)

        hilos = [threading.Thread(target=jugar) for _ in range(40)]
        [h.start() for h in hilos]; [h.join() for h in hilos]
        self.assertEqual(len(ok), 3)                              # solo 3 apuestas de 1 caben
        self.assertEqual(len(errores), 37)
        self.assertEqual(limites.disponibles(A), 0)
        self.assertEqual(suma_movimientos(A), limites.estado(A)["ajuste"])

    def test_estadistica_de_la_ruleta_real(self):
        n = 120_000
        conteo = [0] * 37
        for _ in range(n):
            conteo[economia.girar()] += 1
        self.assertTrue(all(c > 0 for c in conteo))                       # salen los 37 numeros
        frec_rojo = sum(conteo[i] for i in economia.ROJOS) / n
        self.assertAlmostEqual(frec_rojo, 18 / 37, delta=0.008)           # ~48,65 %
        # ventaja de la casa: ~ -2,7 % por unidad apostada, tanto en 1:1 como en docenas
        for apuesta in ("rojo", "docena1"):
            gana, mult = economia.APUESTAS[apuesta]
            ev = sum(conteo[i] * (mult if gana(i) else -1) for i in range(37)) / n
            self.assertAlmostEqual(ev, -1 / 37, delta=0.02, msg=apuesta)


class TestDuelos(Base):
    def test_gana_el_retador_con_rojo_y_se_conserva_el_total(self):
        r = economia.jugar_duelo(A, B, 2, _numero=ROJO)
        self.assertEqual((r["ganador"], limites.disponibles(A), limites.disponibles(B)), (A, 5, 1))
        self.assertEqual(suma_movimientos(A) + suma_movimientos(B), 0)       # nada se crea ni se destruye

    def test_gana_el_retado_con_negro(self):
        r = economia.jugar_duelo(A, B, 1, _numero=NEGRO)
        self.assertEqual((r["ganador"], limites.disponibles(A), limites.disponibles(B)), (B, 2, 4))

    def test_el_cero_es_empate_y_no_mueve_nada(self):
        r = economia.jugar_duelo(A, B, 2, _numero=0)
        self.assertIsNone(r["ganador"])
        self.assertEqual((limites.disponibles(A), limites.disponibles(B)), (3, 3))
        self.assertEqual(economia.historial(A), [])

    def test_validaciones(self):
        casos = [((A, A, 1), "a_ti_mismo"), ((A, B, 0), "cantidad"), ((A, B, economia.DUELO_MAX + 1), "cantidad")]
        for args, codigo in casos:
            with self.assertRaises(JuegoError) as e: economia.jugar_duelo(*args)
            self.assertEqual(e.exception.codigo, codigo)

    def test_a_los_dos_les_tiene_que_alcanzar_y_caber(self):
        economia.jugar_casa(B, "rojo", 2, _numero=NEGRO)                    # B queda con 1
        with self.assertRaises(JuegoError) as e: economia.jugar_duelo(A, B, 2)
        self.assertEqual((e.exception.codigo, e.exception.datos["quien"]), ("saldo_duelo", B))
        limpiar()
        economia.jugar_casa(B, "rojo", 2, _numero=ROJO)                     # B queda con 5
        with self.assertRaises(JuegoError) as e: economia.jugar_duelo(A, B, 2)   # si B gana tendria 7 > 6
        self.assertEqual((e.exception.codigo, e.exception.datos["quien"]), ("tope_duelo", B))

    def test_limite_diario_entre_la_misma_pareja(self):
        for _ in range(economia.DUELOS_RIVAL_DIA):
            economia.jugar_duelo(A, B, 1, _numero=0)
        with self.assertRaises(JuegoError) as e: economia.jugar_duelo(B, A, 1)     # al reves cuenta igual
        self.assertEqual(e.exception.codigo, "limite_rival")
        economia.jugar_duelo(A, C, 1, _numero=0)                                    # con otra persona si puede

    def test_limite_diario_por_persona(self):
        otros = [1000 + i for i in range(economia.DUELOS_DIA)]
        for o in otros:
            economia.jugar_duelo(A, o, 1, _numero=0)
        with self.assertRaises(JuegoError) as e: economia.jugar_duelo(A, C, 1)
        self.assertEqual((e.exception.codigo, e.exception.datos["quien"]), ("limite_dia", A))

    def test_validar_duelo_no_toca_nada(self):
        economia.validar_duelo(A, B, 2)
        self.assertEqual(economia.historial(A), []); self.assertEqual(limites.disponibles(A), 3)

    def test_aceptaciones_simultaneas_no_duplican_el_cobro(self):
        resultados = []

        def aceptar():
            try:
                resultados.append(economia.jugar_duelo(A, B, 2, _numero=ROJO))
            except JuegoError as e:
                resultados.append(e.codigo)

        hilos = [threading.Thread(target=aceptar) for _ in range(10)]
        [h.start() for h in hilos]; [h.join() for h in hilos]
        self.assertEqual(sum(isinstance(r, dict) for r in resultados), 1)           # solo uno entra
        self.assertEqual(suma_movimientos(A) + suma_movimientos(B), 0)


class TestVinculos(Base):
    def test_flujo_completo_y_un_solo_uso(self):
        codigo, expira = vinculos.crear_codigo(A)
        self.assertEqual(len(codigo), 8)
        self.assertEqual(vinculos.canjear(codigo.lower(), "uid-aaaaaa"), A)          # tolera minusculas
        self.assertEqual((vinculos.discord_de("uid-aaaaaa"), vinculos.uid_de(A)), (A, "uid-aaaaaa"))
        with self.assertRaises(JuegoError) as e: vinculos.canjear(codigo, "uid-bbbbbb")
        self.assertEqual(e.exception.codigo, "codigo_invalido")

    def test_vencimiento(self):
        codigo, _ = vinculos.crear_codigo(A, ahora=1000)
        with self.assertRaises(JuegoError) as e: vinculos.canjear(codigo, "uid-aaaaaa", ahora=1000 + vinculos.CODIGO_VIDA + 1)
        self.assertEqual(e.exception.codigo, "codigo_vencido")

    def test_un_codigo_nuevo_anula_el_anterior(self):
        viejo, _ = vinculos.crear_codigo(A)
        nuevo, _ = vinculos.crear_codigo(A)
        with self.assertRaises(JuegoError): vinculos.canjear(viejo, "uid-aaaaaa")
        self.assertEqual(vinculos.canjear(nuevo, "uid-aaaaaa"), A)

    def test_uno_a_uno(self):
        c1, _ = vinculos.crear_codigo(A); vinculos.canjear(c1, "uid-aaaaaa")
        with self.assertRaises(JuegoError) as e: vinculos.crear_codigo(A)               # A ya esta vinculada
        self.assertEqual(e.exception.codigo, "ya_vinculado")
        c2, _ = vinculos.crear_codigo(B)
        with self.assertRaises(JuegoError) as e: vinculos.canjear(c2, "uid-aaaaaa")     # esa cuenta web ya es de A
        self.assertEqual(e.exception.codigo, "uid_vinculado")

    def test_desvincular(self):
        c, _ = vinculos.crear_codigo(A); vinculos.canjear(c, "uid-aaaaaa")
        self.assertTrue(vinculos.desvincular(A)); self.assertFalse(vinculos.desvincular(A))
        self.assertIsNone(vinculos.uid_de(A))

    def test_codigo_basura(self):
        for malo in ("", "XXXXXXXX", None, "   "):
            with self.assertRaises(JuegoError): vinculos.canjear(malo, "uid-aaaaaa")


class TestApi(Base):
    def setUp(self):
        super().setUp()
        from keep_alive import app
        self.cli = app.test_client()
        self.h = {"Authorization": "Bearer secreto-de-prueba"}

    def test_sin_clave_o_clave_mala(self):
        self.assertEqual(self.cli.get("/api/saldo?uid=uid-aaaaaa").status_code, 401)
        self.assertEqual(self.cli.get("/api/saldo?uid=uid-aaaaaa", headers={"Authorization": "Bearer mala"}).status_code, 401)

    def test_api_apagada_sin_secreto(self):
        os.environ["BOT_API_SECRET"] = ""
        try:
            self.assertEqual(self.cli.get("/api/saldo?uid=uid-aaaaaa", headers=self.h).status_code, 503)
        finally:
            os.environ["BOT_API_SECRET"] = "secreto-de-prueba"

    def test_flujo_web_completo(self):
        r = self.cli.get("/api/saldo?uid=uid-aaaaaa", headers=self.h)
        self.assertEqual(r.get_json(), {"vinculado": False})
        r = self.cli.post("/api/ruleta", json={"uid": "uid-aaaaaa", "apuesta": "rojo", "cantidad": 1}, headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["error"]), (404, "no_vinculado"))

        codigo, _ = vinculos.crear_codigo(A)
        r = self.cli.post("/api/vincular", json={"uid": "uid-aaaaaa", "codigo": codigo}, headers=self.h)
        self.assertEqual((r.status_code, r.get_json()["discord_id"]), (200, str(A)))     # la ID viaja como texto
        r = self.cli.get("/api/saldo?uid=uid-aaaaaa", headers=self.h).get_json()
        self.assertEqual((r["vinculado"], r["disponibles"], r["maximo"]), (True, 3, 6))

        r = self.cli.post("/api/ruleta", json={"uid": "uid-aaaaaa", "apuesta": "rojo", "cantidad": 1}, headers=self.h)
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(j["disponibles"], 3 + j["delta"])
        self.assertEqual(economia.historial(A)[0][1], j["delta"])                          # mismo saldo que en Discord

    def test_errores_de_la_api(self):
        codigo, _ = vinculos.crear_codigo(A); vinculos.canjear(codigo, "uid-aaaaaa")
        ruleta = lambda **d: self.cli.post("/api/ruleta", json={"uid": "uid-aaaaaa", **d}, headers=self.h)
        self.assertEqual(ruleta(apuesta="rojo", cantidad=99).get_json()["error"], "cantidad")
        self.assertEqual(ruleta(apuesta="rojo", cantidad="1").get_json()["error"], "cantidad")
        self.assertEqual(ruleta(apuesta="rojo", cantidad=True).get_json()["error"], "cantidad")
        self.assertEqual(ruleta(apuesta="numero7", cantidad=1).get_json()["error"], "apuesta")
        self.assertEqual(self.cli.post("/api/ruleta", json={"uid": "../x", "apuesta": "rojo", "cantidad": 1}, headers=self.h).status_code, 400)
        self.assertEqual(self.cli.post("/api/vincular", json={"uid": "uid-bbbbbb", "codigo": "NOEXISTE"}, headers=self.h).status_code, 404)


class TestDiscord(Base):
    """Carga los cogs de verdad y ejecuta los comandos con interacciones simuladas."""

    @classmethod
    def setUpClass(cls):
        import disnake
        from disnake.ext import commands
        cls.disnake = disnake
        cls.bot = commands.InteractionBot()
        for ext in ("cogs.pedir", "cogs.admin_pedidos", "cogs.ruleta"):
            cls.bot.load_extension(ext)
        cls.cog = cls.bot.get_cog("Ruleta")
        import cogs.ruleta as modulo
        modulo.PAUSA_GIRO = 0
        modulo.ESPERA_ENTRE_JUGADAS = 0
        cls.mod = modulo

    def inter(self, uid, nombre="Persona", dias_cuenta=400, dias_server=100):
        from datetime import datetime, timedelta, timezone
        ahora = datetime.now(timezone.utc)
        m = MagicMock(spec=self.disnake.Member)
        m.id, m.mention, m.display_name, m.bot = uid, f"<@{uid}>", nombre, False
        m.created_at = ahora - timedelta(days=dias_cuenta)
        m.joined_at = ahora - timedelta(days=dias_server)
        i = MagicMock()
        i.author = m
        i.response.send_message = AsyncMock(); i.response.edit_message = AsyncMock()
        i.edit_original_response = AsyncMock()
        return i

    def run_(self, coro):
        return asyncio.run(coro)

    def test_los_comandos_estan_registrados(self):
        nombres = {c.name for c in self.bot.slash_commands}
        self.assertTrue({"pedir", "gestionar", "ruleta", "duelo", "saldo", "historial", "vincular"} <= nombres, nombres)

    def test_ruleta_gana_o_pierde_y_el_mensaje_coincide_con_el_saldo(self):
        i = self.inter(A)
        self.run_(self.cog.ruleta.callback(self.cog, i, "rojo", 1))
        self.assertEqual(i.response.send_message.await_count, 1)
        final = i.edit_original_response.await_args.kwargs["embed"]
        self.assertIn(str(limites.disponibles(A)), final.footer.text)

    def test_ruleta_sin_saldo_responde_privado(self):
        economia.jugar_casa(A, "rojo", 2, _numero=NEGRO); economia.jugar_casa(A, "rojo", 1, _numero=NEGRO)
        i = self.inter(A)
        self.run_(self.cog.ruleta.callback(self.cog, i, "rojo", 1))
        self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"])
        self.assertIn("No te alcanza", i.response.send_message.await_args.args[0])

    def test_duelo_completo_con_boton(self):
        retador, retado = self.inter(A, "Ana"), self.inter(B, "Beto")
        self.run_(self.cog.duelo.callback(self.cog, retador, retado.author, 2))
        kw = retador.response.send_message.await_args.kwargs
        view = kw["view"]
        self.assertEqual(economia.historial(A), [])                              # retar no cobra nada

        async def aceptar():
            intruso = self.inter(C)
            await view.interaction_check(intruso)
            intruso.response.send_message.assert_awaited()                        # un tercero no puede tocar el duelo
            boton = self.inter(B)
            boton.author.id = B
            await view.aceptar.callback(boton)
            return boton
        boton = self.run_(aceptar())
        total = limites.disponibles(A) + limites.disponibles(B)
        self.assertEqual(total, 6)                                               # se conserva
        self.assertEqual(boton.edit_original_response.await_count, 1)

    def test_el_retador_no_puede_aceptar_su_propio_duelo(self):
        retador, retado = self.inter(A), self.inter(B)
        self.run_(self.cog.duelo.callback(self.cog, retador, retado.author, 1))
        view = retador.response.send_message.await_args.kwargs["view"]
        boton = self.inter(A)
        self.run_(view.aceptar.callback(boton))
        self.assertIn("Solo quien fue retado", boton.response.send_message.await_args.args[0])
        self.assertEqual(limites.disponibles(A), 3)

    def test_duelo_rechazado_no_mueve_nada(self):
        retador, retado = self.inter(A), self.inter(B)
        self.run_(self.cog.duelo.callback(self.cog, retador, retado.author, 1))
        view = retador.response.send_message.await_args.kwargs["view"]
        boton = self.inter(B)
        self.run_(view.rechazar.callback(boton))
        self.assertEqual((limites.disponibles(A), limites.disponibles(B)), (3, 3))

    def test_duelo_exige_antiguedad(self):
        nuevo = self.inter(A, dias_cuenta=2)
        self.run_(self.cog.duelo.callback(self.cog, nuevo, self.inter(B).author, 1))
        self.assertIn("muy nueva", nuevo.response.send_message.await_args.args[0])
        poco_server = self.inter(A, dias_server=1)
        self.run_(self.cog.duelo.callback(self.cog, poco_server, self.inter(B).author, 1))
        self.assertIn("muy poco en el servidor", poco_server.response.send_message.await_args.args[0])
        rival_nuevo = self.inter(B, "Nuevito", dias_cuenta=2).author
        i = self.inter(A)
        self.run_(self.cog.duelo.callback(self.cog, i, rival_nuevo, 1))
        self.assertIn("Nuevito", i.response.send_message.await_args.args[0])

    def test_duelo_en_mensaje_privado_se_rechaza(self):
        i = self.inter(A)
        i.guild = None
        self.run_(self.cog.duelo.callback(self.cog, i, self.inter(B).author, 1))
        self.assertIn("dentro del servidor", i.response.send_message.await_args.args[0])

    def test_duelo_solo_para_servidores(self):
        c = {x.name: x for x in self.bot.slash_commands}["duelo"]
        self.assertFalse(c.body.dm_permission)

    def test_duelo_contra_bot_o_uno_mismo(self):
        i = self.inter(A)
        yo = i.author
        self.run_(self.cog.duelo.callback(self.cog, i, yo, 1))
        self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"])
        bot = self.inter(B); bot.author.bot = True
        j = self.inter(A)
        self.run_(self.cog.duelo.callback(self.cog, j, bot.author, 1))
        self.assertTrue(j.response.send_message.await_args.kwargs["ephemeral"])

    def test_vincular_saldo_e_historial(self):
        i = self.inter(A)
        self.run_(self.cog.vincular.callback(self.cog, i))
        self.assertIn("Tu código es", i.response.send_message.await_args.args[0])
        j = self.inter(A)
        self.run_(self.cog.saldo.callback(self.cog, j))
        self.assertTrue(j.response.send_message.await_args.kwargs["ephemeral"])
        economia.jugar_casa(A, "rojo", 1, _numero=ROJO)
        k = self.inter(A)
        self.run_(self.cog.historial.callback(self.cog, k))
        self.assertIn("+1", k.response.send_message.await_args.args[0])

    def test_pedir_usa_el_saldo_con_premios(self):
        # Alguien con 5 disponibles (3 + 2 de premios) puede consumir 5 pedidos
        economia.mover_admin(A, +2, "prueba")
        self.assertEqual([limites.consumir(A)[0] for _ in range(6)], [True] * 5 + [False])


if __name__ == "__main__":
    unittest.main(verbosity=2)
