# Preparing a public repository

An existing personal installation may contain private information in old commits,
screenshots and Git metadata even after its current files have been cleaned.
Build a clean source snapshot before creating the public repository:

```sh
python scripts/public_release.py --check
python scripts/public_release.py --audit-history
python scripts/public_release.py --output dist/stacksignal-public.zip
```

The export contains reviewed source directories, documentation, `AGENTS.md`, `CLAUDE.md`
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

- Review the proposed MIT license and copyright notice.
- Enable private vulnerability reporting, secret scanning and push protection where available.
- Require passing tests for pull requests to `main`.
- Use the repository description: **A local technology radar that matches open-source tools to your projects.**
- Suggested topics: `technology-radar`, `fastapi`, `sqlite`, `github`, `local-first`.

`scripts/make_archive.py` serves a different purpose: transferring a private checkout
with its Git history. Its archives, especially those made with `--include-data`, must
not be uploaded as public releases.
