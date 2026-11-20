# Juntadas

App web para el celular para coordinar juntadas con amigas: grupos, invitación por link, disponibilidad (tocando la grilla o escribiendo horas), mapa de calor del grupo, confirmación con invitación al calendario (Google, Apple, Outlook, .ics) y gastos divididos.

**Link para compartir:** https://arehermann.github.io/juntadas/

## Cómo funciona

```
 Celular de cada amiga ──► Página (GitHub Pages, link fijo)
                                 │  pregunta "¿dónde está la Raspberry?"
                                 ▼
                       backend.json en este repo  ◄── la Raspberry lo actualiza al arrancar
                                 │
                                 ▼
             Túnel gratis de Cloudflare ──► Raspberry Pi (guarda los datos)
```

- Nadie necesita instalar nada ni usar Tailscale: solo abren el link.
- Los datos quedan en la Raspberry, en `~/juntadas-datos/datos.json`, con una copia diaria en `~/juntadas-datos/copias/` (guarda las últimas 14).
- El túnel de Cloudflare no pide cuenta ni dominio. Su dirección cambia cada vez que la Pi se reinicia, por eso la Pi la anota en `backend.json` y la página la busca sola.
- Cada una ve solo las juntadas a las que la invitaron. Quien crea una juntada la organiza: invita con un link, confirma la fecha y puede sacar a alguien. Nadie puede salir ni ser sacada si tiene cuentas pendientes.
- No hace falta crear cuenta. Al entrar, cada persona recibe una llave personal secreta que queda guardada en su celular, y un **link personal** para entrar desde otro dispositivo. La Raspberry revisa esa llave en cada pedido, así que nadie puede hacerse pasar por otra ni ver juntadas ajenas, aunque la dirección de la Pi sea pública.
- Copias de seguridad: una por día en la Pi (`~/juntadas-datos/copias/`) y otra por día en el repo **privado** `arehermann/juntadas-datos`. Las llaves personales no se guardan (solo una huella), así que una copia no sirve para entrar como otra persona.

## Puesta en marcha (una sola vez)

### 1. Activar la página en GitHub
1. Entrá a **Settings → Pages** de este repositorio.
2. En *Source* elegí **Deploy from a branch**, en *Branch* elegí **main** y **/ (root)**, y tocá **Save**.
3. En un par de minutos la página queda en https://arehermann.github.io/juntadas/

### 2. Crear la llave para que la Raspberry pueda avisarle a GitHub
1. Entrá a https://github.com/settings/personal-access-tokens/new
2. *Token name*: `juntadas-pi`. *Expiration*: la más larga que te deje.
3. *Repository access*: **Only select repositories** → elegí **juntadas**.
   Antes, creá un repo **privado** vacío llamado `juntadas-datos` (para las copias) y elegí también ese.
4. *Permissions* → *Repository permissions* → **Contents**: **Read and write**.
5. Tocá **Generate token** y copiá la llave (empieza con `github_pat_`). Solo se ve una vez.

### 3. Instalar en la Raspberry
1. Abrí la terminal de la Raspberry (o conectate por SSH).
2. Pegá esta línea y apretá Enter:
   ```
   curl -fsSL https://raw.githubusercontent.com/arehermann/juntadas/main/server/instalar.sh | bash
   ```
3. Cuando te la pida, pegá la llave del paso 2 (no se ve mientras la pegás) y apretá Enter. Puede pedirte la contraseña de la Pi para instalar.
4. Al final dice **"Listo. La Raspberry está en: https://…trycloudflare.com"**. Ya está: abrí https://arehermann.github.io/juntadas/ y creá tu grupo.

Desde ahí, la Pi arranca todo sola cada vez que se prende.

## Comandos útiles en la Pi

| Para… | Escribí |
|---|---|
| Ver si anda | `systemctl status juntadas juntadas-tunel` |
| Ver qué está pasando con el túnel | `journalctl -u juntadas-tunel -f` |
| Reiniciar todo | `sudo systemctl restart juntadas juntadas-tunel` |
| Actualizar a la última versión | volver a pegar la línea del paso 3 |
| Cambiar la llave de GitHub | `rm ~/.juntadas.conf` y volver a pegar la línea del paso 3 |

## Si algo falla
- **La página dice "No se puede conectar"**: la Pi está apagada, sin internet o recién reiniciada. Esperá un minuto. Si sigue, mirá `journalctl -u juntadas-tunel -n 50`.
- **El túnel dice que no pudo actualizar GitHub**: la llave venció o no tiene permiso de *Contents: Read and write* sobre `juntadas`. Hacé una nueva (paso 2) y cambiala.

## Para programar
- `index.html`: toda la app (HTML, CSS y JS, sin build).
- `server/juntadas_server.py`: API en Python 3 sin dependencias. Probar local: `python3 server/juntadas_server.py --data /tmp/datos.json` y abrir `index.html?api=http://localhost:8787` desde un servidor local.
- `server/tunel.py`: corre `cloudflared` y publica la dirección en `backend.json`.
- `server/respaldo.py`: copia diaria de `datos.json` al repo privado `BACKUP_REPO` (se niega si el repo es público).
- `server/instalar.sh`: instala todo como servicios de systemd.
