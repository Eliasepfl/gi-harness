# diag.gd -- reports which physics backend is live (used to prove the rapier
# GDExtension actually loads headless). Emits one framed JSON line.
extends SceneTree

func _initialize() -> void:
	var d := {
		"phys_engine_setting": str(ProjectSettings.get_setting("physics/2d/physics_engine", "DEFAULT")),
		"rapier_class_exists": ClassDB.class_exists("RapierPhysicsServer2D"),
		"gravity": ProjectSettings.get_setting("physics/2d/default_gravity", 0.0),
	}
	print("__DIAG__" + JSON.stringify(d))
	quit()
