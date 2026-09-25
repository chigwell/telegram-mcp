# Skill: Draft Replies Without Sending

## Purpose
Inspects recent conversation context in a specified Telegram chat, prepares a contextual and tone-appropriate response, and either returns it as text for user review or saves it into Telegram's native input field as an unposted draft. **Under no circumstances does this skill transmit or send a message.**

---

## When to Use / Use Cases
- Preparing a thoughtful response to a client, manager, or collaborator before sending.
- Reviewing sensitive messages where the agent provides options for human approval.
- Staging a draft inside the Telegram mobile or desktop app so the user can edit or send it with a single tap.
- Operating in high-security or read-only environments where outbound communication requires human-in-the-loop authorization.

---

## Prerequisites / Configuration
- Telegram MCP Server running and authenticated.
- Optional: `TELEGRAM_EXPOSED_TOOLS=read-only` (if drafting text-only) or `TELEGRAM_EXPOSED_TOOLS=read-only+save_draft` (if allowing native Telegram drafts).
- Invariant Rule: Tool `send_message`, `reply_to_message`, and `forward_message` must **never** be invoked.

---

## MCP Tools Used
1. `get_message_context`
   - **Parameters:** `chat_id=<chat_id>`, `message_id=<message_id>`, `context_size=3`
   - **Purpose:** Pulls messages immediately preceding and following the target message to understand the exact conversational flow.
2. `save_draft` *(Optional)*
   - **Parameters:** `chat_id=<chat_id>`, `message=<draft_text>`, `reply_to_msg_id=<message_id>`
   - **Purpose:** Saves the drafted response into Telegram's native draft storage. The draft appears in the client's input field ready for the user to review and send manually.
3. `send_message` / `reply_to_message` *(Strictly Prohibited)*
   - **Invariant:** Neither of these tools may be called in this workflow.

---

## Inputs
- **Chat Identifier:** Username, phone, or numeric ID (e.g., `@sarah_pm` or `12345678`).
- **Target Message ID:** The specific message requiring a reply.
- **Intent / Guidance:** User instructions regarding tone, constraints, or key information (e.g., *"Confirm we can meet tomorrow at 3 PM, but ask if Zoom is preferred"*).
- **Save Mode:** Whether to return the draft in the chat or also stage it via `save_draft`.

---

## Outputs
- **Context Inspection Summary:** Brief summary of the thread context.
- **Draft Proposal:** Formatted proposed reply text presented in a fenced block.
- **Explanation:** Why this wording was chosen and notes on specific tone nuances.
- **Confirmation:** Confirmation that no message was sent.

---

## Step-by-Step Example

### Step 1: User Prompt
```text
User: "Sarah asked about the project delivery date in chat @sarah_pm (message ID 542). 
Draft a polite reply letting her know we are on track for Thursday, but do NOT send it yet. 
Save it as a draft in Telegram if possible."
```

### Step 2: Agent Inspects Surrounding Context
The agent calls `get_message_context` to understand Sarah's exact question:
```json
{
  "name": "get_message_context",
  "arguments": {
    "chat_id": "@sarah_pm",
    "message_id": 542,
    "context_size": 2
  }
}
```

**Tool Mock Response:**
```json
{
  "metadata": {
    "chat_id": "@sarah_pm",
    "target_message_id": 542
  },
  "data": [
    {
      "id": 540,
      "sender": "Omid",
      "date": "2026-09-21 15:30:00",
      "text": "We finished the core API integrations today."
    },
    {
      "id": 541,
      "sender": "Sarah PM",
      "date": "2026-09-21 16:10:00",
      "text": "Awesome progress!"
    },
    {
      "id": 542,
      "sender": "Sarah PM",
      "date": "2026-09-22 08:15:00",
      "is_target": true,
      "text": "Are we still good for the Thursday release demo, or do you need extra time for QA?"
    }
  ]
}
```

### Step 3: Agent Stages Native Draft (Non-transmitting)
The agent calls `save_draft` (which saves to Telegram's cloud draft storage without sending):
```json
{
  "name": "save_draft",
  "arguments": {
    "chat_id": "@sarah_pm",
    "message": "Hi Sarah! Yes, we are fully on track for Thursday's demo. QA testing is already underway on staging and looking solid.",
    "reply_to_msg_id": 542
  }
}
```

**Tool Mock Response:**
```text
"Draft saved to chat @sarah_pm. Open the chat in Telegram to see and send it."
```

### Step 4: Agent Presents Draft for User Review
```markdown
### 📝 Draft Prepared (Not Sent)

**Target Chat:** @sarah_pm  
**Replying to Message #542:** *"Are we still good for the Thursday release demo, or do you need extra time for QA?"*

#### Proposed Reply:
> Hi Sarah! Yes, we are fully on track for Thursday's demo. QA testing is already underway on staging and looking solid.

---
✅ **Status:**
- **No message was sent.**
- The text above has been saved as a **Draft** in your Telegram app for `@sarah_pm`.
- You can open Telegram, review the text in the message box, edit any details, and press Send whenever you are ready.
```

---

## Safety / Privacy Notes
- **Strict "Do Not Send" Invariant:** The system prompt and tool constraints guarantee that tools with mutating delivery effects (`send_message`, `reply_to_message`) are never invoked.
- **Safe Staging via `save_draft`:** Saving a draft invokes MTProto's `messages.SaveDraftRequest`. This populates the user's local message input box on all connected devices (mobile, desktop, web) but does not trigger push notifications or broadcast any message to the recipient.
- **Privacy Allowlist:** Respects `ALLOWED_CHAT_IDS`.

---

## Failure Modes & Recovery
1. **Accidental Send Prevention:**
   - *Failure Mode:* Agent misunderstands instructions and attempts to call `send_message`.
   - *Guardrail:* Run the server with `TELEGRAM_EXPOSED_TOOLS=read-only+save_draft` to physically prevent `send_message` from being registered as an MCP tool.
2. **Target Message Not Found:**
   - *Error:* `Message with ID 542 not found in chat @sarah_pm`.
   - *Recovery:* Call `list_messages(chat_id="@sarah_pm", limit=5)` to retrieve recent valid message IDs and ask user for clarification.
3. **Invalid Peer or Chat Restrictions:**
   - *Error:* `ChatAccessDeniedError` or `PeerIdInvalidError`.
   - *Recovery:* Check chat permissions and ensure the user has initiated conversation with that peer.
