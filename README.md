# vets-anni

Wynnvets **Annihilation** coordination stack — user dashboard, staff/organizer
drag-and-drop board, and the **fishbot** Discord bot (`/rsvp`). Deploys as
`anni.wynnvets.org` on the vets-deploy VPS.

One lean Python process: FastAPI + fishbot + background pollers.

- **Docs hub:** [`.claude/CLAUDE.md`](.claude/CLAUDE.md)
- **Spec:** [`.claude/spec.md`](.claude/spec.md)
- **Local dev / deploy:** [`.claude/deployment.md`](.claude/deployment.md)

```bash
python -m venv .venv
.venv/Scripts/pip install -e .[dev]   # uv is intentionally not required
.venv/Scripts/aerich init-db          # first time only
.venv/Scripts/python main.py          # http://127.0.0.1:8000
```
