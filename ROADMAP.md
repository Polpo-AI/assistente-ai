# Roadmap: Polpo AI Future Features

## Phase 1: Robustness & Scaling (Current focus)
- [x] **JSON Reliability**: Handle complex JSON payload truncation and parsing errors.
- [x] **Worker Visibility**: Real-time logging of processing states.
- [ ] **Error Retries**: Implement exponential backoff for transient IMAP/SMTP disconnections.
- [ ] **Database Optimization**: Consolidate migrations and move to more optimized async queries.

## Phase 2: Document Intelligence
- [ ] **OCR Integration**: Use Sonnet to read architectural diagrams or hand-written notes attached to emails to improve quote accuracy.
- [ ] **Excel Support**: Generate multi-sheet quotes or cost analysis files in `.xlsx` format.
- [ ] **Template Engine**: Allow clients to upload their own `.docx` or HTML templates for PDF generation.

## Phase 3: CRM & Knowledge Base
- [ ] **Vector Database (RAG)**: Integrate a knowledge base (Pinecone/Supabase Vector) so the AI can answer specific technical questions based on company manuals.
- [ ] **Customer Memory**: Track past interactions per sender to personalize the "tone" and remember previous preferences or quotes.

## Phase 4: Omnichannel Intelligence
- [ ] **WhatsApp Integration**: Bridge the current email intelligence to a WhatsApp bot (Baileys/Polpo WhatsApp API).
- [ ] **Voice-to-Email**: Transcribe voice messages from users and process them as inbound intents.

## Phase 5: Dashboard & Analytics
- [ ] **Monitoring UI**: A Next.js dashboard to see real-time "Health" of the workers.
- [ ] **Conversion Tracking**: Track if a generated quote actually leads to a sale (via email follow-ups).

---
**Design Philosophy**: Always prioritize "Beautiful & Premium" document output. The PDF is often the first formal touchpoint with the customer; it must look impeccable.
