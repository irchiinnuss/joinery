"""Run the Journeyman sidebar's end-to-end UI scenarios against a scripted provider.

    uv run python tests/godot/run_ui_scenarios.py [--out DIR] [--scenarios a,b]
        [--widths 340,480,1000] [--themes dark light] [--editor] [--headless]

The sidebar runs for real (ui_scenario_dock.gd; with --editor also inside a real editor on
a copy of the testbed, ui_scenario_editor.gd), and so does the chat host behind it; only the
provider is scripted (scripted_host.py), so a run costs nothing and repeats exactly. Each
scenario prints its checks, metrics and screenshots (see ui_scenarios.gd); this collects them
in ``report.json`` and ``report.md`` beside the screenshots.

A check that fails fails the run. A known bug is an expected failure (``xfail``) and is
listed, not failed; one that starts passing is listed as ``xpass`` so its mark can go.
Findings of ui_checks.gd (clipped, contrast, overflow, target) are listed; with
``--strict-design`` they fail the run too.

``--headless`` runs the checks without a window or screenshots (CI on every runner).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import partial
from pathlib import Path

from run_startup_test import isolated_editor, isolated_environment, scratch_folder
from run_visual_suite import (
    FINDING,
    Finding,
    Harness,
    rendering,
    run_godot,
    testbed_editor,
)

from joinery.distribution import engine_binary

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests/godot"
CHECK = re.compile(r"^SCENARIO-CHECK (\S+) (pass|fail|xfail|xpass) (.*)$")
METRIC = re.compile(r"^SCENARIO-METRIC (\S+) (\S+) (\S+) (\S+)$")
#: Wall-clock budgets the metrics are held to (ms), the usual ones for chat UIs.
BUDGETS = {
    "send_to_card": 100.0,
    "send_to_status_line": 1000.0,
    "stop_to_idle": 1000.0,
    "decision_to_line": 1000.0,
}


def scenario_env(env: dict[str, str], args: argparse.Namespace) -> dict[str, str]:
    env = dict(env)
    env["JOINERY_SCRIPTED_HOST"] = str(TESTS / "scripted_host.py")
    env["JOINERY_TEST_PYTHON"] = sys.executable
    if trace := os.environ.get("JOINERY_SCRIPTED_TRACE_DIR"):
        env["JOINERY_SCRIPTED_TRACE_DIR"] = trace  # where a stuck host writes its stacks
    env["JOINERY_BACKGROUND"] = "1"  # never take focus (ADR 0032)
    if args.scenarios:
        env["JOINERY_SCENARIOS"] = args.scenarios
    if args.widths:
        env["JOINERY_SCENARIO_WIDTHS"] = args.widths
    env["JOINERY_SCENARIO_THEMES"] = ",".join(args.themes)
    return env


def dock(capture: Path, logs: Path, args: argparse.Namespace) -> tuple[Harness, str]:
    harness = Harness("scenarios-dock")
    with scratch_folder("joinery-ui-scenarios-") as directory:
        temporary = Path(directory)
        project = temporary / "project"
        project.mkdir()
        shutil.copytree(ROOT / "godot_addon/joinery", project / "addons/joinery")
        (project / "project.godot").write_text(
            'config_version=5\n[application]\nconfig/name="UI scenarios"\n'
            '[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
        )
        env = scenario_env(isolated_environment(temporary, ROOT, capture), args)
        binary = isolated_editor(engine_binary(), temporary)
        base = [str(binary), "--path", str(project), "--audio-driver", "Dummy"]
        run_godot(harness, base + ["--headless", "--editor", "--import"], env, logs)
        script = ["--script", str(TESTS / "ui_scenario_dock.gd")]
        window = ["--headless"] if args.headless else rendering(env)
        output = run_godot(
            harness,
            base + script + window,
            env,
            logs,
            marker="ui scenarios dock:",
            timeout=args.timeout,
        )
    return harness, output


def editor(
    theme: str, capture: Path, logs: Path, args: argparse.Namespace
) -> tuple[Harness, str]:
    harness = Harness(f"scenarios-editor-{theme}")
    testbed_editor(
        harness,
        theme,
        "ui_scenario_editor.gd",
        [
            "ui_checks.gd",
            "ui_scenarios.gd",
            "ui_scenario_cases.gd",
            "ui_scenario_checks.gd",
            "playtest_fixture.gd",
        ],
        "ui scenarios editor:",
        capture,
        logs,
        extra_env=scenario_env({}, args),
    )
    log = logs / f"{harness.name}.log"
    return harness, log.read_text(encoding="utf-8") if log.is_file() else ""


def collect(output: str) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    checks: list[dict[str, str]] = []
    metrics: list[dict[str, object]] = []
    for line in output.splitlines():
        if match := CHECK.match(line.strip()):
            where, state, what = match.groups()
            scenario, _, variant = where.partition("/")
            checks.append(
                {"scenario": scenario, "variant": variant, "state": state, "what": what}
            )
        elif match := METRIC.match(line.strip()):
            scenario, name, value, unit = match.groups()
            metrics.append(
                {
                    "scenario": scenario,
                    "name": name,
                    "value": float(value),
                    "unit": unit,
                }
            )
    return checks, metrics


def summarise_metrics(metrics: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    units: dict[tuple[str, str], str] = {}
    for item in metrics:
        key = (str(item["scenario"]), str(item["name"]))
        grouped[key].append(float(str(item["value"])))
        units[key] = str(item["unit"])
    rows: list[dict[str, object]] = []
    for (scenario, name), values in sorted(grouped.items()):
        ordered = sorted(values)
        median = ordered[len(ordered) // 2]
        budget = BUDGETS.get(name)
        rows.append(
            {
                "scenario": scenario,
                "name": name,
                "unit": units[(scenario, name)],
                "runs": len(values),
                "median": round(median, 1),
                "max": round(ordered[-1], 1),
                "budget": budget,
                "over_budget": budget is not None and ordered[-1] > budget,
            }
        )
    return rows


def write_report(
    out: Path,
    harnesses: list[Harness],
    checks: list[dict[str, str]],
    metrics: list[dict[str, object]],
    strict_design: bool,
) -> int:
    findings: list[Finding] = [f for h in harnesses for f in h.findings]
    rows = summarise_metrics(metrics)
    states = Counter(check["state"] for check in checks)
    failed = [c for c in checks if c["state"] == "fail"]
    known = sorted(
        {(c["scenario"], c["what"]) for c in checks if c["state"] == "xfail"}
    )
    fixed = sorted(
        {(c["scenario"], c["what"]) for c in checks if c["state"] == "xpass"}
    )
    over = [row for row in rows if row["over_budget"]]
    broken = [h for h in harnesses if h.errors]
    report = {
        "output": str(out),
        "checks": checks,
        "counts": dict(states),
        "metrics": rows,
        "design_findings": [f.__dict__ for f in findings],
        "harnesses": [
            {
                "name": h.name,
                "ok": not h.errors,
                "errors": h.errors,
                "seconds": h.seconds,
            }
            for h in harnesses
        ],
        "captures": sorted(p.name for p in out.glob("scn-*.png")),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        "# Journeyman UI scenarios",
        "",
        (
            f"{len(checks)} checks: {states.get('pass', 0)} passed, "
            f"{states.get('fail', 0)} failed, {states.get('xfail', 0)} known bugs, "
            f"{states.get('xpass', 0)} known bugs now passing. "
            f"{len(report['captures'])} screenshots, {len(findings)} design findings."
        ),
        "",
    ]
    if broken:
        lines += ["## Harness failures", ""]
        lines += [f"- {h.name}: {'; '.join(h.errors[:3])}" for h in broken] + [""]
    if failed:
        lines += ["## Failed checks", ""]
        seen: set[tuple[str, str]] = set()
        for check in failed:
            key = (check["scenario"], check["what"])
            if key not in seen:
                seen.add(key)
                variants = sorted(
                    c["variant"] for c in failed if (c["scenario"], c["what"]) == key
                )
                lines.append(f"- {key[0]}: {key[1]} ({', '.join(variants)})")
        lines.append("")
    if known:
        lines += ["## Known bugs (expected failures)", ""]
        lines += [f"- {scenario}: {what}" for scenario, what in known] + [""]
    if fixed:
        lines += ["## Known bugs now passing (remove the mark)", ""]
        lines += [f"- {scenario}: {what}" for scenario, what in fixed] + [""]
    lines += [
        "## Metrics",
        "",
        "| Scenario | Metric | Median | Max | Budget | Runs |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        budget = f"{row['budget']:.0f}" if row["budget"] is not None else ""
        flag = " (over)" if row["over_budget"] else ""
        lines.append(
            f"| {row['scenario']} | {row['name']} ({row['unit']}) | {row['median']} | "
            f"{row['max']}{flag} | {budget} | {row['runs']} |"
        )
    lines.append("")
    if findings:
        lines += ["## Design findings (ui_checks.gd)", ""]
        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        for finding in findings:
            grouped[(finding.kind, finding.node)].append(finding.screen)
        for (kind, node), screens in sorted(grouped.items(), key=lambda i: -len(i[1])):
            lines.append(
                f"- {kind} `{node}` on {len(screens)} screens, e.g. {screens[0]}"
            )
        lines.append("")
    (out / "report.md").write_text("\n".join(lines))
    print("\n".join(lines))
    if broken or failed:
        return 1
    if strict_design and findings:
        return 1
    return 1 if over and os.environ.get("JOINERY_SCENARIO_BUDGETS") else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "out/ui-scenarios")
    parser.add_argument(
        "--scenarios", default="", help="Comma-separated scenario names"
    )
    parser.add_argument("--widths", default="", help="Comma-separated sidebar widths")
    parser.add_argument("--themes", nargs="+", default=["dark", "light"])
    parser.add_argument(
        "--editor", action="store_true", help="Also run in a real editor"
    )
    parser.add_argument("--editor-only", action="store_true")
    parser.add_argument(
        "--headless", action="store_true", help="Checks only, no window"
    )
    parser.add_argument("--strict-design", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Run this with uv run python.")
    out: Path = args.out.resolve()
    if out.exists():
        for stale in [*out.glob("scn-*.png"), out / "report.json", out / "report.md"]:
            stale.unlink(missing_ok=True)
        shutil.rmtree(out / "logs", ignore_errors=True)
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    harnesses: list[Harness] = []
    outputs: list[str] = []
    jobs: list[tuple[str, Callable[[], tuple[Harness, str]]]] = []
    if not args.editor_only:
        jobs.append(("scenarios-dock", partial(dock, out, logs, args)))
    if args.editor or args.editor_only:
        for theme in args.themes:
            jobs.append(
                (f"scenarios-editor-{theme}", partial(editor, theme, out, logs, args))
            )
    for name, job in jobs:
        print(f"== {name}", flush=True)
        started = time.monotonic()
        harness, output = job()
        harness.seconds = round(time.monotonic() - started, 1)
        # Each finding once; run_godot already parsed this harness's own output.
        if not harness.findings:
            harness.findings = [
                Finding(*m.groups())
                for line in output.splitlines()
                if (m := FINDING.match(line.strip()))
            ]
        harnesses.append(harness)
        outputs.append(output)
        print(
            f"   {harness.seconds:.0f}s, {'ok' if not harness.errors else harness.errors[:2]}"
        )
    checks, metrics = collect("\n".join(outputs))
    return write_report(out, harnesses, checks, metrics, args.strict_design)


if __name__ == "__main__":
    raise SystemExit(main())
