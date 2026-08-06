"""Tiny translation layer — stdlib only.

English is the source language: UI code passes the English string to `t()`,
which returns it verbatim (optionally formatted) unless the active language's
catalog overrides it. Missing entries fall back to English automatically, so a
partial translation never breaks the UI.

Adding a language = adding one dict to CATALOGS.
"""
from __future__ import annotations

LANGUAGES = {"en": "English", "es": "Español"}

_lang = "en"


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in LANGUAGES else "en"


def t(text: str, **kwargs) -> str:
    """Translate *text* into the active language and format placeholders."""
    out = CATALOGS.get(_lang, {}).get(text, text)
    return out.format(**kwargs) if kwargs else out


CATALOGS: dict[str, dict[str, str]] = {
    "es": {
        # ── Navigation / shell ───────────────────────────────────────────────
        "Models": "Modelos",
        "Model Preset": "Preset de modelos",
        "Dashboard": "Panel",
        "Runtime": "Runtime",
        "Server": "Servidor",
        "Settings": "Ajustes",
        "server": "servidor",
        "runtime": "runtime",
        "model": "modelo",
        "models": "modelos",
        "local": "local",
        "a control panel for llama.cpp": "panel de control para llama.cpp",
        "Active runtime and routes available to llama-server.":
            "Runtime activo y rutas disponibles para llama-server.",
        "{count} preset errors": "{count} errores en el preset",

        # ── Page eyebrows / placeholders ─────────────────────────────────────
        "panel": "panel",
        "llama.cpp builds": "builds de llama.cpp",
        "configuration": "configuración",

        # ── Models ───────────────────────────────────────────────────────────
        "Add folder": "Añadir carpeta",
        "Scan folders": "Escanear carpetas",
        "Save": "Guardar",
        "Validate": "Validar",
        "Add model": "Añadir modelo",
        "Add parameter": "Añadir parámetro",
        "Apply to server": "Aplicar al servidor",
        "Scan models": "Escanear modelos",
        "Restore last backup": "Restaurar última copia",
        "Open file location": "Abrir ubicación del archivo",
        "The saved INI is the source of truth for model routes.":
            "El INI guardado es la fuente única de verdad para las rutas.",
        "No validation issues": "No hay problemas de validación",
        "External change": "Cambio externo",
        "Invalid preset": "Preset inválido",
        "Save failed": "Error al guardar",
        "Unsaved": "Sin guardar",
        "Unreadable": "Ilegible",
        "Saved": "Guardado",
        "External change detected — reload or overwrite explicitly":
            "Se detectó un cambio externo — recarga o sobrescribe explícitamente",
        "The file changed outside this editor. Overwrite the external version with your draft?":
            "El archivo cambió fuera de este editor. ¿Sobrescribir la versión externa con tu borrador?",
        "Discard unsaved changes and reload from disk?":
            "¿Descartar los cambios sin guardar y recargar del disco?",
        "Model missing": "Modelo no encontrado",
        "Model already present": "Modelo ya presente",
        "This model already has route(s). Add another route explicitly?":
            "Este modelo ya tiene ruta(s). ¿Añadir otra ruta explícitamente?",
        "Not in preset": "No está en el preset",
        "Select a model to add or jump to its routes":
            "Selecciona un modelo para añadirlo o saltar a sus rutas",
        "Add / Jump": "Añadir / Saltar",
        "Remove route": "Quitar ruta",
        "This model is not present in the preset.":
            "Este modelo no está presente en el preset.",
        "Choose one section to remove": "Elige una sección para quitar",
        "Remove section [{section}] from the preset?":
            "¿Quitar la sección [{section}] del preset?",
        "Jump to route": "Saltar a ruta",
        "Routes": "Rutas",
        "Add this route?": "¿Añadir esta ruta?",
        "MMProj companion": "Compañero MMProj",
        "A single MMProj companion was detected. Add it to this route?":
            "Se detectó un único compañero MMProj. ¿Añadirlo a esta ruta?",
        "Draft companion": "Compañero draft",
        "A single draft companion was detected. Add it to this route?":
            "Se detectó un único compañero draft. ¿Añadirlo a esta ruta?",
        "Place the cursor inside [*] or a model section.":
            "Coloca el cursor dentro de [*] o de una sección de modelo.",
        "Managed parameter": "Parámetro gestionado",
        "Parameter scope": "Alcance del parámetro",
        "This parameter is not available in the current section scope.":
            "Este parámetro no está disponible en el alcance de la sección actual.",
        "Controlled by Settings": "Controlado desde Ajustes",
        "Parameter value": "Valor del parámetro",
        "Insert": "Insertar",
        "Preset path": "Ruta del preset",
        "Reloading may unload or restart changed models. Continue?":
            "Recargar puede descargar o reiniciar modelos cambiados. ¿Continuar?",
        "Apply failed": "Error al aplicar",
        "Restore backup": "Restaurar copia",
        "Replace the current preset with its last backup?":
            "¿Reemplazar el preset actual con su última copia?",
        "Restore failed": "Error al restaurar",
        "Add model folder": "Añadir carpeta de modelos",
        "Scan failed": "Error al escanear",
        "Cancel": "Cancelar",
        "Scanning…": "Escaneando…",
        "Name": "Nombre",
        "Path": "Ruta",
        "GGUF": "GGUF",
        "Size": "Tamaño",
        "State": "Estado",
        "Missing": "Ausente",
        "Compatibility": "Compatibilidad",
        "Default": "Predeterminado",
        "Source": "Fuente",
        "Description": "Descripción",
        "Aliases": "Alias",
        "Allowed": "Permitidos",
        "Scope": "Alcance",
        "route(s)": "ruta(s)",
        "Quant": "Cuantización",
        "Params": "Parámetros",
        "Ctx": "Ctx",
        "VRAM": "VRAM",
        "ready": "listo",
        "missing": "no encontrado",
        "Remove from list": "Quitar de la lista",
        "Enable all": "Habilitar todos",
        "Disable all": "Deshabilitar todos",
        "{total} models · {new} new": "{total} modelos · {new} nuevos",
        "No models yet.\nDrop GGUF files in a folder and scan.":
            "Aún no hay modelos.\nDeja archivos GGUF en una carpeta y escanea.",

        "New session": "Nueva sesión",

        # ── Preset page ──────────────────────────────────────────────────────
        "Reload": "Recargar",
        "Save file": "Guardar archivo",
        "edited — save or reload": "editado — guarda o recarga",
        "invalid INI — changes not saved":
            "INI inválido — cambios no guardados",
        "could not write file": "no se pudo escribir el archivo",
        "Preset": "Preset",
        "Application": "Aplicación",
        "Language": "Idioma",
        "Applies instantly": "Se aplica al instante",
        "Start server on launch": "Iniciar servidor al abrir",
        "Concurrent downloads": "Descargas simultáneas",
        "Auto-check for runtime updates":
            "Buscar automáticamente nuevas versiones",
        "Reset to defaults": "Restaurar valores",
        "Network exposure": "Exposición de red",
        "This machine only": "Solo este equipo",
        "Local network": "Red local",
        "Custom host": "Host personalizado",
        "Host": "Host",
        "Port": "Puerto",
        "Models in memory": "Modelos en memoria",
        "Parallel slots": "Slots en paralelo",
        "CPU threads": "Hilos de CPU",
        "API key": "Clave de API",
        "Show API key in dashboard and examples": "Mostrar clave API en dashboard y ejemplos",
        "Stop timeout (s)": "Tiempo de parada (s)",
        "Continuous batching": "Batching continuo",
        "Prometheus metrics": "Métricas Prometheus",
        "Autoload models": "Cargar modelos automáticamente",
        "Batch CPU threads": "Hilos de CPU por lote",
        "Restart on crash": "Reiniciar si se cae",
        "Extra arguments": "Argumentos extra",

        # ── Dashboard ────────────────────────────────────────────────────────
        "Router status and first steps": "Estado del router y primeros pasos",
        "Stopped": "Detenido",
        "Starting…": "Iniciando…",
        "Running": "En ejecución",
        "Stopping…": "Deteniendo…",
        "Error": "Error",
        "Start server": "Iniciar servidor",
        "Port unavailable": "Puerto no disponible",
        "Stop": "Detener",
        "Connect your client": "Conecta tu cliente",
        "Examples": "Ejemplos",
        "Hide examples": "Ocultar ejemplos",
        "Configured (hidden)": "Configurada (oculta)",
        "Not configured": "No configurada",
        "LAN access uses this computer's current address. Allow the port through the firewall.":
            "El acceso LAN usa la dirección actual de este equipo. Permite el puerto en el firewall.",
        "Local access only. Choose Local network in Settings to connect from another device.":
            "Solo acceso local. Elige Red local en Ajustes para conectar desde otro dispositivo.",
        "Saved network changes apply after restarting the server.":
            "Los cambios de red guardados se aplican después de reiniciar el servidor.",
        "Copy": "Copiar",
        "Export logs": "Exportar logs",
        "Log files": "Archivos de log",
        "Text files": "Archivos de texto",
        "All files": "Todos los archivos",
        "Show": "Mostrar",
        "Hide": "Ocultar",
        "Generate": "Generar",
        "Copied": "Copiado",
        "System": "Sistema",
        "none": "ninguno",
        "First steps": "Primeros pasos",
        "Download a runtime": "Descarga un runtime",
        "pick a llama.cpp build in Runtime": "elige un build de llama.cpp en Runtime",
        "Add your models": "Añade tus modelos",
        "scan a folder with GGUF files": "escanea una carpeta con archivos GGUF",
        "Edit the model preset": "Edita el preset de modelos",
        "routes, parameters and load policy": "rutas, parámetros y política de carga",
        "Start the server": "Inicia el servidor",
        "then point your client at the endpoint": "y conecta tu cliente al endpoint",

        # ── Playground ───────────────────────────────────────────────────────
        "Playground": "Playground",
        "chat": "chat",
        "temp": "temp",
        "max": "máx",
        "New": "Nueva",
        "Sessions": "Sesiones",
        "The server is not running — start it to chat.":
            "El servidor no está en ejecución — inícialo para chatear.",
        "System prompt": "Prompt de sistema",
        "Saved sessions": "Sesiones guardadas",
        "No saved sessions yet.": "Aún no hay sesiones guardadas.",
        "Attach": "Adjuntar",
        "Send": "Enviar",
        "You": "Tú",
        "Assistant": "Asistente",
        "Copy message": "Copiar mensaje",
        "Edit": "Editar",
        "Regenerate": "Regenerar",
        "Delete message": "Eliminar mensaje",
        "Clear chat": "Vaciar chat",
        "Attach text files": "Adjuntar archivos de texto",
        "Attachment skipped": "Adjunto omitido",
        "file is larger than 200 KB": "el archivo supera los 200 KB",
        "Rename": "Renombrar",
        "Session name": "Nombre de la sesión",
        "Export…": "Exportar…",
        "Delete": "Eliminar",
        "Export session": "Exportar sesión",
        "Export failed": "Fallo al exportar",

        # ── Tray / multi-OS ──────────────────────────────────────────────────
        "Minimize to tray": "Minimizar a la bandeja",
        "Restore": "Restaurar",
        "Quit": "Salir",
        "Still running here — click to restore.":
            "Sigue corriendo aquí — clic para restaurar.",
    },
}
