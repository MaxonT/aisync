# Contributing to aisync

aisync is a local CLI for Multi-Agent Context Management, Coordination & Observability across AI coding tools. Issues, questions and pull requests are welcome.

## Before you start

- Open an issue first for anything bigger than a small fix, so we can agree on the shape.
- The runtime stays standard-library only. Please do not add runtime dependencies. `pytest` and `mcp` are test-only.
- Python 3.11 or newer.

## Run the tests

```sh
python3 -m pip install pytest "mcp>=2.3,<3"
python3 -m pytest -q tests
```

`python3 -m unittest discover -s tests` also works. The suite includes building the wheel, installing it into a fresh virtualenv and running `aisync demo`, so it takes a while.

## Before you push

- CI runs the tests on Linux and macOS and also runs `python tools/privacy_scan.py --history`. Run the privacy scan locally too. A change that adds a personal path, an email address or a token will fail it.
- Keep a change small and say in the pull request what it changes and how you checked it.
- If an AI tool helped write the change, say so in the pull request. That is fine; being clear about it helps review.

## Reporting a bug

Use the bug report template. Include the output of `aisync --version` and, if you can, the command that failed.
