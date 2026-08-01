# AGENTS.md — Working in llama-router

Guidance for AI agents (or any contributor) touching this codebase. No prior
knowledge assumed. Follow these conventions so changes stay consistent and
verifiable.

---

## 0. Getting oriented (read this first)

There is no generated architecture map — the code is the map. To orient
yourself in a codebase this size (~7k lines), read in this order:

1. `main.py` — wiring: it constructs every service in dependency order and
   shows exactly how they connect. The clearest 100 lines in the repo.
2. `llama_router/schemas.py` — the data model (dataclasses, no pydantic).
3. `llama_router/ui/app.py` — the shell: `PAGES`, navigation, theme rebuild.
4. Whichever service or page you're about to change.

The layout table in §3 and the architecture primer in §4 cover the rest.
Prefer reading the source over trusting any doc, including this one — docs
drift, `grep` does not.

---

## 1. What this project is

**llama-router** is a desktop control panel for `llama.cpp` servers, built as a
**pure-stdlib Python + Tkinter** app (no venv, no web framework). It manages
runtimes (llama.cpp builds), scans `.gguf` models, builds per-model profiles,
generates a `models-preset.ini`, starts/stops `llama-server`, and shows GPU
monitoring.

- **Language:** Python 3.10+ (enforced in `main.py`).
- **UI:** Tkinter (custom canvas-drawn widgets, NOT a web framework).
- **Docs:** `README.md` is the user-facing doc; this file is the contributor
  guide. The code is the architecture map (see §0).
- **i18n:** English is the base catalog; `t("key")` resolves a string. Missing
  keys fall back to the key itself, so you can add new UI strings as plain
  English keys without breaking anything. The Spanish catalog is additive.

---

## 2. Run it

```bat
launch.bat            # Windows: py -3 main.py
launch.sh             # Linux / macOS: python3 main.py
# or directly:
python main.py
```

On first run it creates `config/`, `models/`, `runtime/`, `logs/` under the
project base and writes a default config. Frozen builds (tools/build.py) show
a one-time dialog to pick that base; the choice is stored in a
`llama-router.base` pointer file next to the exe (delete it to reset).

---

## 3. Repository layout (what you'll edit most)

```
main.py                  Entry point: builds services + App, calls app.run()
tools/                   ALL developer tools and tests live here (see §§7–8)
  screenshots.py             Captures isolated README/ad-hoc screenshots (Pillow)
  generate_icon.py           Regenerates the packaged PNG/ICO app icons
  tests/                     unittest suite
llama_router/
  core/                  EventBus, storage, secrets, paths, gguf, logs, OS helpers
  services/              ConfigManager, ModelsManager, ProfileManager,
                         RuntimeManager, DownloadManager, ServerManager,
                         PlaygroundService, GpuMonitor, system monitor, tray
  schemas.py             AppConfig dataclass (incl. `theme` field)
  preset.py              Writes models-preset.ini
  i18n.py                t() + catalogs
  ui/
    app.py               Root window: header + tab bar + status bar + pages
                         AppContext, build_chrome/build_content, apply_theme()
    theme.py             DESIGN TOKENS + ttk styling + THEMES dict
    widgets.py           Custom widgets: Card, PillButton, NavItem, StatusDot,
                         SegmentBar, ScrollFrame, section_label, enable_row_hover
    pages/               Active pages: dashboard, playground, profiles
                         (Models workspace), runtime, settings.
                         Legacy models/preset/server modules remain as helpers.
      base.py            Page base class + PAGE_PAD + header() helper
```

**Rule of thumb:** tokens live in `theme.py`; reusable chrome in `widgets.py`;
per-screen layout in `pages/*.py`.

---

## 4. Architecture primer (read before changing UI)

- **Services** are plain objects passed in `AppContext.services` (a dict keyed
  by name: `"config"`, `"models"`, `"profiles"`, `"runtimes"`, `"downloads"`,
  `"server"`).
- **EventBus** is thread-safe: workers `publish()` from threads; the UI drains
  on the main thread via `App._pump` (`root.after`). Subscribe with
  `ctx.events.subscribe("event", handler)`; handlers run on the UI thread and
  may touch widgets.
- **Pages are lazily built and cached** in `App._pages`. `show_page(key)`
  builds a page once, then `pack_forget`/`pack` to switch. `on_show()` runs on
  every show.
- **Custom widgets snapshot their colors from the token dict at build time.**
  This is the single most important UI fact: recoloring at runtime requires
  **rebuilding** the widget, not mutating a color. See §6 for how theme
  switching does exactly that.

---

## 5. Design language (keep it consistent)

Instrument-panel aesthetic, not a website:
- Near-black/charcoal surfaces, **one hot accent** (amber in *forge*, cyan in
  *midnight*), **monospace** for all machine data/numbers.
- Tokens (never hardcode hex in pages): `bg, surface, surface_hi, inset,
  border, text, muted, faint, accent, accent_hi, accent_dn, on_accent, title,
  ok, ok_dim, warn, warn_dim, error, error_dim, request, num`.
- Rounded surfaces (Card radius 6, PillButton radius 4), hairline borders,
  subtle hover only. Avoid drop shadows / gradients.
- If you must hardcode a color, add it as a token in `theme.py` instead.

---

## 6. Themes

`theme.py` defines `THEMES`, one dict of full palettes — it is the *only*
theme table. `theme.apply(root, name)` repaints all ttk styles and returns the
active token dict.

**Eight themes ship**, in `THEMES` order (which is the selector order):
**forge** (dark amber), **midnight** (default), **carbon**, **vapor**,
**ember**, **light**, **sage**, **arctic**. Never hardcode a theme list
anywhere; read `theme.theme_names()` (Settings does).

Two non-colour keys live in each palette alongside the tokens:
`_label` (selector caption) and `_dark` (drives the Windows titlebar), read via
`theme.label(name)` and `theme.is_dark(name)`.

### Adding / editing a theme
1. Add or edit an entry in `THEMES` with **all** tokens plus `_label` and
   `_dark` (copy an existing one as a base — every page/widget assumes the
   full key set exists).
2. Position it in `THEMES` where it should appear in the Settings picker.
   That is the whole registration — there is no second list to update.
3. The chosen theme is persisted in `AppConfig.theme` and restored on launch
   (`App._current_theme_name`).

### Live theme switching (how it works)
`App.apply_theme(name)`:
1. Snapshots the active page's unsaved input (`page._serialize()`, if present).
2. Persists `theme` to config.
3. Unsubscribes chrome handlers, cancels the clock timer, destroys all root
   children.
4. Repaints ttk styles and **rebuilds chrome + the active page** from the new
   palette.
5. Restores the snapshotted input (`page._restore()`), re-syncs the server
   status dot.

Because pages rebuild, any `Page` with editable fields should implement
`_serialize()` / `_restore()` so a theme flip can't wipe unsaved edits
(SettingsPage is the example).

---

## 7. Tools (`tools/`)

Every developer tool lives in `tools/` and is run from the repository root.
Nothing tool-related belongs in the project root, and no tool may be imported
by `llama_router/` — tools may use dev-only dependencies (Pillow), the app may
not.

| Tool | What it does |
| --- | --- |
| `tools/screenshots.py` | Captures README screenshots or page/theme/size matrices. |
| `tools/benchmark_ui.py` | Measures page construction and settled Tk navigation. |
| `tools/build.py` | One-file PyInstaller build (`--debug` keeps the console; pass `--debug` when launching the app to enable timings; `--keep-work` preserves scratch files). Cleans `build/` and `llama-router.spec` automatically. Needs `pip install pyinstaller`; per-OS, no cross-compiling. |
| `tools/generate_icon.py` | Regenerates `app_icon.png` and multi-resolution `app_icon.ico` using Pillow. |

### 7.1 `tools/screenshots.py`

Builds the real `App` through a capture fixture, walks pages, switches themes
and grabs the window with Pillow. The fixture locks the real data folder and
copies only the database and generated preset into a temporary base; it never
writes the user's config, sessions, downloads, or preset.

```bat
tools\dev.bat screenshots                                :: static review set
py -3 tools/screenshots.py --pages profiles settings      :: midnight only
py -3 tools/screenshots.py --pages settings --themes light arctic
py -3 tools/screenshots.py --pages dashboard --sizes 960x640 1280x860
py -3 tools/screenshots.py --pages dashboard --out _scratch --keep-open
tools\dev.bat video                                      :: README demo GIF + MP4
```

- **`tools\dev.bat screenshots`** → writes the committed static review set
  listed in §10. It does not regenerate the demo video.
- **`--pages` / `--themes` / `--sizes`** → ad-hoc captures named
  `<page>_<theme>[_<width>x<height>].png` in `--out` (default
  `llama_router/assets/screenshots/`). Use a scratch `--out` so you don't clobber
  the committed README set.
- `--keep-open` leaves the window up for manual review; otherwise it closes.
- Requires Pillow (`pip install pillow`), a **dev-only** dependency. On a
  headless Linux box run it under `xvfb-run`. Confirm a display exists with
  `python -c "import tkinter;r=tkinter.Tk();print(r.winfo_screenwidth())"`.
- It reads the copied database state. If the model registry is empty it injects
  a placeholder into the temporary fixture; nothing is persisted to the real
  data folder.

### 7.2 `tools/benchmark_ui.py`

Run the benchmark separately from `verify` because it is slower and sensitive
to the host:

```bat
tools\dev.bat benchmark
```

The default run uses five fresh worker processes per case, two window sizes,
and three repeated navigations. It reports `app_build_ms`,
`startup_settled_ms`, `construct_ms`, `on_show_ms`, `show_call_ms`,
`navigation_idle_ms`, and the externally measured `settled_ms`. Startup and
first-load comparisons use `startup_settled_ms` or `settled_ms`, never the
diagnostic navigation-idle number.

### 7.3 Verifying UI work

The agent model generally **cannot view rendered images**, so do both:

**(a) Produce screenshots a human can open** — run the tool above and hand over
the paths.

**(b) Assert structure and colours programmatically** — far more trustworthy
than pixels:

```python
c = app.ctx.colors
assert c["bg"] == "#070b12"                       # midnight bg
assert app.content.cget("bg") == c["bg"]
app.show_page("settings")
sp = app._pages["settings"]
sp._appearance_card.set_open(True)
assert set(sp._theme_btns) == set(theme.theme_names())   # never a literal list
assert sp._theme_btns["midnight"]._kind == "primary"     # active = primary
app.show_page("profiles")
assert app._active == "profiles"
```

### 7.4 Capture-coordinate gotcha

`winfo_rootx()/rooty()` return the **outer** window top-left (including the OS
titlebar) while `winfo_width()/winfo_height()` return the **client** size. The
capture rect therefore includes the titlebar and clips a sliver off the bottom.
**Never sample edge or background pixels from these grabs to assert colours** —
you will read the window frame, not the app. Sample inside a known widget
(`widget.winfo_rootx/y` + size) instead.

### 7.5 Responsive captures

Use `tools/screenshots.py` for an ad-hoc page/theme/viewport matrix. Direct
scratch output to an ignored directory:

```bat
py -3 tools/screenshots.py --pages dashboard profiles --sizes 960x640 760x500 --out _responsive
```

## 8. Tests

```bat
py -3 -m unittest discover tools/tests
# equivalently:
python -m unittest discover -s tools/tests -p "*.py"
```
- Tests live with the developer tooling under `tools/tests/`. Run them from
  the repository root; use the command output as the source of truth for the
  current test count.
- **Known unrelated noise:** `test_phase4` spawns the bundled `llama-server`
  binary which may print `Unknown option: --models-preset` / exit rc=2 on this
  host. That is an environment/runtime-binary issue, **not** caused by UI
  changes. Don't try to "fix" it as part of UI work.
- `pytest` is **not** installed and not used. Use `unittest`.
- After UI changes, at minimum: `tools\dev.bat compile` and
  `tools\dev.bat tests`.
- For Tcl/Tk, tracemalloc, warnings, and the complete resource check, use
  `tools\dev.bat tests-resources`.

---

## 9. Common tasks (recipes)

**Add a hover state to a button/row**
- `PillButton` already hovers when it has a `command` (ghost/primary/accent).
- For table rows, call `enable_row_hover(tree, c)` after creating a
  `ttk.Treeview` (done in models/profiles/runtime). It adds a `hover` tag and
  untags on leave; it preserves existing tags (e.g. `missing`, `disabled`) and
  guards against rows deleted by a refresh via `tree.exists()`.
- For a custom clickable frame, bind `<Enter>`/`<Leave>` to swap `bg` on the
  frame and all children (see `DashboardPage._render_steps`).

**Add/change a token**
- Edit the relevant palette in `THEMES` in `theme.py`. Keep all keys present.
- ttk styles are (re)applied in `theme.apply()`; custom widgets read `c[...]`.

**Add a setting**
- Add the field to `AppConfig` in `schemas.py`, surface it in
  `pages/settings.py`, persist via `config.update({...})`.

**Change a page layout**
- Edit `pages/<name>.py`. Reuse `Card`, `PillButton`, `section_label`,
  `key_value`, `ScrollFrame`, `StatusDot`, `SegmentBar` from `widgets.py`.
- Read colors from `self.c` (the token dict), never literal hex.

---

## 10. README screenshots

`README.md` embeds images from `llama_router/assets/screenshots/`. When the UI changes visibly,
regenerate them.

```bat
tools\dev.bat compile
tools\dev.bat screenshots
```

The static review set is:

`page_dashboard.png` · `page_playground.png` · `page_profiles.png` ·
`page_runtime.png` · `page_settings.png` (Midnight) ·
`settings_midnight.png` · `settings_light.png` ·
`final_dashboard.png`

The PNGs are review artifacts. The README embeds `app_demo.gif` and links to
`app_demo.mp4`; regenerate both with `tools\dev.bat video`.

Then:

1. Check the README demo links resolve: `app_demo.gif` and `app_demo.mp4`.
2. If you added or renamed a page, update `README_PAGES` in
   `tools/screenshots.py` and the static review set together.
3. Commit `README.md` and `llama_router/assets/screenshots/*.png` in the same commit as the UI
   change. `tools/screenshots.py` is a committed tool — do **not** delete it.

Pillow is only needed to run the tool; it is never a runtime dependency.

## 11. Gotchas

- **DPI:** `theme.set_dpi_aware()` is called before `Tk()` on Windows; don't
  remove it or text gets blurry.
- **Titlebar color:** `theme.enable_windows_niceties(root, dark=...)` sets the
  OS titlebar to match the theme via DWM. Pass `dark=False` for light themes.
- **Window rebuild on theme change** destroys everything under `root` — any
  `after()` timer or event subscription tied to chrome must be recreated (see
  `App.apply_theme` / `_build_chrome`). Don't double-subscribe
  `server_status`.
- **`rounded_rect` helper** in `widgets.py` draws smooth corners on a Canvas;
  custom widgets use it instead of square `create_rectangle`.
- **i18n:** wrap user-facing text in `t(...)`. Don't concatenate raw English
  into `t()` calls in a way that breaks the key lookup.
- **Titlebar follows `theme.is_dark(name)`** — add every new dark palette to
  that function or its window keeps a light titlebar.
- **Tools go in `tools/`, never the project root.** Don't create `_shot.py`,
  `_gen_screenshots.py` or similar scratch scripts — extend
  `tools/screenshots.py` instead. Scratch *output* (stray PNGs, `_scratch/`)
  still must not be committed; the tools themselves are permanent and
  committed.
