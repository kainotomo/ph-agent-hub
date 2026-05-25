# Chat Area Architecture — PH Agent Hub

The chat area is the end-user experience inside the single React frontend of PH Agent Hub. It provides a clean, modern interface for interacting with AI agents while keeping all administrative and operational logic out of the user's view.

This document defines the structure, responsibilities, and integration points of the chat area.

---

## 1. Purpose of the Chat Area

The chat area is designed for end users who interact with AI agents. It focuses on:

- fast, responsive chat experience
- real-time streaming responses and agent events
- model selection based on tenant permissions (model name displayed on each response)
- template, prompt, and skill selection
- personal skill creation and management
- file uploads
- memory management (view, delete, manually add entries, paginated, cross-session retrieval)
- session-level tool activation from tenant-approved tools (auto-synced when skill changes)
- temporary and permanent chat sessions (with temp→permanent conversion)
- message editing, deletion (with confirmation dialog), branching, and regeneration
- message feedback
- follow-up question suggestions after each response
- full-text search across sessions and messages
- multi-session chat history
- auto-scroll to bottom when revisiting a session
- authentication via backend-issued JWT

It does **not** include any admin functionality.

---

## 2. Technology Approach

The chat area should be implemented with custom React components inside the shared frontend application.

Recommended stack:

- **React**
- **TypeScript**
- **Ant Design 5** — the design system shared with the admin area
- **TanStack Query**
- **SSE** ([`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source)) for streaming — see [streaming-protocol.md](streaming-protocol.md)
- **REST API client** for backend communication
- **JWT authentication** shared with the rest of the frontend

Refine is **not** used for the chat area.

---

## 3. High-Level Architecture

```
┌──────────────────────────────────────────────┐
│         Chat Area (Frontend Route Space)     │
│  - Chat window                               │
│  - Model selector                            │
│  - Template / prompt / skill selectors       │
│  - File uploads                              │
│  - Session history                           │
│  - Streaming renderer                        │
└───────────────────────────────┬──────────────┘
                                │ REST + SSE (text/event-stream)
                                ▼
┌──────────────────────────────────────────────┐
│   Backend + Microsoft Agent Framework        │
│  - Agent loop                                │
│  - DeepSeek stabilizer                       │
│  - Model routing                             │
│  - Tool execution                            │
│  - Sessions + messages                       │
└──────────────────────────────────────────────┘
```

The chat area is a thin client. It renders state returned by the backend but does not run agents directly.

---

## 4. Core Features

### **4.1 Authentication**
- Login page shared with the rest of the frontend
- JWT stored in memory, not localStorage
- Automatic token refresh
- Tenant and role loaded from backend claims

### **4.2 Chat Interface**
- Markdown rendering
- Code block highlighting
- Streaming token display
- Support for images and file attachments
- Smooth auto-scrolling (including auto-scroll to bottom when navigating to an existing session)
- Retry last message
- Stop generation button
- Rendering of tool activity and other agent-side progress states when exposed by the backend
- Branch navigation controls when multiple branches exist at a message node (e.g. "2 / 3 ▶")
- Model name shown on each assistant message bubble

### **4.3 Model Selection**
- Dropdown listing models available to the tenant
- Default model pre-selected
- Disabled models hidden automatically

### **4.4 Templates & Prompts**
- Dropdown or library view listing templates available to the user or tenant
- Personal prompt library for reusable user-authored prompts
- Selected template or prompt can influence system prompt, default model, and allowed tools

### **4.5 Skills**
- Dropdown or launcher for skills available to the user
- A skill can map to a predefined Microsoft Agent Framework agent or workflow
- A selected skill can set defaults such as model, template, prompt, and allowed tools
- When a skill is changed, session tools are automatically synced — the old skill's tools are removed and the new skill's tools are added (always-on tools are preserved)
- Users can create, edit, and delete their own personal (private) skills
- Personal skills are scoped to the user and not visible to other users
- Personal skills follow the same structure as tenant skills: execution type, model, template, prompt, allowed tools

### **4.6 File Uploads**
- Upload files to backend
- Backend handles extraction, embedding, and tool-based processing

### **4.7 Session Management**
- Create new session (permanent or temporary mode)
- Rename / edit session title
- Delete session (with confirmation dialog to prevent accidental deletion)
- Pin / unpin a session (pinned sessions appear at top of list)
- Convert a temporary session to permanent (finalize — migrates messages from Redis to MariaDB)
- View session history
- Load previous messages
- Clear visual indicator when a session is in temporary mode
- Collapsible session list (desktop minimize)

### **4.8 Temporary Sessions**
- Users can start a session in temporary mode at creation time
- Temporary sessions are held in Redis with a TTL; they are not written to MariaDB
- Temporary sessions are automatically deleted on logout or TTL expiry
- Memory writes are disabled for temporary sessions
- File uploads are supported for both permanent and temporary sessions (auto-cleaned on session expiry)
- RAG embedding is disabled for temporary sessions
- The UI prominently marks temporary sessions so the user always knows the session will not be saved

### **4.9 Message Management**
- Edit any user or assistant message
  - Editing creates a new branch from the edited message's parent; the original branch is preserved
  - After editing a user message, the agent automatically regenerates a response in the new branch
- Delete a message (soft delete; hidden from view but branch integrity preserved)
- Regenerate the last assistant response
  - Creates a new branch sibling; previous response remains accessible
- Navigate between branches at any message node using inline branch controls
- Thumbs up / thumbs down feedback on any assistant message
  - Feedback is stored and surfaced in admin analytics

### **4.10 Search**
- Full-text search across the user's own sessions and messages
- Results scoped to the authenticated user's data within their tenant
- Search UI accessible from the session sidebar

### **4.11 Memory Management**
- View all memory items associated with the current user (paginated listing)
- Filter memory by session or view all (global memories always included alongside session-scoped ones)
- Delete individual memory entries
- Manually add a memory entry (pin a fact or context note)
- **Cross-session memory retrieval**: When enabled on a session, the agent semantically searches all of the user's memory entries from past conversations and injects relevant matches into context
- Entries created by the agent are marked as automatic; user-added entries are marked as manual
- All memory actions are proxied through the backend; no direct DB access from the frontend

### **4.12 Session Tool Activation**
- Users can view tools enabled for their tenant
- Users can activate or deactivate individual tools for the current session
- Only tools enabled by the tenant admin or manager are available for activation
- Tool activation state is stored per session in the backend
- The active tool list is sent to the agent on each request

---

## 5. UI Layout Structure

```
/frontend
  /src
    /features
      /chat
        /routes
        /components
          ChatWindow.tsx
          MessageBubble.tsx
          MessageBranchNav.tsx
          MessageFeedback.tsx
          ModelSelector.tsx
          TemplateSelector.tsx
          PromptLibrary.tsx
          SkillSelector.tsx
          PersonalSkillEditor.tsx
          FileUpload.tsx
          SessionSidebar.tsx
          SessionSearch.tsx
          MemoryManager.tsx
          SessionToolActivation.tsx
          TemporaryChatBadge.tsx
        /hooks
        /services
          chat.ts
        /state
```

Shared app-level providers, auth logic, and theme configuration live outside the feature module.

---

## 6. Navigation Structure

### **Sidebar**
- New Chat (with temporary / permanent mode toggle)
- Sessions list (pinned first, then recents)
- Search sessions
- Memory manager
- User settings
- Logout

### **Main Area**
- Chat window
- Input box
- Model selector
- Template selector
- Prompt library / quick prompt actions
- Skill selector (tenant skills + personal skills)
- Tool activation panel
- File upload button

---

## 7. API Integration

### **Authentication**
```
POST /auth/login
GET  /auth/me
```

### **Sessions**
```
POST   /chat/session
GET    /chat/session/:id
PUT    /chat/session/:id
DELETE /chat/session/:id
POST   /chat/session/:id/finalize   # Convert temp → permanent
GET    /chat/sessions/search
```

### **Messages**
```
POST   /chat/session/:id/message
GET    /chat/session/:id/messages
PUT    /chat/session/:id/message/:msgId
DELETE /chat/session/:id/message/:msgId
POST   /chat/session/:id/message/:msgId/regenerate
POST   /chat/session/:id/message/:msgId/feedback
```

### **Streaming**
```
POST /chat/session/:id/message (Accept: text/event-stream)
DELETE /chat/session/:id/stream
```

### **User Configuration**
```
GET /models
GET /templates
```

### **Prompts**
```
GET    /prompts
POST   /prompts
PUT    /prompts/:id
DELETE /prompts/:id
```

### **Skills**
```
GET    /skills
POST   /skills
PUT    /skills/:id
DELETE /skills/:id
```

### **Session Tools**
```
GET    /chat/session/:id/tools
POST   /chat/session/:id/tools/:toolId
DELETE /chat/session/:id/tools/:toolId
```

### **Memory**
```
GET    /memory
POST   /memory
DELETE /memory/:id
```

### **Files**
```
POST   /chat/session/:id/upload
GET    /chat/session/:id/uploads
GET    /chat/session/:id/upload/:fileId/url
DELETE /chat/session/:id/upload/:fileId
```

---

## 8. Security Considerations

- JWT stored in memory only
- No admin endpoints exposed in the chat area
- Tenant context enforced by backend
- File uploads validated server-side
- No sensitive data stored in browser
- Frontend feature visibility does not replace backend authorization

---

## 9. Goals of the Chat Area

- provide a clean, intuitive chat experience
- support real-time agent interactions
- allow users to select models, templates, prompts, and skills easily
- allow users to create and manage personal skills without admin involvement
- allow users to activate tenant-approved tools per session
- give users full control over their own memory entries
- support temporary sessions for privacy-sensitive conversations
- support non-destructive message editing and regeneration via branching
- enable users to search their own conversation history
- capture message feedback for model quality improvement
- keep all admin logic out of the user experience
- integrate cleanly with the backend and Microsoft Agent Framework runtime
