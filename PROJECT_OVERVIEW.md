# Project Overview: Polpo AI Email Intelligence

## Purpose
Polpo AI is an "intelligent bridge" between traditional email communication and automated business logic. Its primary goal is to act as an AI-powered assistant for businesses, managing inbound customer requests with high-level reasoning (classification), generating human-like responses (drafting), and producing formal business documents (PDF quotes/invoices).

## Core Architecture
The project follows a modular, asynchronous architecture designed to be lightweight and scalable on a VPS.

### 1. Inbound Processing (`email_worker.py`)
- **IMAP Polling**: Periodically scans multiple client inboxes for new messages.
- **Classification**: Uses Anthropic **Claude-3-Haiku** for fast, cost-effective intent classification (e.g., `preventivo`, `info`, `spam`).
- **Intelligence**: For complex intents like `preventivo`, it triggers a deep analysis using **Claude-3.5-Sonnet**.

### 2. Intelligent Response Generation (`responder.py`)
- **JSON Payload Logic**: Generates a structured response containing:
  - `subject` and `body` (Italian/Natural Language).
  - `suggested_actions` for the human operator.
  - `attach_document`: A structured instruction for generating PDFs with dynamic pricing (including 10% markup logic).
- **Robust Cleaning**: Includes a dedicated engine to sanitize and repair malformed LLM outputs.

### 3. Document Generation (`document_generator.py`)
- Uses `ReportLab` to turn JSON data into professional PDF documents (e.g., formal quotes).
- Supports branding/signatures based on client configuration in Supabase.

### 4. Human-in-the-Loop (Telegram)
- All generated drafts are sent to a private Telegram channel via a custom bot.
- Operators can review the draft, the generated PDF, and click "Approve" to send.

### 5. Outbound Delivery (`smtp_sender.py` / `main.py`)
- Once approved, the system uses SMTP (Zoho/Other) to send the response, correctly handling attachments and threading (`In-Reply-To`).

## Key Technology Stack
- **Languages**: Python (Asyncio)
- **Database**: Supabase (PostgreSQL + Realtime)
- **AI**: Anthropic Claude API (3.5 Sonnet & 3 Haiku)
- **Communication**: IMAP/SMTP (standard protocols), Telegram Bot API.
- **Deployment**: Linux VPS with Systemd services for Production and Staging.

---
**Advice for Future AI**: Focus on `responder.py` for intelligence changes and `email_worker.py` for pipeline flow. Always check the database schema in Supabase for client-specific credentials.
