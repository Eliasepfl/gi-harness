---
name: godot-2d-physics
description: "Expert patterns for Godot 2D physics including collision layers, RigidBody2D, Area2D triggers, and raycasting. Use when implementing collision detection, jump arcs, or trigger zones. Trigger keywords: physics, collision, rigidbody, area2d, raycast, move_and_slide."
---

# 2D Physics

Guidance for collision detection, triggers, and raycasting in Godot 2D.

## Collision layers

- Layer answers "what am I"; mask answers "what do I detect". They differ.
- Do not scale CollisionShape2D nodes; resize the shape resource instead.

## Movement

- move_and_slide already includes the timestep; only multiply gravity by delta.
