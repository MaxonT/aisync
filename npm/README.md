# aisync (npm)

`aisync` is shared rules, a journal and file claims for every AI coding tool. The real program is the
Python package on PyPI, `aisync-cli`. This npm package is a launcher: it runs that exact release, so
`npx aisync-cli --version` prints the same version as `pip install aisync-cli`.

```bash
npx aisync-cli --version      # runs aisync-cli 0.2.7 through uvx (or pipx run)
npx aisync-cli doctor
```

Requirements: Node 18+, and either `uv` (https://docs.astral.sh/uv/) or `pipx`. Python 3.11+ and git are needed by
the Python release itself. Nothing is installed globally and no shell config is touched.

Full documentation: https://github.com/MaxonT/aisync
