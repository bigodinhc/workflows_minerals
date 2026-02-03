# Execution Scripts (Layer 3)

This folder contains deterministic Python scripts that do the actual work.

## Purpose
Scripts handle **execution** — API calls, data processing, file operations, database interactions.

## Principles
1. **Deterministic**: Same input = same output
2. **Reliable**: Proper error handling and logging
3. **Testable**: Can be tested independently
4. **Well-commented**: Clear documentation

## Conventions
- Use `.env` for environment variables and API tokens
- Store intermediate files in `.tmp/`
- Deliverables go to cloud services (Google Sheets, Slides, etc.)
- Include docstrings and inline comments

## Example Script Structure

```python
#!/usr/bin/env python3
"""
Script: script_name.py
Purpose: [What this script does]

Usage:
    python script_name.py [arguments]

Inputs:
    - [Input 1]
    
Outputs:
    - [Output 1]
"""

import os
from dotenv import load_dotenv

load_dotenv()

def main():
    # Implementation here
    pass

if __name__ == "__main__":
    main()
```

## Dependencies
Add any Python dependencies to `requirements.txt` in the root folder.
