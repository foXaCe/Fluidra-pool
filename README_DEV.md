# Development Guide

## Setup Development Environment

### 1. Install Python dependencies

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Linux/Mac
# or
.venv\Scripts\activate  # On Windows

# Install development dependencies
pip install -r requirements-dev.txt
```

### 2. Install pre-commit hooks

```bash
pre-commit install
```

This will automatically run code quality checks before each commit.

### 3. Run code quality checks manually

```bash
# Check code with Ruff
ruff check custom_components/fluidra_pool/

# Auto-fix issues
ruff check custom_components/fluidra_pool/ --fix

# Format code
ruff format custom_components/fluidra_pool/

# Run all pre-commit hooks
pre-commit run --all-files
```

## Code Quality Standards

- **Linting**: Ruff is configured to enforce code quality
- **Formatting**: Code is automatically formatted with Ruff
- **Type hints**: Use modern Python type hints (dict, list instead of Dict, List)
- **Logging**: Only log critical errors, avoid debug/info/warning in production

## Pre-commit Hooks

The following checks run automatically before each commit:
- Ruff linting with auto-fix
- Ruff formatting
- Trailing whitespace removal
- End-of-file fixing
- YAML/JSON validation
- Large files check
- Merge conflict detection

## Configuration Files

- `pyproject.toml`: Ruff configuration and Python project metadata
- `.pre-commit-config.yaml`: Pre-commit hooks configuration
- `requirements-dev.txt`: Development dependencies
