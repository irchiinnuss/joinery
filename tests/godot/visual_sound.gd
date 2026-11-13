extends RefCounted
## The visual suite's sound screens: a music track in review. Copied beside
## visual_editor_test.gd by run_visual_suite.py; [param suite] is that test, whose `_shot`,
## `_check`, `_frames` and `_plugin` these use.


## A new music track in review: its layers with their stems and levels, loudness from the facts.
static func review_music(suite: Variant, dock: Control, panel: Control) -> void:
	var text := FileAccess.get_file_as_string("res://music/harbour.tres")
	var diff := "--- music/harbour.tres\n+++ music/harbour.tres\n@@ -0,0 +1,4 @@\n"
	for line: String in text.strip_edges().split("\n"):
		diff += "+" + line + "\n"
	var lead := ProjectSettings.globalize_path("res://audio/music/harbour/main_lead.wav")
	var stem := {"path": "audio/music/harbour/main_lead.wav", "media": "audio", "after": lead}
	var changes := [{"path": "music/harbour.tres", "change": "created"}]
	changes.append({"path": stem["path"], "change": "created"})
	dock.show_file_review("Compose a calm harbour theme", diff, [stem], changes)
	panel.call("show_glance", {"facts": ["4 layers", "Lead −15.8 LUFS", "Mix −16.1 LUFS"]})
	await dock.get_tree().create_timer(0.4).timeout
	await suite._shot("review-music", [panel, dock])
