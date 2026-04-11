# The Resolve Story

> *An AI engineering journey told through the growing pains of a company that bet its product on agents.*

This is the story behind the exam domains. Each chapter of this study lab was built to solve a problem that Resolve — a fictional AI-native SaaS company — actually faced as it grew. The problems are real. The architectural mistakes are common. The fixes are what the Anthropic Certified Architect exam tests.

If you want to understand *why* each domain matters before you study *how* to pass it, start here.

---

## The Company

**Resolve** is a B2B AI customer success platform, built in London. SaaS companies subscribe to let Resolve's agents automatically triage, investigate, and resolve customer support tickets. Tier-1 tickets are closed without a human. Complex tickets are researched and handed off with a full context summary. Account managers get weekly trend reports with no one running a query.

The platform is run by a small engineering team that grew from 2 to 14 people over eighteen months. This is their story.

---

## The Characters

**Sofia** — Co-founder and CEO. Former VP of Customer Success at a Series D fintech. She knows support workflows better than she knows her own calendar, which is why every agent failure lands in her inbox before engineering knows about it.

**Arnaud** — CTO and first engineer. Built the MVP in five weeks. Deeply pragmatic. Has opinions about everything and is right about most of it. Chose Claude because he read the system prompt paper three times.

**Jade** — AI Engineer. Joined from a research lab after the seed round. Understands transformer architectures, attention heads, and context window dynamics better than anyone on the team. Has never operated a production system at scale. Learning fast.

**Marcus** — Senior Backend Engineer. Joined at 200 customers. Has been burned by three ML projects that "worked in demo." Trusts systems he can reason about. Slowly becoming a believer.

**Priya** — Head of Customer Success at Resolve (meta: she is what the agents are replacing at the companies they sell to). On the frontline every time an agent gives a customer wrong information or sends a reply that makes no sense.

---

## Chapter 1 — The Agent That Wouldn't Stop

*Domain 1 — Agentic Architecture & Orchestration*

*Phase — 200 enterprise customers, 8,000 tickets/day*

The first version of the resolution agent was beautiful.

Jade had built it over two weeks. It received a ticket, called a tool to pull the customer's account history, called another to search the knowledge base, called a third to check if there was a known incident, drafted a reply, and closed the ticket. In demo, it handled everything. Support managers at client companies watched it work and immediately started drafting cost-saving projections.

The Monday after the demo, Arnaud got an alert from the billing dashboard at 6 AM.

The agent had spent $11,400 on API calls overnight.

An edge case had created a loop. The ticket was in Portuguese. The knowledge base search returned nothing. The agent decided it needed more context. It called the account history tool again. Still no relevant results. It decided to try the knowledge base one more time with slightly different wording. It ran for four hours until the API rate limiter finally stopped it — but by then, it had made 14,000 tool calls on a single ticket.

*"How do you know when the agent is done?"* Marcus asked at the post-mortem.

Jade thought about it. *"When it has an answer."*

*"But what if it never has an answer?"*

There was silence.

The agent had no concept of loop termination based on state. It was checking the Claude response for phrases like "I have enough information" — natural language, parsed by the model itself. When the model was uncertain, it never said those words. The loop had no floor.

Arnaud rewrote the orchestration layer from scratch. Loop termination moved out of the model's judgment entirely. Every iteration increments a counter. The `stop_reason` field on the API response drives whether the loop continues. Maximum iterations are set at the system level. If a ticket exceeds the complexity budget, it escalates — not based on what Claude says it wants to do, but based on a programmatic decision the code makes.

*"The model decides what to do,"* Arnaud said. *"The system decides when to stop."*

They also introduced hooks. Pre-call hooks validate that the tool being called makes sense for the current ticket state. Post-call hooks check that the output is within expected bounds before it's passed back to the model. The hooks are code. They don't rely on Claude to police itself.

The $11,400 incident never happened again. But it introduced a new question: who decides when to hand off to a human?

The first version used sentiment analysis on the ticket text. If it sounded frustrated, escalate. It worked for obvious cases but missed everything in between — a politely worded churn risk, a calm message hiding a billing error that was about to trigger a chargeback. Jade wanted to add more sentiment dimensions. Marcus pushed back.

*"We're asking the model to assess emotion to decide whether to call a human. That's two layers of uncertainty stacked on one decision. Use something you can reason about."*

They switched to complexity scoring: ticket type, number of unresolved prior interactions, account tier, presence of billing-related keywords. Deterministic criteria. When any threshold was crossed, escalation happened — not because Claude decided it felt right, but because the conditions were met.

*"Escalation logic,"* Marcus wrote in the post-mortem, *"should never depend on how the model is feeling today."*

→ **[Domain 1 — Agentic Architecture & Orchestration](./roadmap.md#domain-1--agentic-architecture--orchestration-27)**

---

## Chapter 2 — The Setup That Lived in Someone's Head

*Domain 2 — Claude Code Configuration & Workflows*

*Phase — 400 enterprise customers, 15,000 tickets/day*

Resolve hired its third engineer on a Tuesday. By Thursday, she had given up trying to set up the development environment on her own and was waiting for Jade to become available.

Jade had been the only person who understood how the agent codebase was structured. There were no setup instructions. The CLAUDE.md file — which was supposed to define how Claude Code should behave in the project — didn't exist. The custom commands that Jade used to run test suites, generate synthetic tickets, and validate agent output were shortcuts she'd added to her local Claude Code config months ago and never shared.

The new engineer spent two days in a chat thread trying to reproduce an agent behaviour she'd seen in the demo. Jade sent her eleven separate messages explaining context that should have been written down once.

*"Every hour you spend explaining the setup is an hour you didn't spend building,"* Sofia said after the second such onboarding.

Arnaud called it the *bus problem*. If Jade was unavailable, the AI half of the product was effectively blocked.

They spent a week on what Jade called *codifying the context*.

A `CLAUDE.md` at the project root now tells Claude Code everything it needs to know before touching the codebase: the agent architecture, which files control which behaviour, which commands to run to simulate a ticket, what the output schema must look like, and which constants should never be changed. A second `CLAUDE.md` in the `agents/` subdirectory describes the agent loop in detail — the state machine, the tool call budget, the escalation conditions. A third lives in `evals/` and describes how to interpret evaluation output.

Custom slash commands replaced muscle memory. `/test-ticket` runs a synthetic ticket through the full agent pipeline and prints the decision path. `/validate-schema` checks that the agent output conforms to the current JSON schema. `/dry-run-deploy` runs the agent in non-interactive mode against a staging ticket batch and prints a diff against expected outputs.

The CI pipeline runs the agent in non-interactive mode on every PR. If the output schema breaks, the PR fails. If the tool call count exceeds the budget on synthetic tickets, the PR fails. The pipeline doesn't need a human to remember to check these things.

The new engineer set up her environment in 45 minutes. She sent Jade one question.

*"The only documentation that doesn't go stale,"* Arnaud said, *"is the documentation the system enforces."*

→ **[Domain 2 — Claude Code Configuration & Workflows](./roadmap.md#domain-2--claude-code-configuration--workflows-20)**

---

## Chapter 3 — The Output That Wasn't an Output

*Domain 3 — Prompt Engineering & Structured Output*

*Phase — 600 enterprise customers, 30,000 tickets/day*

For six months, the ticket routing system had worked perfectly. The resolution agent returned a JSON object with a `decision` field, a `confidence` score, a `resolution` field or `escalation_reason`, and a `next_steps` array. The routing system read the JSON and acted on it.

Then Jade updated the system prompt to make the agent sound warmer.

For three days, nobody noticed anything wrong. The tickets were being processed. Support dashboards were green. On the fourth day, Priya got a message from a client's Head of Support.

*"Your agent replied to 200 tickets this week telling them to 'consider their options and reach out if they need anything.' Is something broken?"*

The new system prompt had inadvertently shifted the model's behaviour. Instead of calling the `resolve_ticket` tool and returning a structured decision, the agent was sometimes generating a friendly response in plain text and ending there. The routing system received a string instead of a JSON object. It silently failed to parse it and fell back to a default action: send a generic acknowledgement and close the ticket.

Eight hundred tickets had been processed this way. None of them had been routed, investigated, or escalated. They had all received the same three sentences and been marked closed.

The post-mortem identified the root cause immediately. The agent was not *required* to produce structured output — it was *asked* to. The system prompt said "always return a JSON object." But the model had found a plausible way to satisfy the instruction in spirit while breaking it in practice.

*"You can't ask the model to be disciplined,"* Jade said. *"You have to make it structurally impossible to be undisciplined."*

The fix was architectural. The `decision` field was no longer generated by asking Claude to write JSON. It was generated by calling a `resolve_ticket` tool whose schema enforced the exact structure. The `tool_use` stop reason became the only valid end state for a completed ticket. Any other stop reason was treated as a failure, not a completion, and triggered a retry loop with a more explicit prompt.

They also redesigned the JSON schemas. Every field had a description. Enum values replaced free text wherever the domain allowed it. Required fields were marked. Nullable fields had explicit fallbacks. The schema was checked into the repo and versioned. When the schema changed, it was a deliberate change with a diff, a review, and a migration plan — not a side effect of updating a system prompt.

*"The schema is the contract,"* Marcus said. *"Everything else is an implementation detail."*

The few-shot examples in the prompt were rebuilt from real production tickets — one example for each decision type, each labelled not just with the correct output but with an explanation of why that output was correct. Edge cases got their own examples.

The generic acknowledgement batch was re-processed. Priya sent apology emails to three clients.

→ **[Domain 3 — Prompt Engineering & Structured Output](./roadmap.md#domain-3--prompt-engineering--structured-output-20)**

---

## Chapter 4 — The Tool That Told No Lies

*Domain 4 — Tool Design & MCP Integration*

*Phase — 800 enterprise customers, 50,000 tickets/day*

The CRM integration had always been the most important tool in the agent's toolkit. Before replying to any ticket, the agent called `get_account_status` to retrieve the customer's subscription tier, open invoices, recent activity, and support history. Without this context, every reply was generic. With it, the agent could give accurate, account-specific answers.

The CRM also had a flaky API. Once or twice a week, it timed out. The tool's error handling had been written in a hurry during the initial integration. When the CRM didn't respond, the tool returned an empty object — `{}` — because returning *something* felt safer than raising an exception.

For months, this was invisible. The agent received the empty object, interpreted it as "no account data found," and continued. Tickets got vague replies, but they got replies.

Then a client upgraded to an enterprise plan that included a 2-hour SLA on Tier-1 tickets. On a Tuesday afternoon, the CRM was intermittently down for ninety minutes. During that window, four hundred tickets were processed by an agent that had no account context. Forty-three of those tickets were billing disputes. The agent had no idea any of them had outstanding invoices. It told every customer their account looked fine.

Priya found out because five of them escalated the next morning, furious that they'd been told everything was fine and then received a dunning notice an hour later.

The post-mortem found the empty object. Marcus was not calm.

*"The tool lied,"* he said. *"It said 'here is the account data' and handed the agent an empty box. The agent had no way to know the box was empty because the CRM was down or because the customer genuinely had no history. Those are completely different situations."*

They rewrote every tool in the integration layer.

Each tool now returns a structured response object with three possible shapes: a success payload with the requested data, an access failure with a specific error code and human-readable reason, and an empty result — explicitly typed as empty, not null, not an omitted field, but a typed empty — for when the query succeeded but returned nothing.

The agent now receives a `{ "status": "access_failure", "code": "CRM_TIMEOUT", "message": "CRM unavailable" }` instead of `{}`. It knows not to proceed. It knows to escalate. It knows to tell the customer that it's looking into their account and will follow up — not that everything is fine.

They also wrote tool descriptions as seriously as they wrote code. Every tool has a description that explains not just what it does but when to call it, what to do when it fails, and what an empty result means in context. The description field is not a comment — it is the model's only source of truth about the tool's behaviour.

Jade brought in MCP properly. The CRM, the knowledge base, and the billing system became separate MCP servers. Each had a clear capability boundary. The agent couldn't accidentally call a billing mutation when it meant to call a billing lookup. The server boundary made the mistake structurally impossible.

*"A tool that hides failure,"* Arnaud said, *"is worse than no tool at all. At least with no tool, the agent knows it doesn't know."*

→ **[Domain 4 — Tool Design & MCP Integration](./roadmap.md#domain-4--tool-design--mcp-integration-18)**

---

## Chapter 5 — The Agent That Forgot Itself

*Domain 5 — Context Management & Reliability*

*Phase — 1,200 enterprise customers, 80,000 tickets/day*

Long threads were the last unsolved problem.

Single-message tickets were reliable. Short conversation threads with three or four turns were reliable. But enterprise clients had tickets that had been open for weeks — forty, sixty, eighty messages deep. The customer had spoken to three different support agents, been escalated twice, had a call that was logged as a note, and had submitted screenshots that had been described in text by the previous agent because the vision attachment had broken.

When Resolve's AI took over one of these threads, something strange happened. By turn thirty of the context, it started contradicting its earlier statements. It would confirm that a refund was being processed and then, fifteen messages later, ask the customer to provide the information needed to start the refund process. It would apply the correct account tier discount and then, deep in the thread, calculate pricing as if the customer were on the default tier.

*"The agent is forgetting things,"* Priya reported. *"Specifically, it's forgetting things it said twenty minutes ago."*

Jade knew what was happening. The system prompt was at the top of the context. The tools and their outputs grew in the middle. By the time the model was generating turn thirty, it was paying more attention to the recent conversation than to the instructions it had received at the very beginning. The persona had drifted. Critical account context — the tier, the active discount, the refund status — had been mentioned once, early, and was now being outweighed by the sheer volume of everything that came after it.

*"What if we just summarise the thread?"* Marcus suggested.

Jade shook her head. *"We tried that. Summarisation at the wrong moment throws away exactly the things you needed to keep. You compress the wrong details."*

They designed a context management strategy. Critical account facts — the customer's tier, any active credits, any commitments already made in the thread — were pinned. Not summarised. Pinned. Extracted at each turn and placed immediately after the system prompt in a structured block labelled `ACTIVE CONTEXT`. The block was overwritten at each turn, not appended. It stayed fresh. It stayed authoritative. It was always visible.

The thread history was compressed using an approach with a strict rule: every compression preserves commitments and contradictions at full fidelity. Generic pleasantries were discarded. The specific sentence *"I can confirm your refund will be processed within 3–5 business days"* was never summarised as *"refund discussed."*

Escalation logic was also updated. A thread that had been compressed more than twice was automatically flagged for human review before the agent continued. Not because the agent had failed — but because the information provenance was now uncertain enough that a human should verify the context before commitments were made.

*"Context isn't just about fitting in the window,"* Jade said. *"It's about what the model chooses to pay attention to. You can't control attention directly, but you can control what you put where."*

The long-thread problem became a solved problem. More importantly, it became a *policy* — written down, reviewed quarterly, updated when behaviour changed.

Priya stopped getting the contradiction complaints. The clients with the oldest, most complicated tickets became the most satisfied. The agent remembered everything it had ever said to them, in every conversation, at every tier of the thread.

→ **[Domain 5 — Context Management & Reliability](./roadmap.md#domain-5--context-management--reliability-15)**

---

## What's Next

Resolve keeps growing. The problems keep changing.

| Chapter | Customers | The Problem |
|---|---|---|
| 6 — Scale | 5,000 | Agent handles 500K tickets/day. Batch API cost optimisation becomes urgent. Parallel agent fleets need coordination. |
| 7 — Governance | 10,000 | Enterprise client requires audit log of every agent decision. AI Act compliance assessment. Model version pinning policy. |
| 8 — Capstone | 20,000+ | Multi-region deployment. Agents handling 8 languages. Full CCA-level architecture review before Series C due diligence. |

---

## How to Use This Lab

Each domain is grounded in a chapter of Resolve's story. Read the chapter first. Understand the failure. Then study the domain — not as abstract knowledge, but as the answer to a real problem.

If you are preparing for the CCA exam:

- **Domain 1 — Agentic Architecture** → Chapter 1: understand loop termination, hooks, and escalation logic before anything else. It's 27% of the exam.
- **Domain 2 — Claude Code** → Chapter 2: the most configuration-dependent domain. Either you know where the files go or you don't.
- **Domain 3 — Prompt Engineering** → Chapter 3: know the difference between *asking* for structure and *enforcing* it. The exam is full of plausible wrong answers here.
- **Domain 4 — Tool Design & MCP** → Chapter 4: tool descriptions are worth more marks than most candidates expect.
- **Domain 5 — Context Management** → Chapter 5: smallest domain but failures here cascade into every other domain.

→ **[Full roadmap with study plan and resources](./ROADMAP.md)**

→ **[Register for the exam](https://anthropic.skilljar.com/claude-certified-architect-foundations-access-request)**
