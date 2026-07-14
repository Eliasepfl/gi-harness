# boot.gd -- trivial script for the BOOT gate (a): print one line and exit.
# Measures the steady-state headless process launch tax with no physics/scene work.
extends SceneTree

func _initialize() -> void:
	print("BOOT_OK")
	quit()
