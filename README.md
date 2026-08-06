# Llama Router

<p align="center">
  <img src="./llama_router/assets/readme-banner.svg" alt="Llama Router - server idle, ready to start">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
</p>

<p align="center">
  <strong>A desktop control panel for <a href="https://github.com/ggml-org/llama.cpp">llama.cpp</a> servers.</strong><br>
  Manage local GGUF models with a native Python + Tkinter application.
</p>

## What you can do

- **Install runtimes**, download prebuilt `llama-server` binaries (CUDA, Vulkan, CPU) or import one you already compiled.
- **Find your models**, point it at folders with `.gguf` files and scan.
- **Edit `models-preset.ini`**, the lossless Model Preset workspace is the single source of truth for routes and model parameters.
- **Keep a GGUF registry**, used for scanning, metadata and route suggestions without duplicating runtime configuration.
- **Run the server**, start, stop, restart from the Dashboard with live logs and health checks.
- **Watch the GPU**, VRAM and utilization while the server is up.
- **Use the built-in playground**, chat with a running model, stream responses, attach files, copy code blocks, and retain local sessions.

## Demo

[![Llama Router app demo](llama_router/assets/screenshots/app_demo.gif)](llama_router/assets/screenshots/app_demo.mp4)

Start the server, connect a client, chat in the Playground, inspect models and
runtimes, then switch themes. Click the demo for the higher-quality MP4.

## Quick start

Launch the application:

```bat
:: Windows
launch.bat
```

```bash
# Linux / macOS
./launch.sh
```

Or invoke it directly with `py -3 main.py` on Windows or `python3 main.py`
elsewhere. Page construction and load timings stay hidden by default; pass
`--debug` when launching the app to enable them. Packaged diagnostics require
the console build produced by `tools\dev.bat build-debug`.

Then:

1. Open **Runtime**, choose a prebuilt build appropriate for your hardware, and click **Install**. You can instead import an existing `llama-server` build.
2. Open **Model Preset**, scan or add folders containing `.gguf` files, and choose **Add model** for a route.
3. Edit the route directly in `models-preset.ini`: context size, GPU layers, sampling, speculative decoding, and other `llama-server` options are offered from the active runtime catalogue.
4. Open **Dashboard** and click **Start Server**.
5. Connect an OpenAI-compatible client to `http://127.0.0.1:8080/v1`.

The app never regenerates the preset from a hidden database. Every configured
route, model source and inference parameter lives in
`config/models-preset.ini`; the server and Playground read that file rather
than a second model-configuration store. The SQLite GGUF registry is only a
discovery index for file metadata, missing-file diagnostics and route
suggestions. Saving creates a recoverable `.bak` and uses an external-change
check so another editor cannot silently overwrite your work. If the server is
already running, use **Apply to server** explicitly to request
`GET /models?reload=1`; saving alone does not reload or restart models.

`models-preset.ini` follows llama.cpp's named-section grammar. The `[*]`
section contains global defaults; every other section is a route and must have
one source such as `model`, `model-url`, `hf-repo`, or `docker-repo`. Local
relative paths are resolved against the selected runtime directory. Duplicate
sections and malformed lines are blocking diagnostics, while missing local
files remain visible warnings so a preset can be repaired before the model is
downloaded.

## Requirements

- **Python 3.10+** is the only application dependency. On Linux, Tk may be packaged separately: `sudo apt install python3-tk`.
- **Windows 10/11, Linux, or macOS.** Windows additionally supports notification-area controls and Job Object cleanup.

Developer tools keep their optional dependencies isolated in `tools/.venv`
(Pillow, imageio-ffmpeg, and PyInstaller); the application itself does not
need a virtual environment.

## Data and files

In source mode, Llama Router is portable: it creates these folders beside `main.py` on first launch.

| Path | Purpose |
| --- | --- |
| `config/` | SQLite configuration, API-key store, and user-owned `models-preset.ini` (plus recoverable backups) |
| `models/` | Default location for models (you may also scan other folders) |
| `runtime/` | Downloaded or imported `llama.cpp` runtimes |
| `logs/` | Application and server logs |

Frozen builds ask where to store data the first time they run. Choosing the app folder keeps the installation portable; choosing another writable folder stores a small pointer next to the executable.

## Troubleshooting

- **Tkinter is missing on Linux:** install `python3-tk`, then run `python3 main.py` again.
- **The server will not start:** check that a runtime is installed and active, `models-preset.ini` contains at least one usable route, and the configured port is not in use. The Dashboard log explains startup failures.
- **A client cannot connect:** verify the Dashboard endpoint and use `/v1` for OpenAI-compatible clients. If an API key is enabled, provide it as a Bearer token.
- **GPU acceleration is unavailable:** install/select a runtime matching your hardware and drivers, or use the CPU runtime.
- **Another instance is already running:** only one application instance can use the same data folder. Close the existing instance before launching another.

## License

MIT
