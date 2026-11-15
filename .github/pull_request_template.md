## What and why

<!-- What this changes and the reason. Link the issue if there is one. -->

## Checks

- [ ] `uv run pytest -q` passes
- [ ] `uv run ruff check . && uv run mypy joinery servers` passes
- [ ] Tests added or updated for changed behaviour
- [ ] No generative AI models, services or assets added (see CONTRIBUTING.md)
- [ ] New assets have a `joinery.lock` entry with a replayable recipe
- [ ] Engine changes are in `godot_patches/patches/`, and the add-on still works on stock Godot
