---
name: godot-genre-platformer
description: "Expert blueprint for platformer games including precision movement (coyote time, jump buffering, variable jump height), game feel, and level design. Trigger keywords: platformer, jump, coyote_time, jump_buffer, precision_movement, game_feel."
---

# Genre: Platformer

Blueprint for platformers emphasising movement feel and level design.

## Precision movement

- Implement coyote time so a jump pressed just after leaving a ledge still fires.
- Buffer a jump pressed just before landing so it triggers on touchdown.
- Use variable jump height: cut upward velocity when the jump button releases.

## Game feel

- Add squash and stretch on takeoff and landing.
- A short screen shake on hard landings sells impact.
