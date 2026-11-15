# Game brief — {{title}}

> One per game project, at `<project>/design/brief.md`. Written by you; Journeyman can draft it from your pitch and the starting point you picked.
> The brief directs, it does not block: nothing waits for it to be complete. Journeyman reads it before suggesting work, and points out where it disagrees with how the game is set up.
> Changing it is a reviewed edit like any other file. "_Not decided yet._" marks what is still open.
> How to write in this file: one idea per line, plain words, and a number where one exists ("a run lasts 8 to 12 minutes", not "fast-paced"). Name real games and what to take from each. Leave a section not decided rather than guess.
> Skip filler (stunning, immersive, seamless, vibrant, elevate, delve, tapestry, unleash, cutting-edge, game-changer), em-dash chains and "not just X but Y".

## 1. Pitch
{{pitch}}

{{pitch_long}}

## 2. Genre and references
- Genre: {{genre}}
- Games whose *play* you mean (how they play, not how they look; looks belong in the art bible), and what to take from each:

{{design_references}}

## 3. Audience and content
- Audience: {{audience}}
- Content notes (violence, themes, rating target): {{content_notes}}

## 4. Player fantasy
Who the player gets to be, and what they get to feel.

{{fantasy}}

## 5. Core loop
{{core_loop}}

## 6. Player verbs
{{verbs}}

## 7. Controls per device
{{controls}}

## 8. Camera
{{camera}}

## 9. Win, lose, progression
{{progression}}

## 10. Session length
{{session}}

## 11. Platforms and stores
{{platforms}}

## 12. Online
{{online}}

## 13. Saving
{{saving}}

## 14. Languages
{{languages}}

## 15. Accessibility goals
{{accessibility_goals}}

## 16. Monetisation
{{monetisation}}

## 17. Scope and milestones
{{scope}}

## 18. Features
One feature per line. End a line with what checks it, and Joinery marks it built, missing or broken from that evidence: `(reach: Lighthouse)`, `(no errors: 60 s)`, `(test: res://tests/test_lamp.gd)`, `(replay: first_storm)`. A line with nothing to check stays "not checked".

{{features}}

## 19. Keep
What must stay true as the game changes. Journeyman asks before changing one. A number pins a value: `- Jump height 1.2 m`, `- Player speed 11 m/s`. Name what holds it when the name alone doesn't find it: `- Jump height 1.2 m (res://player/player.gd:jump_height)`.

{{keep}}

## 20. Avoid
What the game must not have. Journeyman asks before adding one. Words in brackets are what to look for: `- Guns (gun, rifle, pistol)`.

{{avoid}}

## 21. Open questions
{{open_questions}}

## 22. Facts (checked)
The same decisions in a form Journeyman can compare with the game's setup.
Keep them in step with the sections above.

{{facts}}
