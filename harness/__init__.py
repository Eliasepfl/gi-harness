"""Agent harness — génération d'environnements 2D jouables depuis des commandes texte,
avec vérification physique purement programmatique (aucun VLM sur pixels).

Modules:
    sdk        — SceneSDK, wrapper instrumenté de pymunk (seule API vue par le code généré)
    sandbox    — exécution isolée du code de scène (sous-processus, AST-scan, timeout)
    verifier   — entonnoir L0 statique → L1 settling → L2 objectif
    generator  — génération LLM (claude-opus-4-8) ou templates hors-ligne + boucle de réparation
    navigator  — boucle observation-état → action (policy greedy v1, LLM à venir)
    cli        — point d'entrée `python -m harness`
"""

__version__ = "0.1.0"
