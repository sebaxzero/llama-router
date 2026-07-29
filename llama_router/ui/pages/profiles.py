"""Profiles — per-model inference presets that become models-preset.ini
sections. Left: one tree of models with their profiles (checkbox = active).
Right: the full parameter editor, organised in sections."""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from llama_router.core.utils import fmt_bytes
from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.pages.models import ModelsPage
from llama_router.ui.pages.preset import PresetPage
from llama_router.ui.widgets import (AutoScrollbar, CollapsibleCard, NavItem,
                                     PillButton, ScrollFrame, Tooltip,
                                     section_label, enable_row_hover)

_CACHE_TYPES = ["f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0",
                "iq4_nl"]
_DRAFT_CACHE_TYPES = ["f16", "f32", "bf16", "q8_0", "q5_1", "q5_0",
                      "q4_1", "q4_0", "iq4_nl"]
_SPEC_TYPES = ["none", "draft-simple", "draft-eagle3", "draft-mtp",
               "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod",
               "ngram-cache"]

# llama-server defaults (tools/server/README.md). A field showing its default
# is treated as unset: it is not stored in the profile and therefore never
# written to models-preset.ini. Choice fields encode their default as the
# omit value instead.
_LLAMA_DEFAULTS: dict[str, int | float | str] = {
    "n-predict": -1,
    "batch-size": 2048,
    "ubatch-size": 512,
    "seed": -1,
    "temp": 0.8,
    "top-k": 40,
    "top-p": 0.95,
    "min-p": 0.05,
    "typical-p": 1.0,
    "top-nsigma": -1.0,
    "xtc-probability": 0.0,
    "xtc-threshold": 0.1,
    "mirostat-lr": 0.1,
    "mirostat-ent": 5.0,
    "repeat-last-n": 64,
    "repeat-penalty": 1.1,
    "presence-penalty": 0.0,
    "frequency-penalty": 0.0,
    "dry-multiplier": 0.0,
    "dry-base": 1.75,
    "dry-allowed-length": 2,
    "dry-penalty-last-n": -1,
    "cache-reuse": 0,
    "cache-ram": 8192,
    "main-gpu": 0,
    "fit-ctx": 4096,
    "reasoning-budget": -1,
    "spec-draft-n-min": 0,
}

# Field kinds: int | float | str | bool | choice | combo | file
# choice extra = (values, omit_value): omit_value is not stored in params
# (None means the value is always stored). combo = editable choice.
# file extra = optional detect callback name on this page.
# Sections mirror pi-test's profile editor (ui/js/pages/models.js).
_SECTIONS: list[tuple[str, list[tuple]]] = [
    ("Core", [
        ("ctx-size", "Context size", "int", None),
        ("n-predict", "Max tokens", "int", None),
        ("n-gpu-layers", "GPU layers", "int", None),
        ("batch-size", "Batch size", "int", None),
        ("ubatch-size", "Micro-batch size", "int", None),
        ("flash-attn", "Flash attention", "choice", (["auto", "on", "off"], "auto")),
        ("seed", "Seed", "int", None),
    ]),
    ("Sampling", [
        ("temp", "Temperature", "float", None),
        ("top-p", "Top-P", "float", None),
        ("top-k", "Top-K", "int", None),
        ("min-p", "Min-P", "float", None),
        ("typical-p", "Typical-P", "float", None),
        ("top-nsigma", "Top-N sigma", "float", None),
        ("xtc-probability", "XTC probability", "float", None),
        ("xtc-threshold", "XTC threshold", "float", None),
        ("mirostat", "Mirostat", "choice", (["0", "1", "2"], "0")),
        ("mirostat-lr", "Mirostat LR", "float", None),
        ("mirostat-ent", "Mirostat entropy", "float", None),
    ]),
    ("Repetition", [
        ("repeat-last-n", "Repeat last N", "int", None),
        ("repeat-penalty", "Repeat penalty", "float", None),
        ("presence-penalty", "Presence penalty", "float", None),
        ("frequency-penalty", "Frequency penalty", "float", None),
        ("dry-multiplier", "DRY multiplier", "float", None),
        ("dry-base", "DRY base", "float", None),
        ("dry-allowed-length", "DRY allowed length", "int", None),
        ("dry-penalty-last-n", "DRY penalty last N", "int", None),
        ("dry-sequence-breaker", "DRY sequence breaker", "str", None),
    ]),
    ("Chat & templates", [
        ("jinja", "Jinja templates", "choice", (["", "true", "false"], "")),
        ("reasoning", "Reasoning", "choice", (["", "auto", "on", "off"], "")),
        ("reasoning-format", "Reasoning format", "choice",
         (["auto", "none", "deepseek", "deepseek-legacy"], "auto")),
        ("reasoning-budget", "Reasoning budget", "int", None),
        ("chat-template-file", "Chat template file", "file", None),
        ("chat-template-kwargs", "Template kwargs (JSON)", "str", None),
    ]),
    ("KV cache", [
        ("cache-type-k", "K cache type", "choice", (_CACHE_TYPES, "f16")),
        ("cache-type-v", "V cache type", "choice", (_CACHE_TYPES, "f16")),
        ("cache-reuse", "Cache reuse", "int", None),
        ("cache-ram", "Shared cache limit (MiB)", "int", None),
        ("swa-checkpoints", "SWA checkpoints", "int", None),
        ("swa-full", "Full SWA cache", "bool", None),
        ("no-kv-offload", "Keep KV cache on CPU", "bool", None),
        ("no-cache-prompt", "Disable prompt cache", "bool", None),
    ]),
    ("Performance", [
        ("n-cpu-moe", "MoE CPU experts", "int", None),
        ("cpu-moe", "All MoE experts on CPU", "bool", None),
        ("load-mode", "Load mode", "choice",
         (["mmap", "none", "mlock", "mmap+mlock", "dio"], "mmap")),
        ("device", "Devices", "str", None),
        ("fit", "Auto-fit to VRAM", "choice", (["on", "off"], "on")),
        ("fit-target", "Fit target (MiB)", "int", None),
        ("fit-ctx", "Minimum fit context", "int", None),
        ("main-gpu", "Main GPU", "int", None),
        ("split-mode", "Split mode", "choice", (["layer", "row", "none"], "layer")),
        ("tensor-split", "Tensor split", "str", None),
    ]),
    ("RoPE", [
        ("rope-scaling", "RoPE scaling", "choice",
         (["", "none", "linear", "yarn"], "")),
        ("rope-freq-base", "RoPE freq base", "float", None),
        ("rope-freq-scale", "RoPE freq scale", "float", None),
    ]),
    ("Multimodal", [
        ("mmproj", "MMProj file", "file", "_detect_mmproj"),
        ("no-mmproj-offload", "Keep projector on CPU", "bool", None),
    ]),
    ("Speculative decoding", [
        ("spec-type", "Type", "combo", (_SPEC_TYPES, "none")),
        ("spec-draft-n-max", "Draft tokens (n-max)", "int", None),
        ("spec-draft-n-min", "Draft tokens (n-min)", "int", None),
        ("spec-draft-model", "Draft model", "file", "_detect_draft"),
        ("spec-draft-device", "Draft devices", "str", None),
        ("spec-draft-ngl", "Draft GPU layers", "combo",
         (["auto", "all"], "auto")),
        ("cache-type-k-draft", "Draft K cache", "choice", (_DRAFT_CACHE_TYPES, "")),
        ("cache-type-v-draft", "Draft V cache", "choice", (_DRAFT_CACHE_TYPES, "")),
    ]),
    ("Router", [
        ("load-on-startup", "Load on startup", "bool", None),
        ("embedding", "Embedding mode", "bool", None),
        ("stop-timeout", "Stop timeout (s)", "int", None),
        ("sleep-idle-seconds", "Sleep after idle (s)", "int", None),
    ]),
]

# Compact values pair naturally; sliders, paths and free text span the card.
_SECTION_COLUMNS = {
    "Core": 2,
    "Sampling": 2,
    "Repetition": 2,
    "Chat & templates": 2,
    "KV cache": 2,
    "Performance": 2,
    "RoPE": 2,
    "Multimodal": 1,
    "Speculative decoding": 2,
    "Router": 2,
}

# Controls that need the whole card width to remain readable.  Everything
# else flows in the section's compact column count above.
_WIDE_FIELDS = {
    "ctx-size", "temp", "top-p",
    "chat-template-file", "chat-template-kwargs",
    "dry-sequence-breaker", "tensor-split", "device", "mmproj",
    "spec-draft-model", "spec-draft-device",
}

# Keep the controls most often tuned for a model in the always-visible part of
# the editor.  They remain in their original sections when persisted; this only
# changes the form layout, so existing profiles and generated presets are
# unaffected.
_QUICK_FIELD_KEYS = {
    "ctx-size", "flash-attn", "cache-type-k", "cache-type-v",
    "temp", "top-k", "top-p", "n-gpu-layers", "n-cpu-moe",
    "batch-size", "ubatch-size", "load-mode",
}

_SLIDER_FIELDS = {
    "ctx-size": (512, 32768, 512, "int"),
    "temp": (0.0, 2.0, 0.05, "float"),
    "top-p": (0.0, 1.0, 0.01, "float"),
}

_QUICK_GROUPS = [
    ("Capacity", ["ctx-size", "cache-type-k", "cache-type-v", "flash-attn"]),
    ("Sampling", ["temp", "top-k", "top-p"]),
    ("Runtime", ["n-gpu-layers", "n-cpu-moe", "batch-size", "ubatch-size",
                 "load-mode"]),
]

_QUICK_COLUMNS = {"Capacity": 2, "Sampling": 1, "Runtime": 2}

_GROUP_ACCENTS = {
    "Capacity": "panel_accent",
    "Sampling": "panel_request",
    "Runtime": "panel_ok",
}

_ADVANCED_GROUPS = {
    "Capacity": {"Core", "KV cache", "RoPE"},
    "Sampling": {"Sampling", "Repetition"},
    "Runtime": {"Chat & templates", "Performance", "Multimodal",
                "Speculative decoding", "Router"},
}

_TAB_ACCENTS = {
    "Core": "panel_accent",
    "Sampling": "panel_request",
    "Repetition": "panel_warn",
    "Chat & templates": "panel_num",
    "KV cache": "panel_ok",
    "Performance": "panel_accent",
    "RoPE": "panel_error",
    "Multimodal": "panel_request",
    "Speculative decoding": "panel_warn",
    "Router": "panel_ok",
}

_SECTION_KEYS = {title: {field[0] for field in fields}
                 for title, fields in _SECTIONS}
_GROUP_KEYS = {
    group: set().union(*(_SECTION_KEYS[title] for title in sections))
           - {"load-on-startup"}
    for group, sections in _ADVANCED_GROUPS.items()
}

_PARAM_HELP = {
    "ctx-size": "Prompt context size. Zero uses the value stored in the model; larger contexts use more KV-cache memory.",
    "n-predict": "Maximum tokens to generate. -1 allows unlimited generation.",
    "n-gpu-layers": "Maximum model layers stored in VRAM. More layers usually improve speed but consume more VRAM.",
    "n-cpu-moe": "Number of Mixture-of-Experts layers kept on CPU to reduce VRAM use.",
    "batch-size": "Logical prompt-processing batch. Larger values may improve throughput but use more memory.",
    "ubatch-size": "Physical prompt-processing batch. It controls peak working memory and should not exceed Batch size.",
    "flash-attn": "Enables fused attention kernels. Auto lets llama.cpp decide based on backend and model support.",
    "seed": "Random-number seed. -1 chooses a random seed for each server start.",
    "temp": "Sampling randomness. Lower values are more deterministic; higher values are more varied.",
    "top-k": "Restricts sampling to the K most likely tokens. Zero disables the filter.",
    "top-p": "Keeps the smallest token set whose cumulative probability reaches P. 1 disables the filter.",
    "min-p": "Drops tokens whose probability is below this fraction of the most likely token. Zero disables it.",
    "typical-p": "Locally typical sampling threshold. 1 disables this filter.",
    "top-nsigma": "Keeps tokens within N standard deviations of the top logit. -1 disables this filter.",
    "xtc-probability": "Probability of applying XTC sampling. Zero disables XTC.",
    "xtc-threshold": "Probability threshold used by XTC to remove likely tokens. 1 disables the threshold.",
    "mirostat": "Adaptive perplexity sampling: 0 off, 1 Mirostat, 2 Mirostat 2.0. It ignores Top-K, Top-P and Typical-P.",
    "mirostat-lr": "Mirostat learning rate (eta). Higher values adapt sampling more aggressively.",
    "mirostat-ent": "Target entropy (tau) maintained by Mirostat.",
    "repeat-last-n": "Recent tokens checked for repetition. 0 disables it; -1 uses the full context.",
    "repeat-penalty": "Penalty applied to repeated tokens. 1 disables the penalty.",
    "presence-penalty": "Penalizes a token once if it has appeared. Zero disables it.",
    "frequency-penalty": "Penalizes tokens in proportion to how often they appeared. Zero disables it.",
    "dry-multiplier": "Strength of DRY repetition suppression. Zero disables DRY.",
    "dry-base": "Exponential base controlling how quickly the DRY penalty grows with repeated sequence length.",
    "dry-allowed-length": "Repeated sequence length allowed before DRY begins penalizing it.",
    "dry-penalty-last-n": "Recent tokens searched by DRY. -1 uses the full context; 0 disables the search.",
    "dry-sequence-breaker": "Sequence boundary for DRY. Setting one replaces the default breakers; use 'none' for none.",
    "jinja": "Enables llama.cpp's Jinja chat-template engine. Required for custom template files and tool use.",
    "reasoning": "Controls reasoning/thinking: Auto detects support from the model's chat template.",
    "reasoning-format": "Controls where parsed thoughts are returned: content, reasoning_content, or both for legacy clients.",
    "reasoning-budget": "Maximum reasoning tokens. -1 is unrestricted; 0 ends reasoning immediately.",
    "chat-template-file": "Custom Jinja chat-template file. The model's embedded template is used when empty.",
    "chat-template-kwargs": "Extra values passed to the chat template as a valid JSON object.",
    "cache-type-k": "Data type for the key side of the KV cache. Quantized types reduce memory at some quality cost.",
    "cache-type-v": "Data type for the value side of the KV cache. Quantized types reduce memory at some quality cost.",
    "cache-reuse": "Minimum cached token chunk to reuse via KV shifting. Zero disables reuse; prompt caching must be on.",
    "cache-ram": "Maximum shared prompt-cache size in MiB. -1 is unlimited; 0 disables the shared cache.",
    "swa-checkpoints": "Maximum context checkpoints kept per server slot for Sliding Window Attention.",
    "swa-full": "Uses a full-size Sliding Window Attention cache, increasing memory use.",
    "no-kv-offload": "Keeps the KV cache on CPU instead of offloading it to the GPU.",
    "no-cache-prompt": "Disables prompt caching, so repeated prompt prefixes are processed again.",
    "cpu-moe": "Keeps all Mixture-of-Experts weights on CPU to save VRAM, usually at a speed cost.",
    "load-mode": "Model loading strategy. This is the supported replacement for the older mmap and mlock toggles.",
    "device": "Comma-separated devices used for model offloading. Leave empty for automatic selection; use 'none' for CPU only.",
    "fit": "Lets llama.cpp adjust unset arguments to fit available device memory.",
    "fit-target": "Free-memory margin, in MiB per device, reserved by automatic fitting.",
    "fit-ctx": "Smallest context size automatic fitting may select when reducing memory use.",
    "main-gpu": "GPU index used with single-GPU split mode, or for intermediate results and KV in row mode.",
    "split-mode": "Multi-GPU distribution: layer pipelines layers and KV; row splits weights; none uses one GPU.",
    "tensor-split": "Relative model allocation per GPU, comma-separated; for example, 3,1 assigns a 75/25 split.",
    "rope-scaling": "RoPE context-scaling method. Leave empty to use the model's setting.",
    "rope-freq-base": "RoPE base frequency for NTK-aware scaling. Empty uses the value stored in the model.",
    "rope-freq-scale": "RoPE frequency scale; a value N expands context by a factor of 1/N.",
    "mmproj": "Multimodal projector file paired with the model for image input.",
    "no-mmproj-offload": "Keeps the multimodal projector on CPU instead of offloading it to the GPU.",
    "spec-type": "One or more speculative-decoding strategies used to draft tokens before verification.",
    "spec-draft-n-max": "Maximum tokens proposed by the draft model per speculative-decoding step.",
    "spec-draft-n-min": "Minimum draft tokens required for a speculative-decoding step.",
    "spec-draft-model": "Companion model used to draft speculative tokens before the main model verifies them.",
    "spec-draft-device": "Comma-separated devices used to offload the draft model independently of the main model.",
    "spec-draft-ngl": "Maximum draft-model layers stored in VRAM: an exact number, auto, or all.",
    "cache-type-k-draft": "Data type for the draft model's key KV cache.",
    "cache-type-v-draft": "Data type for the draft model's value KV cache.",
    "load-on-startup": "Loads this route when llama-server starts instead of waiting for its first request.",
    "embedding": "Restricts this route to embeddings. Enable only for a dedicated embedding model.",
    "stop-timeout": "Seconds the router waits after requesting unload before forcefully stopping this model.",
    "sleep-idle-seconds": "Unloads the model and its KV cache after this many idle seconds. -1 or empty disables sleep.",
}

# The sampler params a sampling preset owns — applying a preset clears all of
# them first, so presets combine cleanly with any profile (pi-test rule).
_SAMPLING_KEYS = [
    "temp", "top-k", "top-p", "min-p", "typical-p", "top-nsigma",
    "xtc-probability", "xtc-threshold", "mirostat", "mirostat-lr",
    "mirostat-ent",
    "repeat-last-n", "repeat-penalty", "presence-penalty", "frequency-penalty",
    "dry-multiplier", "dry-base", "dry-allowed-length", "dry-penalty-last-n",
]

# Keys owned by dedicated fields — kept out of the free-form box.
_STRUCTURED = {f[0] for _, fields in _SECTIONS for f in fields}
_LEGACY_LOAD_KEYS = {"mlock", "no-mmap"}


def _migrate_load_mode(params: dict) -> dict:
    """Translate legacy loading toggles for display without mutating storage."""
    migrated = dict(params)
    if "load-mode" not in migrated:
        locked = str(migrated.get("mlock", "")).lower() == "true"
        no_mmap = str(migrated.get("no-mmap", "")).lower() == "true"
        if locked:
            migrated["load-mode"] = "mlock" if no_mmap else "mmap+mlock"
        elif no_mmap:
            migrated["load-mode"] = "none"
    for key in _LEGACY_LOAD_KEYS:
        migrated.pop(key, None)
    return migrated


def _context_limit(meta: dict) -> int:
    """Useful slider ceiling, preferring the GGUF's trained context."""
    return max(4096, int(meta.get("ctx", 0) or 0) or 32768)


def _parse_extra(text: str) -> dict:
    """Parse 'key = value' lines into a params dict (str values)."""
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        for sep in ("=", ":"):
            if sep in line:
                k, _, v = line.partition(sep)
                k, v = k.strip(), v.strip()
                if k:
                    out[k] = v
                break
    return out


def _format_extra(params: dict) -> str:
    return "\n".join(f"{k} = {v}" for k, v in params.items()
                     if k not in _STRUCTURED and k not in _LEGACY_LOAD_KEYS)


def _load_sampling_presets() -> list[dict]:
    """Curated per-model-family sampling presets, bundled in assets/."""
    fp = Path(__file__).resolve().parents[2] / "assets" / "sampling-presets.json"
    try:
        return json.loads(fp.read_text(encoding="utf-8")).get("presets", [])
    except (OSError, ValueError):
        return []


class ProfilesPage(Page):
    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        c = self.c
        self.header(t("model workspace"), t("Models & Profiles"),
                    t("Models, routes and generated preset in one place"))
        workspace_nav = tk.Frame(self, bg=c["bg"])
        workspace_nav.pack(fill="x", padx=PAGE_PAD, pady=(0, 12))
        self._workspace_nav = {
            "models": NavItem(workspace_nav, c, t("Models"),
                              command=lambda: self._show_workspace("models")),
            "profiles": NavItem(workspace_nav, c, t("Profiles"),
                                command=lambda: self._show_workspace("profiles")),
        }
        self._workspace_nav["models"].pack(side="left", padx=(0, 4))
        self._workspace_nav["profiles"].pack(side="left")
        self._workspace_nav["profiles"].set_active(True)

        self._cols = ScrollFrame(self, dict(c, bg=c["bg"]), fill_height=True)
        self._cols.pack(fill="both", expand=True, padx=PAGE_PAD,
                        pady=(0, PAGE_PAD))
        cols = self._cols.body
        cols.columnconfigure(0, weight=0)
        cols.columnconfigure(1, weight=3)
        cols.columnconfigure(2, weight=2)
        cols.rowconfigure(0, weight=1)

        # ── Left: models → profiles tree ─────────────────────────────────────
        self._profile_list = tk.Frame(cols, bg=c["bg"], width=210)
        left = self._profile_list
        left.grid(row=0, column=0, sticky="nw", padx=(0, 14))

        treepanel = tk.Frame(left, bg=c["surface"],
                             highlightbackground=c["panel_accent"],
                             highlightthickness=1)
        treepanel.pack(fill="x")
        tk.Label(treepanel, text=theme.track(t("Models & profiles")),
                 bg=c["surface"], fg=c["panel_accent"],
                 font=theme.mono(8, "bold"),
                 padx=10, pady=9).pack(anchor="w")
        self._tree = ttk.Treeview(treepanel, columns=("on",), show="tree",
                                  selectmode="browse")
        self._tree.column("#0", width=162)
        self._tree.column("on", width=40, anchor="center", stretch=False)
        vbar = AutoScrollbar(treepanel, orient="vertical",
                             command=self._tree.yview)
        self._tree.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self._tree.pack(fill="x", padx=1, pady=1)
        self._tree.tag_configure("model", foreground=c["muted"])
        self._tree.tag_configure("model-active", foreground=c["accent"])
        self._tree.tag_configure("active", foreground=c["accent_hi"])
        self._tree.tag_configure("off", foreground=c["faint"])
        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        enable_row_hover(self._tree, c)

        lbtns = tk.Frame(left, bg=c["bg"])
        lbtns.pack(fill="x", pady=(10, 0))
        PillButton(lbtns, c, t("New"), kind="primary", size=9, padx=12, height=28,
                   command=self._new).pack(side="left")
        PillButton(lbtns, c, t("Delete"), size=9, padx=12, height=28,
                   command=self._delete).pack(side="left", padx=(6, 0))

        lbtns2 = tk.Frame(left, bg=c["bg"])
        lbtns2.pack(fill="x", pady=(6, 0))
        PillButton(lbtns2, c, t("Activate all"), kind="accent",
                   size=9, padx=12, height=28,
                   command=lambda: self._set_all_active(True)).pack(
                       anchor="w")
        PillButton(lbtns2, c, t("Deactivate all"), size=9, padx=12, height=28,
                   command=lambda: self._set_all_active(False)
                   ).pack(anchor="w", pady=(6, 0))

        # ── Right: editor ────────────────────────────────────────────────────
        self._editor = tk.Frame(cols, bg=c["surface"],
                                highlightbackground=c["panel_accent"],
                                highlightthickness=1)
        self._editor.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        self._fields: dict[str, tuple] = {}  # key → (kind, widget-or-var, extra)
        self._autosave_id: str | None = None
        self._loading_profile = False
        self._build_editor()

        self._current: str | None = None
        self._preset_host = tk.Frame(cols, bg=c["bg"])
        self._preset_host.grid(row=0, column=2, sticky="nsew")
        self._preset_view: PresetPage | None = PresetPage(
            self._preset_host, self.ctx, embedded=True)
        self._preset_view.pack(fill="both", expand=True)
        self._bind_outer_scroll(self._preset_view._text)
        self._library_view: ModelsPage | None = None
        self._compact_profiles: bool | None = None
        self._shown_once = False
        self._cols.bind("<Configure>", self._on_profiles_resize, add="+")
        self.after_idle(self._on_profiles_resize)
        self.subscribe("preset_imported", self._on_preset_imported)
        self._refresh_tree()

    def _on_profiles_resize(self, event=None) -> None:
        """Stack the preset below the editor before either panel gets cramped."""
        width = event.width if event is not None else self._cols.winfo_width()
        if width <= 1:
            return
        compact = width < 1100
        if compact == self._compact_profiles:
            return
        self._compact_profiles = compact
        if compact:
            self._profile_list.grid_configure(row=0, column=0, rowspan=2,
                                              sticky="nsw", padx=(0, 14))
            self._editor.grid_configure(row=0, column=1, columnspan=2,
                                        sticky="nsew", padx=0)
            self._preset_host.grid_configure(row=1, column=1, columnspan=2,
                                              sticky="nsew", pady=(12, 0))
            self._cols.body.rowconfigure(0, weight=0)
            self._cols.body.rowconfigure(1, weight=0)
        else:
            self._profile_list.grid_configure(row=0, column=0, rowspan=1,
                                              sticky="nw", padx=(0, 14))
            self._editor.grid_configure(row=0, column=1, columnspan=1,
                                        sticky="nsew", padx=(0, 12))
            self._preset_host.grid_configure(row=0, column=2, columnspan=1,
                                              sticky="nsew", pady=0)
            self._cols.body.rowconfigure(0, weight=1)
            self._cols.body.rowconfigure(1, weight=0, minsize=0)

    def _show_workspace(self, name: str) -> None:
        showing_profiles = self._cols.winfo_ismapped()
        if name == "models" and showing_profiles:
            self._flush_autosave()
            self._cols.pack_forget()
            if self._library_view is None:
                self._library_view = ModelsPage(self, self.ctx, embedded=True)
            self._library_view.pack(fill="both", expand=True)
        elif name == "profiles" and not showing_profiles:
            if self._library_view is not None:
                self._library_view.pack_forget()
            self._cols.pack(fill="both", expand=True, padx=PAGE_PAD,
                            pady=(0, PAGE_PAD))
            self._refresh_tree(keep=(f"p:{self._current}"
                                     if self._current else None))
        for key, item in self._workspace_nav.items():
            item.set_active(key == name)

    # ── Services ─────────────────────────────────────────────────────────────

    @property
    def _profiles(self):
        return self.ctx.services["profiles"]

    @property
    def _models(self):
        return self.ctx.services["models"]

    # ── Editor construction ──────────────────────────────────────────────────

    def _build_editor(self) -> None:
        c = self.c
        # The workspace's outer ScrollFrame owns vertical movement. Keeping
        # the editor as a plain frame avoids adjacent nested scrollbars when
        # the preset stacks below it in compact mode.
        self._editor_body = tk.Frame(self._editor, bg=c["surface"])
        self._editor_body.pack(fill="both", expand=True, padx=18, pady=16)
        outer = self._editor_body

        top = tk.Frame(outer, bg=c["surface"])
        top.pack(fill="x")
        section_label(top, c, t("Profile"), c["panel_accent"]).pack(side="left")
        self._save_state = tk.Label(top, text=t("Saved automatically"),
                                    bg=c["surface"], fg=c["faint"],
                                    font=theme.ui(8))
        self._save_state.pack(side="right")
        reset_profile = PillButton(
            top, c, t("Reset profile"), size=8, padx=10, height=26,
            command=self._reset_profile)
        reset_profile.pack(side="right", padx=(0, 10))
        Tooltip(reset_profile, c,
                t("Restore the default parameters while keeping the profile name and route alias."))

        self._copy_map: dict[str, str] = {}  # display → profile_id
        self._presets = _load_sampling_presets()

        self._model_summary = tk.Frame(
            outer, bg=c["surface_hi"], highlightbackground=c["panel_accent"],
            highlightthickness=1)
        self._model_summary.pack(fill="x", pady=(12, 8))
        self._model_name = tk.Label(
            self._model_summary, text="", bg=c["surface_hi"], fg=c["text"],
            font=theme.ui(11, "bold"), anchor="w")
        self._model_name.pack(fill="x", padx=12, pady=(9, 2))
        self._model_meta = tk.Label(
            self._model_summary, text="", bg=c["surface_hi"], fg=c["muted"],
            font=theme.mono(8), anchor="w")
        self._model_meta.pack(fill="x", padx=12, pady=(0, 9))

        # Identity row stays fixed above the scrolling parameter form.
        ident = tk.Frame(outer, bg=c["surface"])
        ident.pack(fill="x", pady=(12, 6))
        ident.columnconfigure(1, weight=1)
        ident.columnconfigure(0, minsize=90)
        self._label(ident, 0, 0, t("Name"))
        self._name = ttk.Entry(ident, width=22, font=theme.mono(9))
        self._name.grid(row=0, column=1, sticky="ew")
        self._label(ident, 1, 0, t("Route alias"))
        self._alias = ttk.Entry(ident, width=22, font=theme.mono(9))
        self._alias.grid(row=1, column=1, sticky="ew")
        self._label(ident, 2, 0, t("Active"))
        self._active = tk.BooleanVar()
        ttk.Checkbutton(ident, variable=self._active,
                        takefocus=False).grid(row=2, column=1, sticky="w")
        self._label(ident, 3, 0, t("Load on startup"))
        self._make_field(ident, 3, 1, "load-on-startup", "bool", None)

        # The essential inference controls stay visible.  The complete form
        # remains available below for less common llama-server options.
        section_label(outer, c, t("Parameters"), c["panel_accent"]).pack(
            anchor="w", pady=(12, 4))
        quick_fields = {field[0]: field for _, fields in _SECTIONS
                        for field in fields if field[0] in _QUICK_FIELD_KEYS}
        self._sliders: dict[str, tuple[ttk.Scale, tk.Label, object]] = {}
        self._quick_cards: list[CollapsibleCard] = []
        self._quick_panels: list[tk.Frame] = []
        self._advanced_groups: dict[str, tk.Frame] = {}
        self._advanced_visible = False
        for group_title, keys in _QUICK_GROUPS:
            card = CollapsibleCard(
                outer, c, t(group_title), expanded=True, pad=12,
                state_key=f"profile-group-{group_title}",
                accent=c[_GROUP_ACCENTS[group_title]])
            card.pack(fill="x", pady=(0, 6))
            self._quick_cards.append(card)
            reset_group = PillButton(
                card.header, c, t("Reset"), size=7, padx=8, height=24,
                command=lambda keys=_GROUP_KEYS[group_title]:
                self._reset_params(keys))
            reset_group.pack(side="right", padx=(0, 6))
            Tooltip(reset_group, c,
                    t("Reset every parameter in this category."))
            panel = tk.Frame(card.content, bg=c["surface"])
            panel.pack(fill="x")
            self._quick_panels.append(panel)
            columns = _QUICK_COLUMNS[group_title]
            for col in range(columns):
                panel.columnconfigure(col, weight=1,
                                      uniform="quick-param-cell")
            row = 0
            slot = 0
            for key in keys:
                _key, label, kind, extra = quick_fields[key]
                wide = key in _WIDE_FIELDS
                if wide and slot:
                    row += 1
                    slot = 0
                cell = tk.Frame(panel, bg=c["surface"])
                cell.grid(row=row, column=0 if wide else slot,
                          columnspan=columns if wide else 1, sticky="ew",
                          padx=(0, 8), pady=(0, 7))
                cell.columnconfigure(0, weight=1)
                self._label(cell, 0, 0, t(label))
                if key in _SLIDER_FIELDS:
                    self._make_slider_field(cell, key, kind, extra)
                else:
                    self._make_field(cell, 1, 0, key, kind, extra)
                if wide:
                    row += 1
                else:
                    slot += 1
                    if slot == columns:
                        row += 1
                        slot = 0
            self._advanced_groups[group_title] = tk.Frame(
                card.content, bg=c["surface"])

        default_fields = dict(self._fields)
        default_sliders = dict(self._sliders)

        pickers = tk.Frame(outer, bg=c["surface"])
        pickers.pack(fill="x", pady=(8, 8), before=self._quick_cards[0])
        pickers.columnconfigure(0, weight=1, uniform="profile-picker")
        pickers.columnconfigure(1, weight=1, uniform="profile-picker")
        self._preset_cb = ttk.Combobox(pickers, state="readonly", width=18,
                                       font=theme.ui(8))
        self._preset_cb.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._preset_cb.bind("<<ComboboxSelected>>", self._on_sampling_preset)
        self._copy_cb = ttk.Combobox(pickers, state="readonly", width=18,
                                     font=theme.ui(8))
        self._copy_cb.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._copy_cb.bind("<<ComboboxSelected>>", self._on_copy_from)

        toggle = tk.Frame(
            outer, bg=c["surface"], cursor="hand2", takefocus=True,
            highlightthickness=1, highlightbackground=c["border"],
            highlightcolor=c["accent_hi"])
        toggle._keyboard_nav = True
        self._params_toggle_host = toggle
        toggle.pack(fill="x", pady=(0, 8), before=self._quick_cards[0])
        self._params_toggle = tk.Label(
            toggle, text="▾  " + t("Advanced parameters"),
            bg=c["surface"], fg=c["muted"], font=theme.mono(8, "bold"),
            cursor="hand2")
        self._params_toggle.pack(anchor="w")
        for widget in (toggle, self._params_toggle):
            widget.bind("<Button-1>", self._activate_params_toggle)
        toggle.bind("<Key-space>", self._activate_params_toggle)
        toggle.bind("<Key-Return>", self._activate_params_toggle)

        for title, fields in _SECTIONS:
            group_title = next(group for group, sections in
                               _ADVANCED_GROUPS.items() if title in sections)
            body = self._advanced_groups[group_title]
            advanced_fields = [field for field in fields
                               if field[0] != "load-on-startup"]
            if not advanced_fields:
                continue
            subcard = CollapsibleCard(
                body, c, t(title), expanded=False, pad=10,
                state_key=f"profile-advanced-{title}",
                accent=c[_TAB_ACCENTS[title]])
            subcard.pack(fill="x", pady=(6, 0))
            reset_section = PillButton(
                subcard.header, c, t("Reset"), size=7, padx=8, height=22,
                command=lambda keys=_SECTION_KEYS[title] - {"load-on-startup"}:
                self._reset_params(keys))
            reset_section.pack(side="right", padx=(0, 6))
            Tooltip(reset_section, c,
                    t("Reset every parameter in this section."))
            grid = subcard.content
            columns = _SECTION_COLUMNS[title]
            for col in range(columns):
                grid.columnconfigure(col, weight=1, uniform="param-cell")

            values = [field for field in advanced_fields if field[2] != "bool"]
            switches = [field for field in advanced_fields if field[2] == "bool"]
            row = 0
            slot = 0
            for key, label, kind, extra in values:
                wide = kind == "file" or key in _WIDE_FIELDS
                if wide:
                    if slot:
                        row += 1
                        slot = 0
                    cell = tk.Frame(grid, bg=c["surface"])
                    cell.grid(row=row, column=0, columnspan=columns,
                              sticky="ew", padx=(0, 6))
                    cell.columnconfigure(0, weight=1)
                    self._label(cell, 0, 0, t(label))
                    if key in _SLIDER_FIELDS:
                        self._make_slider_field(cell, key, kind, extra)
                    else:
                        self._make_field(cell, 1, 0, key, kind, extra)
                    row += 1
                    continue
                cell = tk.Frame(grid, bg=c["surface"])
                cell.grid(row=row, column=slot, sticky="ew", padx=(0, 6))
                cell.columnconfigure(0, weight=1)
                self._label(cell, 0, 0, t(label))
                if key in _SLIDER_FIELDS:
                    self._make_slider_field(cell, key, kind, extra)
                else:
                    self._make_field(cell, 1, 0, key, kind, extra)
                slot += 1
                if slot == columns:
                    row += 1
                    slot = 0
            if slot:
                row += 1

            if switches:
                switch_grid = tk.Frame(grid, bg=c["surface"])
                switch_grid.grid(row=row, column=0, columnspan=columns,
                                 sticky="ew", pady=(5, 0))
                for col in range(columns):
                    switch_grid.columnconfigure(col, weight=1,
                                                uniform="switch-cell")
                for index, (key, label, kind, extra) in enumerate(switches):
                    switch_row, switch_col = divmod(index, columns)
                    cell = tk.Frame(switch_grid, bg=c["surface"])
                    cell.grid(row=switch_row, column=switch_col, sticky="ew",
                              padx=(0, 8), pady=2)
                    cell.columnconfigure(0, weight=1)
                    self._label(cell, 0, 0, t(label))
                    self._make_field(cell, 0, 1, key, kind, extra)

        row2 = tk.Frame(body, bg=c["surface"])
        row2.pack(fill="x", pady=(10, 4))
        tk.Label(row2, text=t("Additional parameters (key = value per line)"),
                 bg=c["surface"], fg=c["muted"], font=theme.ui(9)).pack(side="left")
        tk.Label(row2, text=t("same flags as llama-server"),
                 bg=c["surface"], fg=c["faint"], font=theme.ui(8)).pack(side="right")
        self._extra = self._text(body, 6)
        self._advanced_fields = dict(self._fields)
        self._default_fields = {**self._advanced_fields, **default_fields}
        self._advanced_sliders = dict(self._sliders)
        self._default_sliders = default_sliders
        self._fields = self._default_fields
        self._sliders = self._default_sliders
        self._wire_autosave()

        self._editor_hint = tk.Label(self._editor, text="", bg=c["surface"],
                                     fg=c["muted"], font=theme.ui(10))

    def _reset_params(self, keys: set[str] | None = None) -> None:
        profile = self._profiles.get(self._current) if self._current else None
        defaults = (self._profiles.template_params(profile.name)
                    if profile else {})
        params = self._collect_params()
        if keys is None:
            params = defaults
        else:
            for key in keys:
                if key in defaults:
                    params[key] = defaults[key]
                else:
                    params.pop(key, None)
        self._loading_profile = True
        try:
            self._fill_params(params)
        finally:
            self._loading_profile = False
        self._schedule_save(0)

    def _reset_profile(self) -> None:
        if not self._current or not messagebox.askyesno(
                t("Reset profile"),
                t("Restore the default parameters for this profile?"),
                parent=self):
            return
        self._reset_params()

    def _activate_params_toggle(self, _event=None) -> str:
        self._params_toggle_host.focus_set()
        self._toggle_params()
        return "break"

    def _toggle_params(self) -> None:
        params = self._collect_params()
        self._advanced_visible = not self._advanced_visible
        if not self._advanced_visible:
            for group in self._advanced_groups.values():
                group.pack_forget()
            for panel in self._quick_panels:
                panel.pack(fill="x")
            self._fields = self._default_fields
            self._sliders = self._default_sliders
            self._params_toggle.configure(
                text="▾  " + t("Advanced parameters"))
        else:
            for panel in self._quick_panels:
                panel.pack_forget()
            for group in self._advanced_groups.values():
                group.pack(fill="x")
            self._fields = self._advanced_fields
            self._sliders = self._advanced_sliders
            self._params_toggle.configure(
                text="▴  " + t("Advanced parameters"))

        self._loading_profile = True
        try:
            profile = self._profiles.get(self._current) if self._current else None
            model = self._models.get(profile.model_id) if profile else None
            if model:
                self._update_model_context(model)
            self._fill_params(params)
        finally:
            self._loading_profile = False

    def _label(self, parent, row, col, text) -> None:
        tk.Label(parent, text=text, bg=parent.cget("bg"), fg=self.c["muted"],
                 font=theme.ui(9)).grid(row=row, column=col, sticky="w",
                                        pady=4, padx=(0, 10))

    def _make_field(self, grid, row, col, key, kind, extra,
                    columnspan: int = 1) -> None:
        c = self.c
        if kind == "bool":
            var = tk.BooleanVar()
            check = ttk.Checkbutton(grid, variable=var, takefocus=False)
            check.grid(row=row, column=col, sticky="w", pady=4,
                       padx=(0, 12), columnspan=columnspan)
            self._add_param_tooltip(check, key)
            self._fields[key] = (kind, var, extra)
        elif kind in ("choice", "combo"):
            values, _omit = extra
            cb = ttk.Combobox(grid, values=values, width=9,
                              state="readonly" if kind == "choice" else "normal",
                              font=theme.ui(9))
            cb.grid(row=row, column=col, sticky="w", pady=4, padx=(0, 12),
                    columnspan=columnspan)
            # A wheel over a closed combobox normally changes its selection.
            # In the profile form it should continue scrolling the editor.
            self._bind_outer_scroll(cb)
            self._add_param_tooltip(cb, key)
            self._fields[key] = (kind, cb, extra)
        elif kind == "file":
            cell = tk.Frame(grid, bg=c["surface"])
            cell.grid(row=row, column=col, sticky="ew", pady=4, padx=(0, 12),
                      columnspan=columnspan)
            en = ttk.Entry(cell, font=theme.mono(9))
            en.pack(side="left", fill="x", expand=True)
            self._add_param_tooltip(en, key)
            PillButton(cell, c, "…", size=9, padx=8, height=24,
                       command=lambda e=en: self._browse(e)
                       ).pack(side="left", padx=(4, 0))
            if extra:  # auto-detect hook
                PillButton(cell, c, t("Auto"), size=8, padx=8, height=24,
                           command=lambda e=en, fn=extra:
                           getattr(self, fn)(e)).pack(side="left", padx=(4, 0))
            self._fields[key] = (kind, en, extra)
        else:  # int / float / str
            en = ttk.Entry(grid, width=8 if kind in ("int", "float") else 11,
                           font=theme.mono(9))
            en.grid(row=row, column=col,
                    sticky="w" if kind in ("int", "float") else "ew",
                    pady=4, padx=(0, 12),
                    columnspan=columnspan)
            self._add_param_tooltip(en, key)
            self._fields[key] = (kind, en, extra)

    def _add_param_tooltip(self, widget: tk.Widget, key: str) -> None:
        help_text = _PARAM_HELP.get(key)
        if help_text:
            Tooltip(widget, self.c, t(help_text))

    def _make_slider_field(self, parent, key, kind, extra) -> None:
        """Entry + scale for quick mouse and precise keyboard tuning."""
        low, high, step, _ = _SLIDER_FIELDS[key]
        bg = parent.cget("bg")
        row = tk.Frame(parent, bg=bg)
        row.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        row.columnconfigure(0, weight=1)
        value = tk.StringVar()
        scale = ttk.Scale(row, from_=low, to=high, orient="horizontal")
        scale.grid(row=0, column=0, sticky="ew")
        entry = ttk.Entry(row, width=7, textvariable=value,
                          font=theme.mono(9), justify="right")
        entry.grid(row=0, column=1, padx=(8, 0))
        limit = tk.Label(row, text="", bg=bg,
                         fg=self.c["faint"], font=theme.mono(7))
        limit.grid(row=1, column=0, sticky="w")
        syncing = False

        def moved(raw) -> None:
            if syncing:
                return
            snapped = round(float(raw) / step) * step
            text = str(int(snapped)) if kind == "int" else f"{snapped:g}"
            if value.get() != text:
                value.set(text)
                self._schedule_save()

        def sync_scale(_event=None) -> None:
            nonlocal syncing
            try:
                syncing = True
                scale.set(float(value.get()))
            except ValueError:
                pass
            finally:
                syncing = False

        scale.configure(command=moved)
        entry.bind("<KeyRelease>", sync_scale, add="+")
        self._add_param_tooltip(scale, key)
        self._add_param_tooltip(entry, key)
        self._fields[key] = (kind, entry, extra)
        self._sliders[key] = (scale, limit, sync_scale)

    def _update_model_context(self, model) -> None:
        meta = model.meta or {}
        trained_ctx = int(meta.get("ctx", 0) or 0)
        ctx_max = _context_limit(meta)
        ctx_scale, ctx_limit, _sync = self._sliders["ctx-size"]
        ctx_scale.configure(from_=512, to=ctx_max)
        ctx_limit.configure(text=t("trained max: {value}",
                                   value=f"{ctx_max:,}"))
        for key in ("temp", "top-p"):
            low, high, _step, _kind = _SLIDER_FIELDS[key]
            self._sliders[key][1].configure(text=f"{low:g} — {high:g}")

        facts = [meta.get("arch"), meta.get("params"), meta.get("quant"),
                 fmt_bytes(model.size),
                 (f"CTX {trained_ctx:,}" if trained_ctx else None)]
        self._model_name.configure(text=model.name)
        self._model_meta.configure(
            text="  ·  ".join(str(value) for value in facts if value))

    def _scroll_editor_from_choice(self, event_or_steps) -> str:
        """Scroll the editor instead of cycling a combobox with the wheel."""
        if isinstance(event_or_steps, int):
            steps = event_or_steps
        else:
            steps = -1 if event_or_steps.delta > 0 else (
                1 if event_or_steps.delta < 0 else 0)
        self._cols.scroll_units(steps)
        return "break"

    def _bind_outer_scroll(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self._scroll_editor_from_choice)
        widget.bind("<Button-4>",
                    lambda _e: self._scroll_editor_from_choice(-1))
        widget.bind("<Button-5>",
                    lambda _e: self._scroll_editor_from_choice(1))

    def _text(self, parent: tk.Widget, height: int) -> tk.Text:
        c = self.c
        txt = tk.Text(parent, height=height, bg=c["inset"], fg=c["text"],
                      insertbackground=c["text"], bd=0, padx=8, pady=6,
                      font=theme.mono(9), wrap="none",
                      highlightthickness=1, highlightbackground=c["border"],
                      highlightcolor=c["accent_dn"])
        self._bind_outer_scroll(txt)
        txt.pack(fill="x")
        return txt

    def _browse(self, entry: ttk.Entry) -> None:
        path = filedialog.askopenfilename(
            parent=self, filetypes=[("GGUF / template", "*.gguf *.jinja *.*")])
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)
            self._schedule_save()

    def _detect_mmproj(self, entry: ttk.Entry) -> None:
        self._autofill(entry, self._models.detect_mmproj)

    def _detect_draft(self, entry: ttk.Entry) -> None:
        self._autofill(entry, self._models.detect_draft)

    def _autofill(self, entry: ttk.Entry, detector) -> None:
        p = self._profiles.get(self._current) if self._current else None
        if not p:
            return
        result = detector(p.model_id)
        if result.get("path"):
            entry.delete(0, "end")
            entry.insert(0, result["path"])
            self._schedule_save()
        else:
            msg = t("ambiguous — pick manually") if result.get("ambiguous") \
                else t("nothing found next to the model")
            old = entry.get()
            entry.delete(0, "end")
            entry.insert(0, msg)
            entry.after(1800, lambda: (entry.delete(0, "end"),
                                       entry.insert(0, old)))

    # ── Tree refresh / selection ─────────────────────────────────────────────

    def _refresh_tree(self, keep: str | None = None) -> None:
        self._tree.delete(*self._tree.get_children())
        models = [m for m in self._models.list() if m.enabled]
        row_count = sum(1 + len(self._profiles.list(m.id)) for m in models)
        self._tree.configure(height=max(3, min(12, row_count)))
        if not models:
            any_models = bool(self._models.list())
            message = (t("Enable a model first — then tune its profiles here.")
                       if any_models else
                       t("Add models first — then tune their profiles here."))
            self._show_hint(message)
            return
        for m in models:
            self._profiles.ensure_defaults(m.id)
            plist = self._profiles.list(m.id)
            n_active = sum(1 for p in plist if p.active)
            mid = f"m:{m.id}"
            # A disabled model never reaches models-preset.ini — show it (and
            # its profiles) dimmed so the Models toggle is visible here too.
            self._tree.insert("", "end", iid=mid, text=m.name, open=True,
                              values=(f"{n_active}/{len(plist)}",),
                              tags=(("model-active",) if n_active
                                    else ("model",)))
            for p in plist:
                self._tree.insert(mid, "end", iid=f"p:{p.id}", text=p.name,
                                  values=("☑" if p.active else "☐",),
                                  tags=(("active",) if p.active else ()))
        target = keep if keep and self._tree.exists(keep) else None
        if target is None:
            first = self._tree.get_children()
            kids = self._tree.get_children(first[0]) if first else ()
            target = kids[0] if kids else None
        if target:
            self._tree.selection_set(target)
            self._tree.see(target)

    def _selected(self) -> tuple[str, str] | None:
        """Return ('m'|'p', id) for the selected tree row."""
        sel = self._tree.selection()
        if not sel:
            return None
        kind, _, oid = sel[0].partition(":")
        return kind, oid

    def _selected_model_id(self) -> str | None:
        sel = self._selected()
        if not sel:
            return None
        kind, oid = sel
        if kind == "m":
            return oid
        p = self._profiles.get(oid)
        return p.model_id if p else None

    def _on_tree_click(self, e) -> None:
        # Click on the checkbox column toggles active state.
        if self._tree.identify_column(e.x) != "#1":
            return
        row = self._tree.identify_row(e.y)
        if not row:
            return
        kind, _, oid = row.partition(":")
        if kind == "p":
            p = self._profiles.get(oid)
            if p:
                self._profiles.set_active(oid, not p.active)
                self._refresh_tree(keep=row)
        elif kind == "m":
            plist = self._profiles.list(oid)
            self._profiles.set_active_all(
                oid, any(not p.active for p in plist))
            self._refresh_tree(keep=row)

    def _on_select(self) -> None:
        self._flush_autosave()
        sel = self._selected()
        if not sel:
            return
        kind, oid = sel
        if kind == "p":
            self._load_profile(oid)
        else:
            self._show_hint(t("Pick a profile to edit — or create one with New."))

    def _on_preset_imported(self, _data=None) -> None:
        """Reflect hand-edited INI values in the currently visible form."""
        if self._current:
            self._load_profile(self._current)

    # ── Editor load / save ───────────────────────────────────────────────────

    def _load_profile(self, pid: str) -> None:
        p = self._profiles.get(pid)
        if not p:
            return
        self._loading_profile = True
        try:
            self._current = pid
            self._show_editor()
            self._set(self._name, p.name)
            self._set(self._alias, p.route_alias)
            self._active.set(p.active)
            model = self._models.get(p.model_id)
            if model:
                self._update_model_context(model)
            self._fill_params(p.params)
            self._refresh_pickers(p)
        finally:
            self._loading_profile = False

    def _fill_params(self, params: dict) -> None:
        params = _migrate_load_mode(params)
        for key, (kind, w, extra) in self._fields.items():
            raw = params.get(key)
            if kind == "bool":
                w.set(str(raw).lower() == "true")
            elif kind in ("choice", "combo"):
                _values, omit = extra
                w.set(str(raw) if raw is not None
                      else (omit if omit is not None else _values[0]))
            else:
                # An unset param shows the llama-server default; leaving it
                # untouched keeps it out of the profile (see _collect_params).
                if raw is None:
                    raw = _LLAMA_DEFAULTS.get(key)
                self._set(w, "" if raw is None else str(raw))
                slider = self._sliders.get(key)
                if slider:
                    slider[2]()
        self._extra.delete("1.0", "end")
        self._extra.insert("1.0", _format_extra(params))

    def _refresh_pickers(self, p) -> None:
        # Sampling presets: mark the ones matching the model name as suggested.
        model = self._models.get(p.model_id)
        mname = model.name.lower() if model else ""
        names = []
        for pr in self._presets:
            hit = any(s in mname for s in pr.get("match", []))
            names.append(pr["name"] + (" ★" if hit else ""))
        self._preset_cb.configure(values=names)
        self._preset_cb.set(t("Sampling preset…") if names else "")

        # Copy params from every profile of every *other* model.
        self._copy_map = {}
        for m in self._models.list():
            if m.id == p.model_id:
                continue
            for prof in self._profiles.list(m.id):
                self._copy_map[f"{m.name} · {prof.name}"] = prof.id
        self._copy_cb.configure(values=list(self._copy_map))
        self._copy_cb.set(t("Copy params from…") if self._copy_map else "")

    def _on_sampling_preset(self, _e) -> None:
        idx = self._preset_cb.current()
        if idx < 0 or idx >= len(self._presets) or not self._current:
            return
        params = self._collect_params()
        for k in _SAMPLING_KEYS:
            params.pop(k, None)
        params.update(self._presets[idx].get("params", {}))
        self._fill_params(params)
        self._preset_cb.set(t("Sampling preset…"))
        self._schedule_save()

    def _on_copy_from(self, _e) -> None:
        src_id = self._copy_map.get(self._copy_cb.get())
        src = self._profiles.get(src_id) if src_id else None
        if not src or not self._current:
            return
        self._fill_params(dict(src.params))
        self._copy_cb.set(t("Copy params from…"))
        self._schedule_save()

    @staticmethod
    def _set(entry: ttk.Entry, value: str) -> None:
        entry.delete(0, "end")
        entry.insert(0, value)

    def _collect_params(self) -> dict:
        params = _parse_extra(self._extra.get("1.0", "end"))
        for key, (kind, w, extra) in self._fields.items():
            if kind == "bool":
                if w.get():
                    params[key] = "true"
            elif kind in ("choice", "combo"):
                _values, omit = extra
                val = w.get().strip()
                if val and val != omit:
                    params[key] = val
            elif kind == "int":
                val = w.get().strip()
                if val:
                    try:
                        parsed = int(val)
                    except ValueError:
                        continue
                    if parsed != _LLAMA_DEFAULTS.get(key):
                        params[key] = parsed
            elif kind == "float":
                val = w.get().strip()
                if val:
                    try:
                        parsed = float(val)
                    except ValueError:
                        continue
                    if parsed != _LLAMA_DEFAULTS.get(key):
                        params[key] = parsed
            else:  # str / file
                val = w.get().strip()
                if val and val != _LLAMA_DEFAULTS.get(key):
                    params[key] = val
        return params

    def _wire_autosave(self) -> None:
        entries = [self._name, self._alias]
        field_values = [*self._default_fields.values(),
                        *self._advanced_fields.values()]
        seen: set[int] = set()
        for kind, widget, _extra in field_values:
            if id(widget) in seen:
                continue
            seen.add(id(widget))
            if kind == "bool":
                widget.trace_add("write", lambda *_: self._schedule_save())
            else:
                entries.append(widget)
                if kind in ("choice", "combo"):
                    widget.bind("<<ComboboxSelected>>",
                                lambda _e: self._schedule_save(), add="+")
        self._active.trace_add("write", lambda *_: self._schedule_save())
        for entry in entries:
            entry.bind("<KeyRelease>", lambda _e: self._schedule_save(), add="+")
            entry.bind("<FocusOut>", lambda _e: self._schedule_save(0), add="+")
        self._extra.bind("<KeyRelease>",
                         lambda _e: self._schedule_save(), add="+")

    def _schedule_save(self, delay: int = 650) -> None:
        if self._loading_profile or not self._current:
            return
        if self._autosave_id is not None:
            self.after_cancel(self._autosave_id)
        self._save_state.configure(text=t("Saving…"), fg=self.c["warn"])
        self._autosave_id = self.after(delay, lambda: self._save(auto=True))

    def _flush_autosave(self) -> None:
        if self._autosave_id is None:
            return
        self.after_cancel(self._autosave_id)
        self._autosave_id = None
        if self._current:
            self._save(auto=True)

    def _save(self, auto: bool = False) -> None:
        self._autosave_id = None
        if not self._current:
            return
        try:
            self._profiles.update(self._current, {
                "name": self._name.get().strip() or "Profile",
                "route_alias": self._alias.get().strip(),
                "active": self._active.get(),
                "params": self._collect_params(),
            })
        except ValueError as exc:
            self._save_state.configure(text=str(exc), fg=self.c["error"])
            return
        self._save_state.configure(text=t("Saved automatically"),
                                   fg=self.c["faint"])
        if auto:
            iid = f"p:{self._current}"
            if self._tree.exists(iid):
                p = self._profiles.get(self._current)
                self._tree.item(iid, text=p.name,
                                values=("☑" if p.active else "☐",))
                self._tree.item(iid,
                                tags=(("active",) if p.active else ()))
                parent = self._tree.parent(iid)
                plist = self._profiles.list(p.model_id)
                self._tree.item(parent, values=(
                    f"{sum(1 for item in plist if item.active)}/{len(plist)}",))
                self._tree.item(
                    parent,
                    tags=(("model-active",)
                          if any(item.active for item in plist)
                          else ("model",)))
        else:
            self._refresh_tree(keep=f"p:{self._current}")

    def teardown(self) -> None:
        self._flush_autosave()
        if self._preset_view is not None:
            self._preset_view.teardown()
        if self._library_view is not None:
            self._library_view.teardown()
        super().teardown()

    def _show_hint(self, text: str) -> None:
        self._current = None
        self._editor_body.pack_forget()
        self._editor_hint.configure(text=text)
        self._editor_hint.place(relx=0.5, rely=0.4, anchor="center")

    def _show_editor(self) -> None:
        self._editor_hint.place_forget()
        if not self._editor_body.winfo_ismapped():
            self._editor_body.pack(fill="both", expand=True, padx=18, pady=16)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _new(self) -> None:
        mid = self._selected_model_id()
        if not mid:
            return
        n = len(self._profiles.list(mid)) + 1
        p = self._profiles.create(mid, f"Profile {n}",
                                  {"n-gpu-layers": -1})
        self._refresh_tree(keep=f"p:{p.id}")

    def _delete(self) -> None:
        sel = self._selected()
        if not sel or sel[0] != "p":
            return
        mid = self._selected_model_id()
        self._profiles.delete(sel[1])
        self._current = None
        self._refresh_tree(keep=f"m:{mid}" if mid else None)

    def _set_all_active(self, active: bool) -> None:
        # Applies to every profile of every model, not just the selected one
        # (the per-model toggle lives on the tree's checkbox column).
        sel = self._tree.selection()
        self._profiles.set_active_all(None, active)
        self._refresh_tree(keep=sel[0] if sel else None)

    def on_show(self) -> None:
        self.update_idletasks()
        self._on_profiles_resize()
        if not self._shown_once:
            self._cols.scroll_to_start()
            self._shown_once = True
        sel = self._tree.selection()
        self._refresh_tree(keep=sel[0] if sel else None)
