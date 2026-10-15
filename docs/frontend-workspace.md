# Conversation workspace

The October 2026 workspace uses warm neutral surfaces, a terracotta accent,
serif answer typography and a quiet reading column inspired by Claude. It keeps
AgentDemo's own identity and its local-first agent features.

## Workspace behavior

- Conversations and search stay in the left sidebar. The reading column is
  limited to 760px; runtime information opens on demand through **Details**.
- **Activity**, **Context** and **Tools** retain schedules, background tasks,
  citations, documents/reindexing, MCP capability information and tool history.
- Markdown answers support headings, lists, tables, fenced code and copy actions.
  Raw HTML is ignored. Links allow only absolute HTTP(S) URLs and open with
  `noopener noreferrer`. Remote image descriptions are displayed without loading
  third-party image URLs. Tool protocol sanitization remains a backend boundary.
- **Stop generating** aborts the active browser request. A partial answer stays
  visible until the user changes or reloads the conversation; only completed
  answers are persisted by the backend. This does not undo a tool side effect.
  Interrupted approvals remain consumed, so they cannot be replayed accidentally.
- Switching conversations aborts the old transport and rejects late tokens,
  completion events and refresh results from that stream. Unexpected SSE EOF is
  reported as an incomplete connection; the client never automatically resends it.
- New content follows the bottom while the reader is there. Scrolling back keeps
  the reading position and shows **Latest**. Enter submits, Shift+Enter adds a
  line, and IME composition does not submit.

## Responsive and accessible controls

Below 1100px the sidebar and Details become drawers with an inert background,
focus containment, Escape dismissal and focus restoration. Buttons have visible
keyboard focus. The composer supports safe-area insets and a scrollable schedule
form on short screens. Tables and code scroll independently. Both themes use
semantic colors, and reduced-motion preferences suppress transitions/animations.

Verified viewports include 1440x900, 375x812, 320x640 and 812x375. These checks
cover the composer, approval buttons, task panel, context and schedule form.
Browser viewport simulation does not substitute for every physical mobile
keyboard or assistive-technology combination.

## Toolchain and checks

Use Node 24 LTS (minimum 22.12), then run from `frontend`:

```text
npm ci
npm test
npm run build
npm audit --audit-level=high
```

The lockfile pins React 19.3.1, Vite 8.3.4, TypeScript 7.0.2 and the Markdown
renderer. CI uses Node 24. The app uses React's deferred rendering for answer
Markdown and conversation filtering; experimental rendering modes are not
required. Production output remains a static Vite bundle with no font CDN.

Tests cover SSE framing/terminal markers, transport aborts, stale stream events,
retained partial answers, manual reading position, modal behavior, Markdown
safety and clipboard failure recovery. Existing runtime trace and schedule tests
continue to run. Browser acceptance uses a clearly labelled local model/MCP
fixture, so it verifies UI and persistence rather than paid-provider answer quality.

During acceptance, memory compaction exposed an expired SQLAlchemy approval
object after a transaction rollback. Approval finalization now explicitly reloads
the object before writing completion. A real PostgreSQL regression verifies both
the final status and rejection of a second claim.

Official upgrade references checked on 2026-10-15:
[React 19.3](https://react.dev/blog/2026/09/09/react-19-3),
[Vite migration guide](https://vite.dev/guide/migration),
[react-markdown security](https://github.com/remarkjs/react-markdown#security).
