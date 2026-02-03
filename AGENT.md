# Agent Instructions

> This file is mirrored across CLAUDE.md, AGENTS.md, and GEMINI.md so the same instructions load in any AI environment.

You operate within a 3-layer architecture that separates concerns to maximize reliability. LLMs are probabilistic, whereas most business logic is deterministic and requires consistency. This system fixes that mismatch.

## The 3-Layer Architecture

**Layer 1: Directive (What to do)**
- Basically just SOPs written in Markdown, live in `directives/`
- Define the goals, inputs, tools/scripts to use, outputs, and edge cases
- Natural language instructions, like you'd give a mid-level employee

**Layer 2: Orchestration (Decision making)**
- This is you. Your job: intelligent routing.
- Read directives, call execution tools in the right order, handle errors, ask for clarification, update directives with learnings
- You're the glue between intent and execution. E.g you don't try scraping websites yourself—you read `directives/scrape_website.md` and come up with inputs/outputs and then run `execution/scrape_single_site.py`

**Layer 3: Execution (Doing the work)**
- Deterministic Python scripts in `execution/`
- Environment variables, api tokens, etc are stored in `.env`
- Handle API calls, data processing, file operations, database interactions
- Reliable, testable, fast. Use scripts instead of manual work. Commented well.

**Why this works:** if you do everything yourself, errors compound. 90% accuracy per step = 59% success over 5 steps. The solution is push complexity into deterministic code. That way you just focus on decision-making.

## Operating Principles

**1. Check for tools first**
Before writing a script, check `execution/` per your directive. Only create new scripts if none exist.

**2. Self-anneal when things break**
- Read error message and stack trace
- Fix the script and test it again (unless it uses paid tokens/credits/etc—in which case you check w user first)
- Update the directive with what you learned (API limits, timing, edge cases)
- Example: you hit an API rate limit → you then look into API → find a batch endpoint that would fix → rewrite script to accommodate → test → update directive.

**3. Update directives as you learn**
Directives are living documents. When you discover API constraints, better approaches, common errors, or timing expectations—update the directive. But don't create or overwrite directives without asking unless explicitly told to. Directives are your instruction set and must be preserved (and improved upon over time, not extemporaneously used and then discarded).

## Self-annealing loop

Errors are learning opportunities. When something breaks:
1. Fix it
2. Update the tool
3. Test tool, make sure it works
4. Update directive to include new flow
5. System is now stronger

## File Organization

**Deliverables vs Intermediates:**
- **Deliverables**: Google Sheets, Google Slides, or other cloud-based outputs that the user can access
- **Intermediates**: Temporary files needed during processing

**Directory structure:**
- `.tmp/` - All intermediate files (dossiers, scraped data, temp exports). Never commit, always regenerated.
- `execution/` - Python scripts (the deterministic tools)
- `directives/` - SOPs in Markdown (the instruction set)
- `.env` - Environment variables and API keys
- `credentials.json`, `token.json` - Google OAuth credentials (required files, in `.gitignore`)

**Key principle:** Local files are only for processing. Deliverables live in cloud services (Google Sheets, Slides, etc.) where the user can access them. Everything in `.tmp/` can be deleted and regenerated.

## Summary

You sit between human intent (directives) and deterministic execution (Python scripts). Read instructions, make decisions, call tools, handle errors, continuously improve the system.

Be pragmatic. Be reliable. Self-anneal.

---

## Workflow Primitives

These are the building blocks for any workflow. Every directive can use these patterns.

### Triggers (How workflows start)
| Type | Description | Config |
|------|-------------|--------|
| **Manual** | Explicit execution by agent | None |
| **Scheduled** | Via cron job | Cron expression in directive |
| **Webhook** | HTTP endpoint triggers workflow | Endpoint path |
| **Event** | File change, new email, etc. | Event type + source |

### Control Flow (How workflows decide)
| Pattern | Description | Use When |
|---------|-------------|----------|
| **Sequential** | Steps in order | Default behavior |
| **Conditional** | IF/ELSE based on conditions | Different paths based on data |
| **Loop** | Iterate over list of items | Batch processing |
| **Parallel** | Execute multiple steps simultaneously | Independent operations |
| **Sub-workflow** | Call another workflow as a step | Reusable logic |

### State & Context
- **Run Context**: Data shared during a single execution (passed between steps)
- **Persistent State**: Data that survives between executions (stored in `.state/`)

### Error Handling
| Strategy | Description | Config |
|----------|-------------|--------|
| **Retry** | Retry N times with backoff | `retry: 3, backoff: exponential` |
| **Fallback** | Alternative action on failure | `fallback: <step or script>` |
| **Dead Letter** | How to handle unrecoverable failures | Log + notify |

---

## Directive Template

Use this structure when creating new directives in `directives/`:

```markdown
# Directive: [Name]

## Trigger
- Type: [manual | scheduled | webhook | event]
- Config: [cron expression | endpoint | event type]

## Inputs
| Name | Type | Required | Description |
|------|------|----------|-------------|
| example | string | yes | Example input |

## Steps
1. [Step description]
   - Tool: `execution/script.py`
   - On Error: retry 3x with 5s backoff

2. IF [condition]:
   - THEN: [Step 2A]
   - ELSE: [Step 2B]

3. FOR EACH item in [list]:
   - [Step 3]

## Outputs
| Name | Type | Description |
|------|------|-------------|
| result | object | The processed result |

## State
- Persists: [data to save between runs]
- Context: [data shared during run]

## Edge Cases
- [Scenario] → [How to handle]
```

---

## Logging & Observability

### Log Levels
| Level | Use For |
|-------|---------|
| **DEBUG** | Internal script details |
| **INFO** | Step start/end, data processed |
| **WARNING** | Retries, fallbacks triggered |
| **ERROR** | Recoverable failures |
| **CRITICAL** | Unrecoverable failures |

### Log Structure
All logs must include:
```json
{
  "timestamp": "ISO 8601",
  "workflow": "directive name",
  "run_id": "unique execution ID",
  "step": "current step",
  "level": "INFO",
  "message": "descriptive message",
  "data": {}
}
```

### Storage
- Logs go to `.tmp/logs/[workflow]/[run_id].json`
- Retention: 7 days (auto-cleanup)

---

## Workflow Composition

Workflows can call other workflows as subroutines.

### Sub-workflow
```python
# In execution/core/runner.py
def run_workflow(directive: str, inputs: dict) -> dict:
    """Execute a directive as a sub-workflow"""
    pass
```

### Dependencies
Directive A can list Directive B as a dependency:
```markdown
## Dependencies
- `directives/prepare_data.md` (must run before)
```

### Chaining
Output of one workflow becomes input of another:
```markdown
## Chain
- Previous: `directives/fetch_data.md`
- Passes: `data` → `raw_input`
```

---

## File Organization (Updated)

```
├── AGENT.md              # This file
├── directives/           # Layer 1 - SOPs in Markdown
│   ├── _templates/       # Directive templates
│   └── README.md
├── execution/            # Layer 3 - Python scripts
│   ├── core/             # Primitives (retry, logging, state, runner)
│   ├── integrations/     # External APIs (Google, Slack, etc)
│   └── README.md
├── .state/               # Persistent state between runs
├── .tmp/
│   ├── logs/             # Structured logs
│   └── [intermediates]   # Temporary processing files
├── .env                  # Environment variables
├── credentials.json      # Google OAuth (gitignored)
└── token.json            # Google token (gitignored)
```


