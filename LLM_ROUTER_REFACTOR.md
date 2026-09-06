# LLM router refactor report

## Outcome

AVA now uses one bounded LLM JSON planner for routing and task planning in the
real pipeline. The planner may select a finite combination of filing retrieval,
trusted web search, upload retrieval, evidence-backed calculation, direct
calculation, conversation, or clarification tasks. It does not execute tools or
control storage directly. Server code remains authoritative for owner isolation,
company/ticker validation, selected conversation scope, task and tool limits,
trusted web domains, calculator execution, citation resolution, and memory
ownership.

The original user query is retained unchanged. Planner task queries are internal
execution inputs only. The implementation preserves the hybrid filing retrieval
path, evidence allocation, citation rules, and SEC corpus boundaries.

## Implemented changes

### Typed plan and bounded execution

`TaskPlan` defines the strict JSON contract: a schema version, original query,
selected memory references, up to four tasks, and one final-answer task list.
The server rejects unknown task kinds, invalid tickers, duplicate IDs, invalid
dependencies, unsafe URLs, unapproved web sources, excessive tool calls, and
memory references that do not belong to the current owner or relationship.

Independent filing, upload, and web work is executed in bounded parallelism.
Evidence calculations run only after their cited evidence dependencies succeed.
If one bounded task fails, AVA retains supported evidence from other tasks rather
than fabricating the missing portion. Final answers are generated only from the
validated used evidence, with existing citation filtering retained.

### Reference and memory resolution

The planner receives owner-scoped semantic memory candidates and bounded recent
turns. It may use a saved preferred company to resolve a reference, but cannot
use memory as evidence for a CEO, market price, revenue, or other company fact.
It distinguishes relationships such as preferred company, favorite company,
favorite CEO, metric, and product so unrelated memories do not create false
company conflicts.

The session also added an owner-level **Enable memory** setting. Turning it off
prevents long-term-memory retrieval and new manual or chat-triggered saves while
retaining existing records for later management. Settings-created memories are
labelled “Saved by you”; chat-created records are labelled “Saved by AVA.”
Explicit memory writes remain deterministic and opt-in under the current router
contract; a proposed typed `memory_write` planner extension remains separate
future work.

### Provider-plan robustness

Live provider output exposed harmless presentation differences that previously
caused valid requests to stop before execution. The parser now normalizes only:

- one outer `json` Markdown fence;
- a redundant `sec_edgar` key on an otherwise normal filing task;
- a filing-shaped task that already declares approved current-fact web freshness
  and trusted web keys, converting it to the corresponding web task.

Strict validation runs after those narrow normalizations. The server does not
repair arbitrary fields, company scopes, URLs, dependencies, memory IDs, or tool
budgets. Planner instructions now explicitly cover `their`, a reply of `both`
to AVA's immediately preceding company clarification, and all companies in the
selected scope. For an unambiguous singular-pronoun follow-up, the server also
supplies one validated ticker only when AVA's immediately preceding answer named
exactly one company. This prevents a Tesla answer followed by “What vehicles are
they building?” from being treated as an eleven-company clarification.

### Trusted live-market quotes

Market-live search remains restricted to the reviewed market registry. Tavily
results are still screened for approved HTTPS domains. For an approved
Robinhood result, AVA can make one bounded same-domain page read and extract the
embedded price and quote timestamp only when the page declares real-time quotes.
The result also records retrieval-time market status and the source-declared
real-time delay before it can pass quote validation. No arbitrary URL fetches,
domains, or unverified article substitutes were introduced.

## Verified examples

The following live-provider plan shapes were captured and validated after the
refactor:

| User request | Validated plan/result |
| --- | --- |
| “Who is the CEO of Tesla listed in the 10-K filing, and what is their stock price?” | TSLA filing retrieval plus `TSLA current stock price` trusted market search. |
| “both” after an Aptiv/Aurora clarification | Separate APTV and AUR current-leadership web tasks. |
| “What is their current stock price?” after “Your preferred company is Rivian.” | `RIVN current stock price` trusted market task. |
| “Calculate this company’s operating margin for the latest two fiscal years…” in Mobileye scope | Two MBLY filing tasks plus an evidence-calculation task. |
| “What is my preferred company?” | Direct owner-scoped memory answer, with no filing or web evidence asserted. |

The live RIVN market path was also checked against the approved Robinhood page
and produced a qualifying bounded quote record.

## Verification and remaining boundaries

Regression coverage was added for provider-fenced JSON, implicit SEC filing
sources, current-leadership task shapes, selected-company operating-margin
plans, and approved Robinhood quote extraction. The final backend suite passed:
**446 passed, 3 skipped, 267 subtests**. The frontend checks previously passed
for the memory setting and provenance UI.

The planner is intentionally not an autonomous fallback system. It can still
safely reject malformed, unsafe, out-of-scope, or over-budget plans, and live
evidence may remain unavailable when a trusted provider cannot supply it. These
outcomes must be reported as evidence/tool availability issues rather than the
former generic failure of ordinary, unambiguous requests at plan validation.
