# CLAUDE.md - Project Guidelines for AI Assistants

## Project Overview
Fluidra Pool - Custom Home Assistant integration for Fluidra pool equipment (pumps, heat pumps, chlorinators, lights).

## Tech Stack
- **Language**: Python 3.11+
- **Framework**: Home Assistant Custom Component
- **Linting**: Ruff
- **Testing**: pytest

## Key Directories
- `custom_components/fluidra_pool/` - Main integration code
- `custom_components/fluidra_pool/api.py` - Fluidra API client
- `custom_components/fluidra_pool/coordinator.py` - Data update coordinator
- `custom_components/fluidra_pool/device_registry.py` - Device identification and configuration

## Coding Standards
- Follow Home Assistant development guidelines
- Use type hints for all functions
- Use constants from `const.py` instead of magic numbers
- Entity classes should inherit from `CoordinatorEntity`

## Commits
- Never add "Co-Authored-By" in commit messages
- Use conventional commit messages (feat:, fix:, chore:, refactor:, docs:, test:)
- Messages in English, concise and descriptive

## Autonomy
- Execute tasks without asking for intermediate validation
- Make decisions and document them
- Only ask questions if a decision is truly blocking and irreversible

## Commands
```bash
# Linting
ruff check custom_components/fluidra_pool/
ruff check --fix custom_components/fluidra_pool/

# Format
ruff format custom_components/fluidra_pool/

# Deploy to HA dev server
scp -r custom_components/fluidra_pool/ ha-dev:/config/custom_components/

# Restart HA
ssh ha-dev 'ha core restart'
```

## Device Support
- Variable speed pumps (VS*, VT*, NCC*)
- Heat pumps (LG, Z550iQ+)
- Chlorinators (DM*)
- Lights (LT*)
- Sensors (pH, ORP, temperature)
