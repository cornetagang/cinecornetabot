"""API interna para la web (la llama SOLO el Worker de Cloudflare, nunca el navegador).

Autenticacion: cabecera  Authorization: Bearer <BOT_API_SECRET>  (secret de Fly y del Worker).
El Worker es quien verifica la sesion de Firebase del usuario y manda aqui su uid ya verificado.
Sin BOT_API_SECRET la API queda apagada (503).
"""
import hmac
import os
import re
import time

from flask import Blueprint, jsonify, request

from utils import economia, limites, veintiuno, vinculos
from utils.economia import JuegoError

bp = Blueprint("api", __name__, url_prefix="/api")

_UID_RE = re.compile(r"^[A-Za-z0-9_-]{6,128}$")

# codigo de error -> estado HTTP (los de validacion son 400; los de estado del juego, 409)
_ESTADOS = {
    "apuesta": 400, "cantidad": 400, "codigo_invalido": 404, "codigo_vencido": 410,
    "saldo": 409, "tope": 409, "ya_vinculado": 409, "uid_vinculado": 409, "no_vinculado": 404,
    # el 21
    "apuesta_invalida": 400, "accion": 400, "sala_no_existe": 404, "no_participas": 403,
    "no_es_tu_turno": 409, "sin_cartas": 409, "ya_en_partida": 409, "sala_llena": 409,
    "tu_sala": 409, "sala_invalida": 409, "sin_codigos": 503, "demasiados_intentos": 429,
}


def _error(codigo: str, status: int | None = None, **datos):
    return jsonify({"error": codigo, **datos}), status or _ESTADOS.get(codigo, 400)


@bp.before_request
def _autenticar():
    secreto = os.environ.get("BOT_API_SECRET", "")
    if not secreto:
        return _error("api_apagada", 503)
    enviado = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(enviado.encode(), secreto.encode()):
        return _error("no_autorizado", 401)


def _uid_valido(valor) -> str | None:
    return valor if isinstance(valor, str) and _UID_RE.match(valor) else None


def _saldo_de(discord_id: int) -> dict:
    e = limites.estado(discord_id)
    return {**e, "maximo": economia.MAX_DISPONIBLES, "apuesta_max": economia.APUESTA_MAX,
            "renueva": limites.proximo_reset()}


@bp.post("/vincular")
def vincular():
    datos = request.get_json(silent=True) or {}
    uid = _uid_valido(datos.get("uid"))
    if not uid:
        return _error("uid_invalido")
    try:
        discord_id = vinculos.canjear(str(datos.get("codigo", "")), uid)
    except JuegoError as e:
        return _error(e.codigo)
    # La ID de Discord se devuelve como texto: en JavaScript los numeros de 64 bits pierden precision.
    return jsonify({"ok": True, "discord_id": str(discord_id)})


@bp.get("/saldo")
def saldo():
    uid = _uid_valido(request.args.get("uid"))
    if not uid:
        return _error("uid_invalido")
    discord_id = vinculos.discord_de(uid)
    if discord_id is None:
        return jsonify({"vinculado": False})
    return jsonify({"vinculado": True, "discord_id": str(discord_id), **_saldo_de(discord_id)})


@bp.post("/ruleta")
def ruleta():
    datos = request.get_json(silent=True) or {}
    uid = _uid_valido(datos.get("uid"))
    if not uid:
        return _error("uid_invalido")
    discord_id = vinculos.discord_de(uid)
    if discord_id is None:
        return _error("no_vinculado")
    cantidad = datos.get("cantidad")
    if not isinstance(cantidad, int) or isinstance(cantidad, bool):
        return _error("cantidad", maximo=economia.APUESTA_MAX)
    try:
        r = economia.jugar_casa(discord_id, str(datos.get("apuesta", "")), cantidad)
    except JuegoError as e:
        return _error(e.codigo, **e.datos)
    return jsonify({"ok": True, **r, "renueva": limites.proximo_reset()})


# ---------- el 21 (duelo 1v1 por codigo de sala) ----------
# Todas las rutas reciben el uid de Firebase ya verificado por el Worker y lo traducen a la ID de Discord.
# Las respuestas llevan `disponibles` para que la web actualice el saldo sin otra llamada.

_FALLOS: dict[int, list[float]] = {}   # discord_id -> momentos de codigos de sala incorrectos
_FALLOS_MAX = 8                        # intentos fallidos...
_FALLOS_VENTANA = 300                  # ...cada 5 minutos (memoria del proceso: es un freno, no una garantia)


def _fallos_recientes(discord_id: int) -> list[float]:
    ahora = time.time()
    lista = [t for t in _FALLOS.get(discord_id, []) if ahora - t < _FALLOS_VENTANA]
    if lista:
        _FALLOS[discord_id] = lista
    else:
        _FALLOS.pop(discord_id, None)
    return lista


def _jugador(datos) -> tuple[int | None, tuple | None]:
    """(discord_id, None) si la persona esta vinculada; si no, (None, respuesta de error)."""
    uid = _uid_valido(datos.get("uid"))
    if not uid:
        return None, _error("uid_invalido")
    discord_id = vinculos.discord_de(uid)
    if discord_id is None:
        return None, _error("no_vinculado")
    return discord_id, None


def _con_saldo(vista: dict, discord_id: int):
    return jsonify({"ok": True, **vista, "disponibles": limites.estado(discord_id).get("disponibles")})


@bp.post("/21/crear")
def v21_crear():
    datos = request.get_json(silent=True) or {}
    did, fallo = _jugador(datos)
    if fallo:
        return fallo
    apuesta = datos.get("apuesta")
    if not isinstance(apuesta, int) or isinstance(apuesta, bool):
        return _error("apuesta_invalida", maximo=veintiuno.APUESTA_MAX_21)
    try:
        sala = veintiuno.crear_sala(did, apuesta)
    except JuegoError as e:
        return _error(e.codigo, **e.datos)
    return jsonify({"ok": True, **sala})


@bp.post("/21/unirse")
def v21_unirse():
    datos = request.get_json(silent=True) or {}
    did, fallo = _jugador(datos)
    if fallo:
        return fallo
    if len(_fallos_recientes(did)) >= _FALLOS_MAX:
        return _error("demasiados_intentos", espera=_FALLOS_VENTANA)
    try:
        vista = veintiuno.unirse(did, str(datos.get("codigo", ""))[:8])
    except JuegoError as e:
        if e.codigo == "sala_no_existe":
            _FALLOS.setdefault(did, []).append(time.time())
        return _error(e.codigo, **e.datos)
    return _con_saldo(vista, did)


@bp.get("/21/estado")
def v21_estado():
    did, fallo = _jugador(request.args)
    if fallo:
        return fallo
    try:
        vista = veintiuno.estado(did, str(request.args.get("codigo", ""))[:8])
    except JuegoError as e:
        return _error(e.codigo, **e.datos)
    return _con_saldo(vista, did)


@bp.post("/21/accion")
def v21_accion():
    datos = request.get_json(silent=True) or {}
    did, fallo = _jugador(datos)
    if fallo:
        return fallo
    try:
        vista = veintiuno.accion(did, str(datos.get("codigo", ""))[:8], str(datos.get("accion", "")))
    except JuegoError as e:
        return _error(e.codigo, **e.datos)
    return _con_saldo(vista, did)


@bp.post("/21/cancelar")
def v21_cancelar():
    datos = request.get_json(silent=True) or {}
    did, fallo = _jugador(datos)
    if fallo:
        return fallo
    try:
        vista = veintiuno.cancelar(did, str(datos.get("codigo", ""))[:8])
    except JuegoError as e:
        return _error(e.codigo, **e.datos)
    return _con_saldo(vista, did)


@bp.get("/21/actual")
def v21_actual():
    """Codigo de la sala/partida activa de la persona (None si no tiene), para retomarla al recargar."""
    did, fallo = _jugador(request.args)
    if fallo:
        return fallo
    return jsonify({"ok": True, "codigo": veintiuno.sala_actual(did)})
