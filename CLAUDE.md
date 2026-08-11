# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This repository is currently empty of source code — no Python modules, tests, or build
configuration exist yet. `README.md` is present but blank. The only clue to intended direction
is the `.venv` virtualenv, which has `requests` and `beautifulsoup4` installed, suggesting this
project is meant to become a web-scraping / HTTP-fetching tool in Python.

## Environment

A Python 3.11 virtualenv lives at `.venv/` (gitignored). Activate it before running any Python:

```bash
source .venv/bin/activate
```

Installed dependencies: `requests`, `beautifulsoup4` (with `soupsieve`, `certifi`, `idna`,
`charset-normalizer`, `urllib3` as transitive deps).

There is no `requirements.txt` or `pyproject.toml` yet — if you add dependencies, create one of
these and keep it in sync with what's installed in `.venv`.

## Notes for future work

Since there is no existing code, architecture, or conventions to follow, use standard Python
project structure and idioms unless the user directs otherwise. Update this file once real
structure (source layout, entry points, test runner, etc.) exists.
