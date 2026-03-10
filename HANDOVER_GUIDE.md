# Technical Handover Guide (AI-to-AI)

## Context for Successors
This project, **Polpo AI**, relies heavily on structured JSON and asynchronous Python. If you are reading this, you are tasked with maintaining or extending its intelligence.

## Critical Nuances
### 1. JSON Truncation (IMPORTANT)
The `attach_document` field in the LLM response is very verbose. We've increased `max_tokens` to **2000** in `responder.py`. If you add more PDF features, always monitor the output for truncation (`JSONDecodeError` or "unterminated string").

### 2. Markup Logic
The prompt in `responder.py` is configured to add a **10% markup** to all prices in quote requests. This is a hard requirement. Re-verify this logic if you modify the system prompt.

### 3. Cleaning Logic
The function `clean_json` in `responder.py` is the project's "safety net." It handles:
- Stripping markdown blocks (` ```json `).
- Repairing trailing commas.
- Extracting content from within brackets.
Do not remove this; it is essential for handling Claude's intermittent formatting issues.

## Debugging Workflow
1. **Logs**: Use `journalctl -u polpo-worker.service -f` on the VPS to see real-time processing.
2. **Re-trigger**: To re-process an email for testing:
   - Delete the entry from the `emails` and `draft_responses` tables in Supabase.
   - Patch the `clients` table's `imap_last_uid` to `UID - 1`.
3. **Draft Alerts**: If the AI cannot generate a valid JSON after multiple attempts, it sends an alert: "errore generazione bozza. rispondere manualmente."

## Key Environment Variables
Ensure these are correctly set in the server's `.env`:
- `ANTHROPIC_API_KEY`: Required for intelligence.
- `SUPABASE_URL` / `SUPABASE_KEY`: Required for data persistence.
- `TELEGRAM_BOT_TOKEN`: Required for notifications.

---
**Advice**: The worker uses `asyncio`. Avoid blocking calls (like synchronous database libraries) in the main pipeline. Always wrap them in `asyncio.to_thread` if necessary.
