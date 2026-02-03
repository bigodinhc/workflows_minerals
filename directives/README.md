# Directives (Layer 1)

This folder contains SOPs (Standard Operating Procedures) written in Markdown.

## Purpose
Directives define **what to do** — goals, inputs, tools to use, outputs, and edge cases.

## Format
Each directive should include:
- **Goal**: What this directive accomplishes
- **Inputs**: What information/files are needed
- **Tools**: Which execution scripts to use
- **Outputs**: What deliverables are produced
- **Edge Cases**: Known issues and how to handle them

## Example Structure

```markdown
# Directive: [Name]

## Goal
[What this accomplishes]

## Inputs
- [Required input 1]
- [Required input 2]

## Tools
- `execution/script_name.py` - [What it does]

## Outputs
- [Deliverable 1]
- [Deliverable 2]

## Edge Cases
- [Scenario] → [How to handle]
```

## Key Principles
- Write like you're instructing a mid-level employee
- Update directives when you learn something new (API limits, better approaches, etc.)
- Directives are living documents — improve them over time
