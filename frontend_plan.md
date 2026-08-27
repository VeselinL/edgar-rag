# AVA Frontend Implementation Plan

**Status date:** 20 August 2026
**Scope:** first local stateless frontend and streaming API vertical slice

## Product identity

- Product name: **AVA**.
- Full name: **Autonomous Vehicle Analyst**.
- The historical internal repository name must not appear in visible UI, HTML title/description, favicon text, accessibility labels, errors, or source presentation.
- The verified canonical avatar is `src/frontend/avatar/ava.png` (`avatar/ava.png` relative to the frontend project). It must not be regenerated, redrawn, recoloured, moved, or destructively cropped.
- The verified favicon is the separate `src/frontend/avatar/favicon.png`.
- AVA is a restrained product mark, not a human-like mascot or conversational character. Do not add an artificial personality, biography, face, onboarding story, or decorative animations.

## Responsibility boundary

The frontend sends the user's original query unchanged in `{ "query": string }`. It must not detect or rewrite company names, tickers, aliases, Comparison Cues, or subqueries.

The backend owns:

```text
regex company/ticker/alias detection
→ Comparison Cue and scope classification
→ LLM atomic-subquery planning
→ scope-aware dense + BM25 retrieval for each subquery
→ RRF ranking and stable-ID deduplication across subqueries
→ minimum 2 available chunks per subquery
→ multi-subquery bonus and final 10-chunk evidence selection
→ grounded generation and citation resolution
→ frontend-safe source normalization
```

The waiting bubble stays visible during all planning, scope detection, retrieval, merging, deduplication, and final evidence selection. It disappears only when the first non-empty `delta` event arrives.

## Overall visual direction

Use the familiar structure shared by modern chat products without cloning any one product:

- minimal, spacious, and content-focused;
- one centered chat column;
- thin sticky top header;
- composer sticky near the bottom and aligned with the content column;
- no sidebar, profile controls, empty navigation, dashboard cards, gradients, glassmorphism, excessive shadows, decorative financial charts, official SEC branding, or internal repository branding.

The interface should feel calm and analytical. Borders establish structure; colour is reserved for actions, focus, and citation affordances.

## Theme system

Set theme tokens once on `:root` and override them through `html[data-theme="dark"]`. Components consume semantic variables, not scattered literal theme colours.

### Light theme tokens

```css
--page: #FFFFFF;
--surface: #FFFFFF;
--surface-subtle: #F8FAFC;
--text: #172033;
--text-secondary: #667085;
--border: #E5E7EB;
--primary: #55AEEF;
--primary-strong: #268FD8;
--citation: #E9A62F;
--focus: #1677B8;
--error: #B42318;
```

The principal page background is exactly white. Very light hover/focus surfaces are allowed, but no large grey page panels.

### Dark theme tokens

```css
--page: #07111F;
--surface: #0B1828;
--surface-subtle: #0F2032;
--text: #EAF2FA;
--text-secondary: #91A4B7;
--border: #1B2C3D;
--primary: #6BBEFF;
--primary-strong: #8ACBFF;
--citation: #F2B84B;
--focus: #8ACBFF;
--error: #FDA29B;
```

Dark mode uses very dark blue, never pure black. Text, interactive controls, focus rings, links, and amber citation controls must satisfy WCAG AA contrast in context.

### Initialization and persistence

An inline script in `index.html` runs before the CSS/app bundle. It reads `localStorage['ava-theme']`; if absent, it reads `matchMedia('(prefers-color-scheme: dark)')`, then sets `document.documentElement.dataset.theme`. This avoids a wrong-theme flash. React initializes from that applied value and saves explicit changes to `localStorage`. System preference changes affect only users who have not made a stored selection.

## Header

Use a 56–60 px sticky header with a bottom border and page-background colour.

Top left `Brand`:

- 32 px AVA avatar with consistent `object-fit: contain`;
- primary label `AVA`;
- secondary label `Autonomous Vehicle Analyst`, visibly rendered without a tooltip requirement.

On very narrow screens, stack or reduce the secondary label rather than removing the full name from the interface. The empty state also repeats the full name.

Top right contains exactly one icon button:

- sun icon while light mode is active;
- moon icon while dark mode is active;
- `aria-label` and tooltip describe the action, for example `Switch to dark theme`;
- no permanently visible “Light mode” or “Dark mode” text;
- visible hover and focus states.

No other header buttons.

## Avatar and favicon treatment

Use `avatar/ava.png` for the header, empty state, and every assistant row. Do not show it beside user messages.

The image is square and must use `object-fit: contain`; never stretch or cover-crop it. Use 32 px in the header, 36 px beside desktop assistant responses, 32 px on mobile, and approximately 72 px in the empty state. In dark mode, place it on a subtle light circular backing (`#F8FAFC`) with 2–4 px inset spacing so dark outlines remain legible. This is CSS treatment and does not alter the file.

Copy `avatar/favicon.png` through Vite as the page favicon. Use `AVA — Autonomous Vehicle Analyst` as the document title.

## Empty state

Before the first successful or attempted submission, center a restrained empty state in the available space above the composer:

- large AVA avatar;
- heading `Ask AVA`;
- visible subheading `Autonomous Vehicle Analyst`;
- one short sentence: `Ask questions grounded in SEC 10-K filings from ten companies in the autonomous-vehicle ecosystem.`;
- composer directly below or visually connected to this content.

Do not add feature cards, marketing panels, onboarding slides, or a sidebar. Up to three corpus-supported example-query buttons may appear in small secondary text below the description, but omit them if they compete with the composer.

## Conversation layout

Use a centered column with `max-width: 820px`. Desktop horizontal page padding is 24 px; mobile padding is 14–16 px. Reserve sufficient bottom padding so the sticky composer never covers the last answer or sources.

### User messages

- right-aligned;
- maximum width around 78%;
- compact rounded bubble with theme-appropriate subtle blue surface;
- no user avatar;
- `white-space: pre-wrap` to preserve intentional line breaks;
- long strings wrap rather than overflow.

### AVA responses

- left-aligned row with AVA avatar in a fixed first column;
- answer text sits directly on the page, not inside a large coloured bubble;
- comfortable line height around 1.65 and readable paragraph spacing;
- safe Markdown supports paragraphs, headings no larger than the page hierarchy permits, lists, emphasis, inline/fenced code, and links;
- links open safely with appropriate rel attributes when opening a new tab;
- no raw HTML rendering and no unsanitized `dangerouslySetInnerHTML`.

The visible transcript lives only in React memory. It is not stored in `localStorage`, IndexedDB, cookies, or the backend. Each submission sends only that new query; prior displayed messages are never included.

### Scroll behaviour

The conversation scroller tracks whether the user is within roughly 80 px of the bottom. While near the bottom, new fragments scroll to the latest content using instant or minimal movement. If the user scrolls upward beyond the threshold, streaming must not pull them back down. Submitting a new query explicitly scrolls its new response row into view.

## Composer

The composer is sticky above the viewport bottom/safe area, aligned to the 820 px conversation width. Its containing surface uses the page colour with a small top fade implemented as a solid-to-transparent mask only if needed; do not introduce a decorative gradient. If a fade violates the no-gradient direction, use an opaque page backing and top border.

It contains:

- an explicitly labelled multiline `<textarea>`;
- placeholder `Ask about the filings…`;
- auto-growth from one line to a maximum near 180 px, after which it scrolls internally;
- a single send-arrow `<button type="submit">` with accessible label;
- a short helper line `Enter to send · Shift+Enter for a new line` if space permits.

Behaviour:

- Enter submits when not composing text with an IME;
- Shift+Enter inserts a newline;
- mouse and keyboard submission share one form handler;
- empty/whitespace-only input does not submit and exposes a concise validation status;
- duplicate submission is disabled while a request is active;
- submitted query is immediately added to the transcript;
- clear the textarea once `fetch` has successfully begun;
- if request setup fails before it begins, retain/restore the text and focus the textarea;
- after completion/error, re-enable the form and return focus in a non-disruptive way.

Do not add attachments, microphones, model selectors, tools, search switches, or menus.

## Waiting state

Immediately after a valid submission:

- append an empty AVA response row;
- show AVA's avatar;
- anchor a small speech/thought bubble above or slightly above-right of the avatar;
- animate three dots subtly inside it;
- expose one polite live status such as `AVA is finding evidence and preparing an answer.`

The decorative dots use `aria-hidden="true"`. Under `prefers-reduced-motion: reduce`, show static dots or a low-frequency opacity change. The waiting bubble is controlled by response state, not a timer. It disappears synchronously with receipt of the first non-empty `delta`, before that fragment is rendered.

## Token streaming

Use browser `fetch` with a JSON POST body. Parse SSE framing incrementally with a `TextDecoder` in streaming mode and retain incomplete trailing frames between reads.

Rules:

- do not wait for the complete response;
- do not split complete text in JavaScript;
- add no fake character/token delays;
- append `data.text` exactly and immediately for every non-empty `delta`;
- do not trim fragments, because leading/trailing whitespace may be meaningful;
- update only the active assistant message rather than reconstructing all transcript state unnecessarily;
- keep the request active until `done` or a terminal failure;
- reject malformed event JSON/type with a safe stream error;
- cancel through `AbortController` on component teardown;
- ignore stale events from an aborted request ID.

If the stream fails after partial output, preserve the text, set the message state to `error`, and show `The response was interrupted. Please try again.` beneath it. If it fails before any token, replace the waiting bubble with the concise error. A retry creates a new current-query request; it never submits previous transcript context.

## Sources

Source controls belong to the assistant message that produced them. Show the control only after a `sources` event arrives or generation completes with an empty source list.

Use a compact amber-accented button:

```text
View sources (3)
```

The button uses `aria-expanded` and `aria-controls`. Sources expand directly below the answer as an accordion/list for this version, keeping evidence and answer spatially associated. Each entry is a semantic `<article>` or `<details>` with a descriptive heading, visible focus, and keyboard-operable controls. Do not show raw chunk IDs, retrieval scores, RRF values, or reranker scores.

If no citation IDs resolve and the backend returns final-context fallback sources, label the collection `Retrieved evidence`. If the backend returns zero sources after an otherwise successful answer, show a subtle status: `No source references were available for this answer.` Never imply unsupported evidence.

### Narrative source

Display:

- company name and ticker;
- filing year;
- section;
- complete original chunk text using readable whitespace and wrapping;
- optional valid SEC filing link.

Do not destructively truncate long text. It may sit in a bounded scroll region with an explicit “Show full source” control only if the full content remains reachable and searchable.

### Structured table source

Display company/ticker, filing year, section, trustworthy title, units, and optional filing link. Render `headers` and `rows` directly as:

```html
<div class="table-scroll" tabindex="0">
  <table>
    <thead>…</thead>
    <tbody>…</tbody>
  </table>
</div>
```

Use `<th scope="col">` for headers. Preserve header and row order, empty strings as empty cells, and every supplied value exactly. Use `column_units` only to choose numeric alignment when reliably marked numeric; do not infer values or units. Wide tables scroll horizontally within `.table-scroll`; the page itself must not overflow. Do not parse Markdown.

## State and component structure

Use local React state and focused hooks; no global state library.

```text
App
├── Header
│   ├── Brand
│   └── ThemeToggle
├── EmptyState
├── Conversation
│   ├── UserMessage
│   └── AssistantMessage
│       ├── AvaAvatar
│       ├── WaitingBubble
│       ├── StreamedAnswer
│       └── Sources
│           ├── NarrativeSource
│           └── TableSource
└── Composer
```

Suggested focused modules:

- `api/chatStream.ts`: request and incremental SSE parser;
- `types.ts`: discriminated API source and transcript types;
- `hooks/useTheme.ts`: initialization, toggle, and persistence;
- `components/*`: presentation and small event handlers;
- `App.tsx`: transcript state machine and request orchestration.

### Request/message state machine

```text
idle
  └─ valid submit → submitting
submitting
  ├─ fetch/reader established → waiting_for_first_token
  └─ setup failure → error
waiting_for_first_token
  ├─ first non-empty delta → streaming
  ├─ terminal error → error
  └─ done without delta → completed (empty-answer warning)
streaming
  ├─ more delta/sources → streaming
  ├─ done → completed
  └─ disconnect/error → error (partial answer retained)
completed
  └─ next valid submit → submitting on a new message pair
error
  └─ next valid submit/retry → submitting on a new request
```

`submitting` inserts the transcript rows and starts `fetch`. `waiting_for_first_token` shows the bubble. `streaming` shows answer text and no bubble. `completed` enables sources and final status. `error` shows a safe recoverable status. Theme changes are independent of request state and must not cancel or restart a stream.

## Error and edge states

- **Empty query:** do not call the API; retain focus and provide `Enter a question to continue.` in an accessible status.
- **Backend unavailable:** retain or restore unsent text if setup never began; otherwise keep submitted transcript and show `AVA is unavailable right now. Please try again.`
- **Retrieval/generation failure before first token:** remove the waiting bubble and show a concise retryable error in the response row.
- **Stream interruption after partial output:** preserve partial answer, append interruption status, and re-enable composer.
- **No answerable evidence:** render the backend's grounded abstention normally and show any final evidence it actually used.
- **Answer with no resolvable citation:** show backend-provided final-context sources as `Retrieved evidence`; if none exist, show the no-reference status.
- **Malformed narrative source:** skip only the malformed source, keep valid siblings, and show `One source could not be displayed.`
- **Malformed table source:** never guess structure or parse Markdown; show metadata plus `This table source could not be displayed.`
- **Extremely long narrative:** wrap and preserve all evidence; optionally use an accessible bounded viewer.
- **Wide financial table:** keep full table, use container horizontal scrolling, sticky first column only if it does not obscure data.
- **Repeated submission:** disabled button and guarded submit handler make it a no-op while active.
- **Theme switch during stream:** update theme tokens only; text, request controller, scroll lock, and message state remain intact.

Never expose stack traces, prompts, model deployment names, credentials, or provider error bodies.

## Responsive behaviour

### Desktop

- 820 px maximum conversation/composer width;
- stable sticky header and composer;
- comfortable vertical spacing;
- AVA avatar remains in its own column beside responses;
- user bubbles remain narrower than the column.

### Mobile

- use dynamic viewport units (`100dvh`) and safe-area insets rather than fixed desktop viewport heights;
- reduce outer padding to 14–16 px;
- keep brand and full name readable through smaller type or two-line brand text;
- reduce assistant avatar slightly but never hide it;
- allow user bubbles up to roughly 88%, not 100%;
- keep composer fully within viewport and bottom safe area;
- preserve at least 44×44 px tap targets;
- tables scroll inside source cards;
- source buttons and accordion controls remain easy to tap.

Test at approximately 390×844 and at a narrow 320 px width in addition to desktop.

## Accessibility

- Use semantic `<header>`, `<main>`, `<form>`, `<textarea>`, `<button>`, lists, articles, headings, links, and tables.
- Every icon button and form control has an accessible name.
- Avatar alt text is `AVA`; decorative repeated imagery may use empty alt only when adjacent visible text supplies the exact identity.
- Keyboard focus order follows visual order, and every interactive control has a high-contrast `:focus-visible` ring.
- Enter/Shift+Enter behaviour respects IME composition.
- Loading, completion, and error use a polite status/live region. The streaming text container is not a live region, preventing every token from being announced.
- Completion is announced once, for example `AVA's response is complete.`
- Three-dot animation is hidden from assistive technology and respects reduced motion.
- No status or distinction relies on colour alone.
- Source expansion exposes `aria-expanded` and moves no focus unexpectedly.
- Horizontal table containers are keyboard-focusable and have an accessible label when overflow is present.

## Testing plan

### Frontend unit/component tests

- theme uses stored preference, otherwise OS preference, and persists toggles;
- header shows correct sun/moon action and accessible label;
- Enter submits, Shift+Enter inserts a newline, IME composition does not submit;
- empty and duplicate submission do not issue requests;
- waiting bubble appears before retrieval completes;
- first empty delta is ignored and first non-empty delta removes the bubble;
- fragments concatenate exactly, including whitespace;
- `done` completes and re-enables input;
- pre-token error replaces waiting state;
- mid-stream error preserves partial answer;
- narrative source shows complete text and metadata;
- structured table renders semantic header/body cells in order;
- no raw chunk ID appears visibly;
- wide table uses an overflow container;
- buttons, textarea, theme toggle, sources, loading, and error statuses have accessible labels;
- previous visible turns are absent from subsequent request bodies;
- production build and lint/type checks pass.

### Backend tests

- health response distinguishes mock/real and readiness;
- request rejects empty/over-limit input;
- SSE success event ordering is `delta+`, `sources`, `done`;
- empty provider fragments are not emitted;
- pre-token and mid-stream failure events are safe;
- citation resolution cannot select chunks outside final evidence;
- narrative normalization maps `narrative` to `text` and preserves complete text;
- table normalization uses logical headers/rows and preserves empty cells;
- malformed logical tables fail safely without Markdown parsing;
- single company, ticker, and alias detection regression cases;
- two-company query and Comparison Cue cases;
- comparison final context contains each available target company;
- global no-company scope;
- duplicate chunk removal across scoped retrievals;
- final context never exceeds 12 unique chunks;
- API/evaluation entry points return equivalent scope diagnostics and pre-normalization chunk IDs;
- frontend bundle/config contains no backend secret values.

### Manual visual matrix

Inspect and capture results for:

| View | Light | Dark |
|---|:---:|:---:|
| Desktop empty state | required | required |
| Desktop waiting/mid-stream | required | required |
| Desktop completed narrative + wide table | required | required |
| Mobile empty state | required | required |
| Mobile waiting/mid-stream | required | required |
| Mobile completed narrative + wide table | required | required |
| Partial-response error | required | required |

Confirm in every avatar-bearing view that the supplied image is contained, correctly proportioned, visible against its backing, and not clipped.
