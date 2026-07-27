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
        "Models & Profiles": "Modelos y perfiles",
        "Profiles": "Perfiles",
        "Server": "Servidor",
        "Settings": "Ajustes",
        "Preparing interface… {done}/{total}":
            "Preparando interfaz… {done}/{total}",
        "Interface ready": "Interfaz lista",
        "a control panel for llama.cpp": "panel de control para llama.cpp",

        # ── Page eyebrows / placeholders ─────────────────────────────────────
        "panel": "panel",
        "library": "biblioteca",
        "inference presets": "ajustes de inferencia",
        "llama.cpp builds": "builds de llama.cpp",
        "llama-server process": "proceso llama-server",
        "configuration": "configuración",

        # ── Models ───────────────────────────────────────────────────────────
        "GGUF files found in your model folders":
            "Archivos GGUF encontrados en tus carpetas de modelos",
        "Add folder": "Añadir carpeta",
        "Scan folders": "Escanear carpetas",
        "Scanning…": "Escaneando…",
        "Name": "Nombre",
        "Size": "Tamaño",
        "State": "Estado",
        "ready": "listo",
        "missing": "no encontrado",
        "Remove from list": "Quitar de la lista",
        "Enable all": "Habilitar todos",
        "Disable all": "Deshabilitar todos",
        "{total} models · {new} new": "{total} modelos · {new} nuevos",
        "No models yet.\nDrop GGUF files in a folder and scan.":
            "Aún no hay modelos.\nDeja archivos GGUF en una carpeta y escanea.",

        # ── Profiles ─────────────────────────────────────────────────────────
        "Each active profile becomes a route on the server":
            "Cada perfil activo se convierte en una ruta del servidor",
        "Model": "Modelo",
        "New": "Nuevo",
        "Delete": "Eliminar",
        "Profile": "Perfil",
        "Save profile": "Guardar perfil",
        "Saved automatically": "Guardado automático",
        "Saving…": "Guardando…",
        "Back to profiles": "Volver a perfiles",
        "New session": "Nueva sesión",
        "Model library": "Biblioteca de modelos",
        "Advanced parameters": "Parámetros avanzados",
        "Show editor": "Mostrar editor",
        "Hide editor": "Ocultar editor",
        "Route alias": "Alias de ruta",
        "Context size": "Tamaño de contexto",
        "GPU layers": "Capas en GPU",
        "Reasoning": "Razonamiento",
        "Jinja templates": "Plantillas Jinja",
        "Active": "Activo",
        "Additional parameters (key = value per line)":
            "Parámetros adicionales (clave = valor por línea)",
        "same flags as llama-server": "mismos flags que llama-server",
        "Add models first — then tune their profiles here.":
            "Añade modelos primero — después ajusta sus perfiles aquí.",
        "Pick a profile to edit — or create one with New.":
            "Elige un perfil para editarlo — o crea uno con Nuevo.",
        "Activate all": "Activar todos",
        "Deactivate all": "Desactivar todos",
        "Auto": "Auto",
        "ambiguous — pick manually": "ambiguo — elige manualmente",
        "nothing found next to the model": "no se encontró junto al modelo",
        # Profile editor sections
        "Core": "Núcleo",
        "Sampling": "Muestreo",
        "Chat & templates": "Chat y plantillas",
        "KV cache": "Caché KV",
        "Performance": "Rendimiento",
        "Multimodal": "Multimodal",
        "Speculative decoding": "Decodificación especulativa",
        "Router": "Router",
        # Profile editor fields
        "Batch size": "Tamaño de batch",
        "Micro-batch size": "Tamaño de micro-batch",
        "Flash attention": "Flash attention",
        "Sleep after idle (s)": "Dormir tras inactividad (s)",
        "Temperature": "Temperatura",
        "Top-K": "Top-K",
        "Top-P": "Top-P",
        "Min-P": "Min-P",
        "Repeat penalty": "Penalización de repetición",
        "Presence penalty": "Penalización de presencia",
        "Chat template file": "Archivo de plantilla de chat",
        "Template kwargs (JSON)": "Kwargs de plantilla (JSON)",
        "K cache type": "Tipo de caché K",
        "V cache type": "Tipo de caché V",
        "Full SWA cache": "Caché SWA completa",
        "Keep KV cache on CPU": "Mantener caché KV en CPU",
        "Disable prompt cache": "Desactivar caché de prompt",
        "Lock model in RAM": "Bloquear modelo en RAM",
        "Disable mmap": "Desactivar mmap",
        "MoE experts on CPU": "Expertos MoE en CPU",
        "Auto-fit to VRAM": "Auto-ajustar a VRAM",
        "Fit target (MiB)": "Objetivo de ajuste (MiB)",
        "MMProj file": "Archivo MMProj",
        "Keep projector on CPU": "Mantener proyector en CPU",
        "Type": "Tipo",
        "Draft model": "Modelo draft",
        "Draft tokens (n-max)": "Tokens draft (n-max)",
        "Draft K cache": "Caché K del draft",
        "Draft V cache": "Caché V del draft",
        "Load on startup": "Cargar al iniciar",
        "Embedding mode": "Modo embedding",
        "Seed": "Semilla",
        "XTC probability": "Probabilidad XTC",
        "XTC threshold": "Umbral XTC",
        "Mirostat entropy": "Entropía Mirostat",
        "Main GPU": "GPU principal",
        "Split mode": "Modo de reparto",
        "Tensor split": "Reparto de tensores",
        "RoPE scaling": "Escalado RoPE",
        "RoPE freq base": "Base de frecuencia RoPE",
        "RoPE freq scale": "Escala de frecuencia RoPE",

        # ── Runtime ──────────────────────────────────────────────────────────
        "Prebuilt binaries from ggerganov/llama.cpp releases":
            "Binarios precompilados de las releases de ggerganov/llama.cpp",
        "Import local build": "Importar build local",
        "Refresh releases": "Actualizar releases",
        "Fetching…": "Consultando…",
        "Installed": "Instalados",
        "Use this runtime": "Usar este runtime",
        "Backend": "Backend",
        "invalid": "inválido",
        "No runtimes yet — download one below.":
            "Aún no hay runtimes — descarga uno abajo.",
        "Available releases": "Releases disponibles",
        "Download & install": "Descargar e instalar",
        "Asset": "Archivo",
        "Name for this runtime:": "Nombre para este runtime:",
        "llama-server executable not found in that folder":
            "No se encontró el ejecutable llama-server en esa carpeta",
        "Could not reach GitHub — check your connection.":
            "No se pudo conectar con GitHub — revisa tu conexión.",
        "failed — {err}": "falló — {err}",

        # ── Server page ──────────────────────────────────────────────────────
        "Restart": "Reiniciar",
        "Launch command": "Comando de arranque",
        "Logs": "Logs",
        "All": "Todos",
        "Clear": "Limpiar",
        "Follow": "Seguir",
        "routes": "rutas",
        "No runtime selected": "Sin runtime seleccionado",
        "No runtime installed — pick one on the Runtime page.":
            "No hay runtime instalado — elige uno en la página Runtime.",
        "No enabled model has an active profile — check Models and Profiles.":
            "Ningún modelo habilitado tiene un perfil activo — revisa Modelos y Perfiles.",
        "Port {port} is busy — stop the other process or change it in Settings.":
            "El puerto {port} está ocupado — detén el otro proceso o cámbialo en Ajustes.",
        "The server is already changing state — wait a moment.":
            "El servidor ya está cambiando de estado — espera un momento.",

        # ── Settings ─────────────────────────────────────────────────────────
        "Appearance": "Apariencia",
        "Theme applies instantly and is saved automatically":
            "El tema se aplica al instante y se guarda automáticamente",
        "Save changes": "Guardar cambios",
        "Saved ✓": "Guardado ✓",
        "disabled": "deshabilitado",
        "Max tokens": "Tokens máximos",
        "Repetition": "Repetición",
        "Repeat last N": "Repetir últimos N",
        "Frequency penalty": "Penalización de frecuencia",
        "DRY multiplier": "Multiplicador DRY",
        "DRY base": "Base DRY",
        "DRY allowed length": "Longitud permitida DRY",
        "DRY penalty last N": "Penalización DRY últimos N",
        "DRY sequence breaker": "Separador de secuencia DRY",
        "Cache reuse": "Reutilización de caché",
        "SWA checkpoints": "Checkpoints SWA",
        "MoE CPU experts": "Expertos MoE en CPU (n)",
        "All MoE experts on CPU": "Todos los expertos MoE en CPU",
        "Sampling preset…": "Preset de sampling…",
        "Copy params from…": "Copiar parámetros de…",

        # ── Preset page ──────────────────────────────────────────────────────
        "source of truth": "fuente de la verdad",
        "What llama-server actually loads — regenerated on every change":
            "Lo que llama-server carga realmente — se regenera con cada cambio",
        "Reload": "Recargar",
        "Save file": "Guardar archivo",
        "edited — save or reload": "editado — guarda o recarga",
        "could not write file": "no se pudo escribir el archivo",
        "; empty — enable models and activate profiles":
            "; vacío — habilita modelos y activa perfiles",
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
        "Install a runtime and add models before starting.":
            "Instala un runtime y añade modelos para poder iniciar.",
        "Connect your client": "Conecta tu cliente",
        "Examples": "Ejemplos",
        "Hide examples": "Ocultar ejemplos",
        "Configured (hidden)": "Configurada (oculta)",
        "Not configured": "No configurada",
        "Client connection guide": "Guía de conexión de clientes",
        "Copy example": "Copiar ejemplo",
        "Use an active profile's route alias as the model name.":
            "Usa el alias de ruta de un perfil activo como nombre del modelo.",
        "LAN access uses this computer's current address. Allow the port through the firewall.":
            "El acceso LAN usa la dirección actual de este equipo. Permite el puerto en el firewall.",
        "Local access only. Choose Local network in Settings to connect from another device.":
            "Solo acceso local. Elige Red local en Ajustes para conectar desde otro dispositivo.",
        "Saved network changes apply after restarting the server.":
            "Los cambios de red guardados se aplican después de reiniciar el servidor.",
        "API key is enabled; replace YOUR_API_KEY with the configured key.":
            "La clave de API está habilitada; usa tu clave en lugar de YOUR_API_KEY.",
        "No API key is configured. Set one before exposing the server to a network.":
            "No hay una clave de API configurada. Define una antes de exponer el servidor a una red.",
        "Copy": "Copiar",
        "Export logs": "Exportar logs",
        "Log files": "Archivos de log",
        "Text files": "Archivos de texto",
        "All files": "Todos los archivos",
        "Show": "Mostrar",
        "Hide": "Ocultar",
        "Generate": "Generar",
        "Copied": "Copiado",
        "Copied ✓": "Copiado ✓",
        "OpenAI-compatible API — works with any standard client.":
            "API compatible con OpenAI — funciona con cualquier cliente estándar.",
        "Inventory": "Inventario",
        "System": "Sistema",
        "Registered models": "Modelos registrados",
        "Installed runtimes": "Runtimes instalados",
        "Active runtime": "Runtime activo",
        "none": "ninguno",
        "First steps": "Primeros pasos",
        "Serving at {url}": "Sirviendo en {url}",
        "Download a runtime": "Descarga un runtime",
        "pick a llama.cpp build in Runtime": "elige un build de llama.cpp en Runtime",
        "Add your models": "Añade tus modelos",
        "scan a folder with GGUF files": "escanea una carpeta con archivos GGUF",
        "Tune a profile": "Ajusta un perfil",
        "context, GPU layers and route alias": "contexto, capas en GPU y alias de ruta",
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
