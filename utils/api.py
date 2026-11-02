"""API interna para la web (la llama SOLO el Worker de Cloudflare, nunca el navegador).

Autenticacion: cabecera  Authorization: Bearer <BOT_API_SECRET>  (secret de Fly y del Worker).
El Worker es quien verifica la sesion de Firebase del usuario y manda aqui su uid ya verificado.
Sin BOT_API_SECRET la API queda apagada (503).
"""
import hmac
import os
import re

from flask import Blueprint, jsonify, request

from utils import economia, limites, vinculos
from utils.economia import JuegoError

bp = Blueprint("api", __name__, url_prefix="/api")

_UID_RE = re.compile(r"^[A-Za-z0-9_-]{6,128}$")

# codigo de error -> estado HTTP (los de validacion son 400; los de estado del juego, 409)
_ESTADOS = {
    "apuesta": 400, "cantidad": 400, "codigo_invalido": 404, "codigo_vencido": 410,
    "saldo": 409, "tope": 409, "ya_vinculado": 409, "uid_vinculado": 409, "no_vinculado": 404,
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
