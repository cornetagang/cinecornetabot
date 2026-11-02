# Apuestas con pedidos — guía rápida

## Comandos nuevos (Discord)
| Comando | Qué hace |
|---|---|
| `/ruleta apuesta cantidad` | Apuesta contra la casa. Rojo/negro/par/impar/bajo/alto pagan x2; docenas x3. |
| `/duelo rival cantidad` | Reta a alguien. Si acepta, el servidor tira: rojo gana el retador, negro el retado, 0 empate. |
| `/saldo` | Tus pedidos: disponibles, premios, usados, máximo, cuándo se renuevan. |
| `/historial` | Tus últimos 10 movimientos. |
| `/vincular` | Código de un solo uso (10 min) para unir tu cuenta de Discord con la web. |
| `/gestionar historial` · `/gestionar desvincular` | Solo admins. |

## Reglas (valores por defecto; todos cambiables con variables de entorno en Fly)
- Pedidos disponibles = **3 base + premios − usados**. Todo se reinicia solo cada lunes 00:00 (Chile).
- Tope de **6 disponibles** (`MAX_DISPONIBLES`). Una apuesta que podría pasar ese tope se rechaza *antes* de girar.
- Máximo **2** por giro (`APUESTA_MAX`) y **2** por duelo (`DUELO_MAX`).
- Duelos: **2 al día** entre la misma pareja y **6 al día** por persona; cuenta de Discord con **30+ días** y **7+ días** en el servidor.
- Ruleta europea (0-36): el rojo sale 18/37 (48,6 %), ventaja de la casa 2,7 %. Sin apuestas a número suelto (pagan 35:1: inabsorbible con 3 pedidos a la semana).
- El resultado lo decide el servidor con `secrets` y se guarda en la misma transacción que el cobro. La animación es solo decoración.

## Desplegar
1. **Backup del volumen** antes de tocar nada: `fly volumes list` y luego `fly volumes snapshots create <id_del_volumen>`.
2. (Solo si vas a conectar la web) `fly secrets set BOT_API_SECRET="<cadena larga al azar>"`. Sin ese secret, la API web queda apagada.
3. `fly deploy`.
4. Al arrancar, la base se migra sola (agrega la columna `ajuste`, conserva lo que ya hay). **Es compatible hacia atrás**: si quieres volver a la versión anterior, la base sigue funcionando.
5. Si cambias reglas: `fly secrets set MAX_DISPONIBLES=8` (etc.). Lista completa en `.env.example`.

## Probar antes de subir
```
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## API para la web (cuando se conecte la ruleta de la página)
Solo la llama el Worker de Cloudflare, con `Authorization: Bearer <BOT_API_SECRET>`; el Worker verifica antes la sesión de Firebase.
- `POST /api/vincular` `{uid, codigo}` · `GET /api/saldo?uid=` · `POST /api/ruleta` `{uid, apuesta, cantidad}`
