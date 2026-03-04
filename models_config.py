"""
models_config.py - Configurazione centralizzata dei modelli AI

Modelli attivi ad oggi (Marzo 2026):
  - claude-haiku-4-5-20251001     → veloce ed economico, ideale per classificazione
  - claude-sonnet-4-6-20250217    → bilanciato, ideale per generazione bozze e ragionamento
  - claude-opus-4-6-20250205      → il più potente, per task molto complessi

Aggiorna questi valori quando Anthropic rilascia nuovi modelli.
Riferimento: https://docs.anthropic.com/en/about-claude/models/overview
"""

# Modello per la classificazione veloce (Livello 3 del classifier)
# Haiku 4.5: rapido, economico, ottimo per classificazione in volume
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"

# Modello per il ragionamento complesso e la riclassificazione
# Sonnet 4.6: bilanciato tra intelligenza e costo
RECLASSIFY_MODEL = "claude-sonnet-4-6"

# Modello per la generazione delle bozze di risposta
# Sonnet 4.6: qualità di scrittura elevata a costo contenuto
RESPONDER_MODEL = "claude-sonnet-4-6"

# Modello per l'assistente conversazionale su Telegram
# Haiku 4.5: risposta rapida per interazioni real-time
TELEGRAM_ASSISTANT_MODEL = "claude-haiku-4-5-20251001"

# Modelli per l'onboarding di nuovi clienti
# Sonnet 4.6: buona qualità per suggerire intenti e keyword
ONBOARDING_SECTOR_SUGGESTER_MODEL = "claude-sonnet-4-6"
ONBOARDING_KEYWORDS_SUGGESTER_MODEL = "claude-sonnet-4-6"

# Modello per il summarization di grandi moli di dati (query_tools)
# Sonnet 4.6: context window ampia, ottimo per sintesi
SUMMARIZER_MODEL = "claude-sonnet-4-6"