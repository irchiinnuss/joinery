"""The hook's command guards judge what a shell line runs, not text that names it."""

from __future__ import annotations

import pytest

from joinery import hook
from joinery.policy import command_violation, load_denylist, user_only_violation
from joinery.shell_words import ShellParseError, invocations

#: (guard, command that runs it). Every one must stay refused.
BLOCK = [
    # Publishing
    ("Publish", "joinery share itch --project . --target me/moss"),
    ("Publish", "uv run --frozen joinery share itch --target me/moss"),
    ("Publish", "bash -lc 'cd game && joinery share itch --yes'"),
    ("Publish", 'sh -c "uv run joinery share itch"'),
    ("Publish", "echo ok; $(joinery share itch)"),
    ("Publish", "x=`joinery share itch`"),
    ("Publish", "sudo -E env A=1 joinery share itch"),
    ("Publish", "timeout 60 nice -n 5 joinery share itch"),
    ("Publish", "echo me/moss | xargs -I{} joinery share itch --target {}"),
    ("Publish", "find . -name x -exec joinery share itch \\;"),
    ("Publish", "eval joinery share itch"),
    ("Publish", "bash <<EOF\njoinery share itch\nEOF"),
    ("Publish", "cat <<'EOF' | bash\njoinery share itch\nEOF"),
    ("Publish", "echo 'joinery share itch' | sh"),
    ("Publish", "bash <<< 'joinery share itch'"),
    ("Publish", "cat > log.md <<EOF\n$(joinery share itch)\nEOF"),
    ("Publish", "if true; then joinery share itch; fi"),
    ("Publish", "( cd game && joinery share itch )"),
    ("Publish", "rtk proxy joinery share itch"),
    ("Publish", "butler push build me/moss:web"),
    ("Publish", "~/.cache/joinery/butler/15/butler.exe login"),
    ("Publish", 'BUTLER_API_KEY=x "butler" push build me/moss:web'),
    # The Timeline
    ("Timeline", "joinery timeline restore --project . scenes/a.tscn"),
    ("Timeline", "uv run joinery --project . timeline start"),
    ("Timeline", "bash -c 'joinery timeline restore x'"),
    # Kits
    ("Kits section", "python -m joinery.cli kit import x.joinerykit"),
    ("Kits section", "python3 joinery/cli.py kit export --name x"),
    ("Kits section", "cd game && uv run joinery kit remove --name Harbour"),
    # Tidy
    ("tidy_remove", "joinery tidy --project . --remove --yes"),
    ("tidy_remove", "uv run joinery tidy --keep=audio/x.wav"),
    ("tidy_remove", "zsh -c 'joinery tidy --clear-caches'"),
    # References and design documents
    ("Project apps", "joinery references add --project . --reference x.png"),
    ("Project apps", "joinery new /tmp/g --blueprint blank --design-doc gdd.md"),
    ("Project apps", "joinery game apply --project . --design-doc=gdd.md"),
    ("Project apps", "env joinery game apply --reference a.png"),
]

#: Text that names a guarded command but runs none of them.
ALLOW = [
    "cat >> log.md <<EOF\nTried joinery share itch; the hook refused it.\nEOF",
    "cat > report.md <<'EOF'\n- `joinery share itch` publishes $(not run)\nEOF",
    "echo 'run joinery share itch when ready'",
    "printf '%s\\n' 'joinery timeline restore x' > notes.txt",
    "git commit -m 'docs: explain joinery kit import'",
    "grep -rn 'joinery tidy --remove' docs",
    "rg -- '--design-doc' joinery",
    'echo "butler push is itch\'s tool"',
    "joinery share build --project . --targets web",
    "joinery share status --project . --json",
    "joinery timeline list --project .",
    "joinery kit list --json",
    "joinery tidy --project . --json",
    "joinery brief --project . --json",
    "joinery references list --project .",
    "python -c \"print('joinery share itch')\"",
    "cat <<EOF > a.sh\njoinery share itch\nEOF",
]


@pytest.mark.parametrize(("guard", "command"), BLOCK)
def test_real_invocations_stay_refused(guard: str, command: str) -> None:
    verdict = user_only_violation(command)
    assert verdict is not None and guard in verdict.reason, command
    raw = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}
    assert hook.violation(raw) is not None


@pytest.mark.parametrize("command", ALLOW)
def test_text_that_only_names_a_command_passes(command: str) -> None:
    assert user_only_violation(command) is None, command


def test_a_line_that_does_not_parse_is_judged_on_its_text() -> None:
    with pytest.raises(ShellParseError):
        invocations("joinery share itch 'unterminated")
    assert user_only_violation("joinery share itch 'unterminated") is not None


@pytest.mark.parametrize(
    "command",
    [
        "pip install diffusers",
        "bash -lc 'pip install diffusers'",
        "sudo pip3 install replicate",
        "uv run pip install diffusers",
        "x=$(uvx blender-mcp)",
        "sh <<EOF\nnpm install @fal-ai/client\nEOF",
    ],
)
def test_denied_installs_stop_wherever_they_run(command: str) -> None:
    assert command_violation(command, load_denylist()) is not None, command


@pytest.mark.parametrize(
    "command",
    [
        "cat >> log.md <<EOF\npip install diffusers was refused\nEOF",
        "echo pip install diffusers",
    ],
)
def test_installs_named_in_text_pass(command: str) -> None:
    assert command_violation(command, load_denylist()) is None, command


def test_invocations_unwrap_and_skip_data() -> None:
    found = invocations("cd g && FOO=1 uv run joinery a 2>&1 | tee x.log <<EOF\nz\nEOF")
    assert ("joinery", "a") in found
    assert ("tee", "x.log") in found
    assert not any("z" in argv for argv in found)
