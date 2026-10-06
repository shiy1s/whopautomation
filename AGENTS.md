# Whop automation continuation

Read docs/HANDOFF_2026-10-06.md first, then older checkpoints for evidence/run IDs.
Preserve the GitHub Actions Phase Runner + n8n Cloud architecture. Never restart it.

- Keep Boxabl and Backyard Breaks untouched and Phase 13 paused.
- Do not fabricate media, account eligibility, audience demographics, or publication records.
- The user authorized a real post on October 6, 2026; eligibility, exact account identity,
  finished-media review and existing publishing safety gates still apply.
- Never activate the main n8n workflow or submit Content Rewards without explicit scope.
- Use bounded exact Google Drive files. Drive sources must never fall through to broad crawling.
- Tests use isolated fixtures; these do not prove production readiness.
- Commit meaningful fixes and continuation notes; never commit secrets or downloaded media.

Local regression commands (no live publishing):

```sh
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
git diff --check
```

Python tests require Pillow and NumPy. Rendering additionally needs ffmpeg/ffprobe.
Live dependencies are documented in each existing GitHub Actions workflow. Prefer
that runtime for real controlled phase executions; do not extract its secrets.
