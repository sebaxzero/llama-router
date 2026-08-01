"""Compare page construction, navigation, and settled Tk timings.

Run from the repository root with the tool-local Python environment:

    tools/.venv/Scripts/python tools/benchmark_ui.py

The benchmark is deliberately a script rather than a test.  It starts a fresh
worker process for every sample, creates a temporary database there, and keeps
the reported numbers descriptive instead of enforcing timing thresholds.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from llama_router.schemas import ModelEntry  # noqa: E402
from tools.tests.test_ui import TestAppLayout  # noqa: E402

PAGES = ("dashboard", "playground", "profiles", "runtime", "settings")
SCENARIO_PAGES = {
    "empty": PAGES,
    "logs500": ("dashboard",),
    "settings-open": ("settings",),
    "settings-closed": ("settings",),
    "models10x3": ("profiles",),
}
DEFAULT_SIZES = ((960, 640), (1280, 860))
_SETTINGS_CARDS = (
    "settings.appearance", "settings.application", "settings.server",
)


def _parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT")
    if width < 760 or height < 500:
        raise argparse.ArgumentTypeError("size is below the app minimum")
    return width, height


def _seed_logs(app) -> None:
    for index in range(500):
        app.ctx.logs.log("app", "info", f"benchmark log line {index:03d}")
    dashboard = app._pages["dashboard"]
    dashboard._set_logs_open(True)


def _seed_models(app) -> None:
    models = app.ctx.services["models"]
    profiles = app.ctx.services["profiles"]
    for index in range(10):
        model_id = f"benchmark-model-{index:02d}"
        models._models[model_id] = ModelEntry(
            id=model_id,
            name=f"Benchmark Model {index:02d}",
            path=str(ROOT / "models" / f"benchmark-{index:02d}.gguf"),
        )
        profiles.ensure_defaults(model_id)
        profiles.create(model_id, "Review", {})


def _prepare_scenario(app, scenario: str) -> None:
    if scenario == "logs500":
        app.show_page("playground")
        app._cancel_prewarm()
        _seed_logs(app)
    elif scenario == "models10x3":
        _seed_models(app)
    elif scenario.startswith("settings-"):
        is_open = scenario == "settings-open"
        app.ctx.collapsible_states.update(
            {key: is_open for key in _SETTINGS_CARDS})


def _settle(app) -> float:
    """Drain two fixed Tk rounds; benchmark-only, never used by App itself."""
    started = time.perf_counter()
    for _ in range(2):
        app.root.update_idletasks()
        app.root.update()
    return (time.perf_counter() - started) * 1000


def _build_and_settle(base: Path, geometry: str) -> tuple:
    """Build the shell, then measure its complete startup separately."""
    started = time.perf_counter()
    app, logs = TestAppLayout._build_app(base, geometry=geometry)
    app_build_ms = (time.perf_counter() - started) * 1000
    app._cancel_prewarm()
    _settle(app)
    startup_settled_ms = (time.perf_counter() - started) * 1000
    return app, logs, {
        "app_build_ms": app_build_ms,
        "startup_settled_ms": startup_settled_ms,
    }


def _timings(app, page: str) -> dict[str, float]:
    construct = getattr(app, "_page_construct_ms", None)
    if construct is None:
        construct = getattr(app, "_page_build_ms", {})
    on_show = getattr(app, "_page_on_show_ms", {})
    navigation_idle = getattr(app, "_page_navigation_idle_ms", None)
    if navigation_idle is None:
        navigation_idle = getattr(app, "_page_first_idle_ms", {})
    return {
        "construct_ms": construct.get(page, 0.0),
        "on_show_ms": on_show.get(page, 0.0),
        "navigation_idle_ms": navigation_idle.get(page, 0.0),
    }


def _navigate_and_settle(app, page: str) -> dict[str, float]:
    started = time.perf_counter()
    app.show_page(page)
    show_call_ms = (time.perf_counter() - started) * 1000
    app._cancel_prewarm()
    _settle(app)
    settled_ms = (time.perf_counter() - started) * 1000
    return {"show_call_ms": show_call_ms, "settled_ms": settled_ms}


def _first_measurement(app, page: str,
                       startup: dict[str, float]) -> dict[str, float]:
    if page == "dashboard" and app._active == "dashboard":
        # Dashboard was built by App.__init__; do not turn a no-op navigation
        # into a misleading first-load metric.
        return {**startup, **_timings(app, page)}
    if page != "dashboard" and page in app._pages:
        raise RuntimeError(f"first-load target already exists: {page}")
    navigation = _navigate_and_settle(app, page)
    return {**startup, **navigation, **_timings(app, page)}


def _repeated_measurements(app, page: str, other: str,
                           repeats: int) -> list[dict[str, float]]:
    if app._active != other:
        _navigate_and_settle(app, other)
    repeated = []
    for _ in range(repeats):
        if app._active != other:
            _navigate_and_settle(app, other)
        navigation = _navigate_and_settle(app, page)
        repeated.append({**navigation, **_timings(app, page)})
        _navigate_and_settle(app, other)
    return repeated


def _close(app, logs) -> None:
    try:
        app._cancel_idle_callbacks()
        app._cancel_prewarm()
        for after_id in app.root.tk.call("after", "info"):
            app.root.after_cancel(after_id)
        if app.root.winfo_exists():
            app._on_close()
    except Exception:
        pass
    finally:
        for service in app.ctx.services.values():
            close = getattr(service, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        logs.close()


def _run_case(scenario: str, page: str, size: tuple[int, int],
              repeats: int) -> tuple[dict[str, float], list[dict[str, float]]]:
    width, height = size
    with tempfile.TemporaryDirectory(prefix="llama-router-bench-") as td:
        app, logs, startup = _build_and_settle(
            Path(td), geometry=f"{width}x{height}")
        try:
            _prepare_scenario(app, scenario)
            app.ctx.events.drain()
            app._cancel_prewarm()
            _settle(app)

            first = _first_measurement(app, page, startup)

            other = "dashboard" if page != "dashboard" else "playground"
            repeated = _repeated_measurements(app, page, other, repeats)
            return first, repeated
        finally:
            _close(app, logs)


def _record(results: dict[tuple, list[float]], scenario: str,
            size: tuple[int, int], page: str, phase: str,
            timings: dict[str, float]) -> None:
    for metric, value in timings.items():
        results[(scenario, size, page, phase, metric)].append(value)


def _run_fresh_sample(scenario: str, page: str, size: tuple[int, int],
                      repeats: int) -> tuple[dict[str, float],
                                              list[dict[str, float]]]:
    """Run one sample in a fresh interpreter to avoid cross-sample state."""
    width, height = size
    command = [sys.executable, str(Path(__file__).resolve()), "--worker",
               scenario, page, str(width), str(height), str(repeats)]
    completed = subprocess.run(command, cwd=ROOT, text=True,
                               capture_output=True, timeout=120)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"benchmark worker failed ({completed.returncode}): {detail}")
    try:
        payload = json.loads(completed.stdout.strip())
        return payload["first"], payload["repeated"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"benchmark worker returned invalid JSON: {completed.stdout!r}") from exc


def _report(results: dict[tuple, list[float]]) -> None:
    print("scenario             size       page       phase   metric          "
          "median     min     max  n")
    print("-" * 92)
    for key in sorted(results, key=str):
        scenario, size, page, phase, metric = key
        values = results[key]
        print(f"{scenario:<20} {size[0]}x{size[1]:<7} {page:<10} "
              f"{phase:<7} {metric:<15} "
              f"{statistics.median(values):8.2f} {min(values):7.2f} "
              f"{max(values):7.2f} {len(values):2d}")


def _run_worker(argv: list[str]) -> int:
    if len(argv) != 5:
        print("benchmark worker expects SCENARIO PAGE WIDTH HEIGHT REPEATS",
              file=sys.stderr)
        return 2
    scenario, page, width, height, repeats = argv
    try:
        first, repeated = _run_case(
            scenario, page, (int(width), int(height)), int(repeats))
    except Exception as exc:
        print(f"benchmark worker failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"first": first, "repeated": repeated}))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        return _run_worker(sys.argv[2:])

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5,
                        help="fresh-process samples per case (default: 5)")
    parser.add_argument("--repeats", type=int, default=3,
                        help="cached navigations per sample (default: 3)")
    parser.add_argument("--sizes", nargs="+", type=_parse_size,
                        default=list(DEFAULT_SIZES), metavar="WIDTHxHEIGHT")
    parser.add_argument("--scenarios", nargs="+",
                        choices=tuple(SCENARIO_PAGES),
                        default=list(SCENARIO_PAGES))
    args = parser.parse_args()
    if args.samples < 1 or args.repeats < 1:
        parser.error("--samples and --repeats must be positive")

    results: dict[tuple, list[float]] = defaultdict(list)
    total = sum(len(SCENARIO_PAGES[name]) for name in args.scenarios)
    total *= len(args.sizes) * args.samples
    completed = 0
    for scenario in args.scenarios:
        for size in args.sizes:
            for page in SCENARIO_PAGES[scenario]:
                for _ in range(args.samples):
                    first, repeated = _run_fresh_sample(
                        scenario, page, size, args.repeats)
                    _record(results, scenario, size, page, "first", first)
                    for timings in repeated:
                        _record(results, scenario, size, page, "repeat",
                                timings)
                    completed += 1
                    print(f"completed {completed}/{total}: "
                          f"{scenario} {size[0]}x{size[1]} {page}")
    _report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
