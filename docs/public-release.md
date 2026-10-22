# Publishing StackSignal source

[Documentation](README.md) · [Current state](STATUS.md)

## Updating the existing public repository

Review normal changes and commit them to the existing history. A new history is not
required for each update. Check the source, documentation and reachable Git history:

```text
python scripts/public_release.py --check
python scripts/check_docs.py
python scripts/public_release.py --audit-history
gitleaks git --staged --redact
gitleaks git --log-opts="--all" --redact
```

Gitleaks is optional external tooling; the Python checks use the standard library.
Review the actual diff and screenshots as well. Commit and push only the reviewed
changes; private notes, credentials and runtime files stay outside Git.

## Public source snapshots

An existing personal installation may contain private information in old commits,
screenshots and Git metadata even after its current files have been cleaned.
For a source-only handoff or the first publication from private development, build a
clean source snapshot:

```sh
python scripts/public_release.py --check
python scripts/public_release.py --audit-history
python scripts/public_release.py --output dist/stacksignal-public.zip
```

The export contains reviewed source directories, both README languages, documentation, `AGENTS.md`, `CLAUDE.md`
and the portable `stacksignal.py` entry point. It
does not include Git history, Git author metadata, local data, configuration, archives,
agent results or a Git bundle. It includes current uncommitted source changes.
Output paths must be new; existing archives are never overwritten. A SHA-256 file
and a manifest of file hashes are written alongside the archive.

The scanner checks known local credential values, private-key headers and common
token formats. It reports paths and rule names without printing values. History
auditing examines objects reachable from local refs; it does not inspect remote-only
objects, reflogs or arbitrary binary image contents. Automated checks cannot identify
every secret or personal detail. Review all exported text and images before upload.

## First publication

These steps apply to an installation whose history has never been reviewed for publication.
For an existing public repository, review and commit normal changes; a new history is not
required for each release. See [STATUS.md](STATUS.md) for the current project checkpoint.

Extract the public ZIP into a **new directory outside the personal checkout**, review
the files, then run the backend and browser tests there. Start a new Git history:

```sh
git init -b main
git add .
git diff --cached --stat
git commit -m "Initial public release"
```

Choose a Git author name/email suitable for public commits before committing. Create
an empty GitHub repository, add its remote, and push only this new history. Keep the
personal repository and its backups private. Changing the existing repository's
visibility or pushing its old branches/tags would also expose its old history.

## GitHub settings

- Keep the MIT license and copyright notice in the published source.
- Enable private vulnerability reporting, secret scanning and push protection where available.
- Require passing tests for pull requests to `main`.
- Keep the repository description aligned with the README: **Local-first technology radar: match open-source tools, models and papers to project needs, test candidates, and keep evidence-backed decisions.**
- Topics: `technology-radar`, `developer-tools`, `local-first`, `python`, `fastapi`, `sqlite`, `research-tools`, `decision-support`.
- Link documentation from the README; add a website only when a separate site exists.

`scripts/make_archive.py` serves a different purpose: transferring a private checkout
with its Git history. Its archives, especially those made with `--include-data`, must
not be uploaded as public releases.
