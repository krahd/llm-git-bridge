# Agent instructions

This repository implements `llm-git-bridge`, a provider-agnostic bridge between LLM/agent clients and local Git repositories.

When modifying the project:

- preserve provider-neutral terminology in core code and protocol;
- do not introduce arbitrary remote shell execution; remember that locally configured commands can execute patched repository code and therefore are not an OS sandbox;
- do not add automatic push/merge behaviour without explicit policy and tests;
- local filesystem paths must not appear in the remote repository index;
- retain stale-SHA checks and safe branch-prefix validation;
- minimise mailbox/Drive round trips without racing ambiguous Drive writes; Google Drive permits duplicate filenames;
- keep the standard-library-only runtime unless a dependency has a clear benefit;
- run `python3 -m unittest discover -s tests -v` before committing.
