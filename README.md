# Anthropic Certified Architect Lab

A story-driven study lab for the **Claude Certified Architect – Foundations (CCA-F)** exam. Every domain is grounded in a real production failure from a fictional AI-native company. Every exercise reproduces that failure and builds the fix.

---

## The Story

The lab follows **Resolve** — a B2B SaaS platform that automates customer support using Claude agents. As Resolve grows from 200 to 1,200 enterprise customers, five production incidents force the engineering team to master exactly what the CCA-F exam tests.

| Incident | Root Cause | Exam Domain |
|---|---|---|
| $11,400 in API calls overnight — one ticket, infinite loop | Loop termination based on model language, not `stop_reason` | Agentic Architecture (27%) |
| New engineer blocked for 2 days — setup lived in one person's head | No CLAUDE.md, no custom commands, no CI validation | Claude Code (20%) |
| 800 tickets closed with a generic reply | Agent *asked* for JSON; not *required* via `tool_use` | Prompt Engineering (20%) |
| 43 billing disputes told "everything is fine" during a CRM outage | Tool returned `{}` instead of a typed access failure | Tool Design & MCP (18%) |
| Agent contradicted commitments made 20 messages ago | Critical context drifted out of effective attention window | Context Management (15%) |

→ **[Read the full story](./STORY.md)**

---

## The Exam

| | |
|---|---|
| **Certification** | Claude Certified Architect – Foundations (CCA-F) |
| **Issuer** | Anthropic |
| **Format** | 60 scenario-based multiple-choice questions |
| **Duration** | 120 minutes |
| **Passing score** | 720 / 1000 |
| **Cost** | Free for Claude Partner Network members |
| **Launched** | March 12, 2026 |

→ **[Full exam roadmap with domain breakdown and study plan](./ROADMAP.md)**

→ **[Register](https://anthropic.skilljar.com/claude-certified-architect-foundations-access-request)**

---

## Lab Structure

```
.
├── STORY.md              # The Resolve narrative — read this first
├── ROADMAP.md            # Exam domains, 8-week study plan, resources
└── labs/
    ├── week-1-foundations/               # API basics, stop_reason, tool_use vs. JSON
    ├── week-2-agentic-architecture-part1/ # Loop termination, iteration budget, task decomposition
    ├── week-3-agentic-architecture-part2/ # Multi-agent, coordinator/subagent, hooks
    ├── week-4-claude-code/               # CLAUDE.md hierarchy, custom commands, non-interactive mode
    ├── week-5-prompt-engineering/        # Structured output, schema design, retry loops
    ├── week-6-tool-design-mcp/           # Tool descriptions, three-response-shape, MCP servers
    ├── week-7-context-management/        # Pinning, summarisation, error propagation
    └── week-8-review/                    # Scenario walkthroughs, anti-pattern drill, practice exam
```

Each lab folder contains a `README.md` with exercises, step-by-step instructions, and exam connections. Exercises are implemented in both **Python** and **TypeScript**.

---

## 8-Week Study Plan

| Week | Topic | Exam Domain |
|---|---|---|
| 1 | API foundations — `stop_reason`, `tool_use`, structured output basics | All |
| 2 | Agentic loops, iteration budget, task decomposition | D1 (27%) |
| 3 | Multi-agent orchestration, coordinator/subagent, hooks | D1 (27%) |
| 4 | CLAUDE.md hierarchy, custom commands, CI integration | D2 (20%) |
| 5 | Prompt engineering, schema design, validation-retry loop | D3 (20%) |
| 6 | Tool design, three-response-shape pattern, MCP servers | D4 (18%) |
| 7 | Context pinning, summarisation risks, error propagation | D5 (15%) |
| 8 | Scenario walkthroughs, anti-pattern drill, timed practice exam | All |

---

## Prerequisites

- 6+ months hands-on experience with the Claude API
- Python 3.11+ or Node.js 20+
- `pip install anthropic python-dotenv` / `npm install @anthropic-ai/sdk`
- An `ANTHROPIC_API_KEY` (set in a `.env` file at the repo root)
- Claude Code installed for Week 4 exercises

---

## Quick Start

```bash
git clone https://github.com/wb-platform-engineering-lab/anthropic-certified-architect-lab.git
cd anthropic-certified-architect-lab

# Add your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Start with the story
open STORY.md

# Then the Week 1 lab
open labs/week-1-foundations/README.md
```
