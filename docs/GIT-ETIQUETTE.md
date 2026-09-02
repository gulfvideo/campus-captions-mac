# Git etiquette

How we write history in this repo, and why. Aimed at someone who has not used
Git much — there is nothing here you need a computer science degree for.

The short version: **a commit is a message to a stranger, and the stranger is
you in eight months.** Everything below follows from that.

---

## 1. The commit message has two halves

**The subject line — what it does.** Written as an instruction, present tense,
as if ordering the codebase to change. Finish the sentence *"If applied, this
commit will ___"*:

```
Remember the operator's audio input between launches
Stop blaming the microphone permission for every silent input
Keep captions flowing after a backend switch
```

Not `Fixed audio bug`, not `Fixes`, not `audio stuff`. Around 50 characters,
no full stop. If you need the word "and" twice, it is probably two commits.

**The body — why you did it.** This is the half almost everyone skips, and the
half that is worth something later.

The diff already shows *what* changed; anyone can read it. What the diff can
never show is what you knew at the time. Compare:

```
Fix whisper
```

against the real one from this repo:

> **Stop Whisper putting a repetition loop on the wall**
>
> The srt from this morning has a line reading "you're not sure if" twenty
> times over […] `temperature` is a ladder, not a value. The library decodes
> at the first entry and, if the result trips compression_ratio_threshold —
> its own comment for that branch is "too repetitive" — retries at the next
> one. Passing a scalar 0.0 makes `temperatures` a one-item list […]

Someone hitting that line with `git blame` in a year learns the whole story:
what broke, why the old code looked reasonable, and what would break again if
they "simplified" it back. That is the entire point.

Worth putting in a body:

- the symptom, and how you noticed it
- the cause, once you found it
- anything you tried that did **not** work, or chose not to do, and why
- how you checked that it actually works

A one-line typo fix does not need three paragraphs. Use judgement — but the
default should be to explain yourself, not to stay quiet.

The template in `.gitmessage` puts this in front of you every time. Turn it on:

```bash
git config commit.template .gitmessage
```

That is per-clone, so each person runs it once.

---

## 2. Small commits

One idea per commit. A commit that fixes a bug *and* renames a variable *and*
adds a feature cannot be reviewed, cannot be explained in one sentence, and
cannot be reverted without collateral damage.

The test: if the subject line needs "and" twice, split it.

Small does not mean trivial. `Split configuration into a Settings page` touched
a dozen files — but it was one idea, and it says so.

---

## 3. Every commit should work

This is the part with real teeth. The goal is that **any commit you could
`git checkout` is one that runs**, because:

- someone bisecting a bug lands on arbitrary commits and needs them to build
- a reviewer can merge half your branch if the rest needs more work
- you can safely roll back to any point when something goes wrong live

So: run the tests before you commit.

```bash
.venv/bin/python tools/smoke_test.py "$(pwd)"
```

Eleven seconds, 87 checks. There is also a hook that refuses a push if the
tests fail — install it once:

```bash
ln -sf ../../tools/hooks/pre-push .git/hooks/pre-push
```

Bypass it deliberately when you mean to (`git push --no-verify`), for instance
to back up a half-finished branch.

### What CI does and does not cover

> **Not enabled yet.** The workflow is written and sitting at
> `.github/workflows/ci.yml`, but pushing anything under `.github/workflows/`
> needs a GitHub token carrying the `workflow` scope, which ours does not have
> yet. To finish it: `gh auth refresh -h github.com -s workflow`, then
> `gh auth setup-git` (git sends a separate keychain copy of the token
> otherwise), then commit and push that one file. Delete this note when it is
> running. Until then the pre-push hook above is what is actually protecting
> the branch.

Every push and pull request runs `.github/workflows/ci.yml`, so each commit
gets a green tick or a red cross on GitHub. **Be careful how much you trust
it.** It runs the relay's smoke test on Linux. It cannot test:

- the packaged `.app`, code signing, or Gatekeeper
- any audio device, or the microphone permission
- Apple on-device speech, or Whisper
- the menu bar app
- anything a person has to look at

Most of the bugs found in this project so far were in exactly those areas. A
green tick here means "the relay's contract still holds", not "this works".
For anything touching audio, the Mac app, or the viewer pages, run it on a Mac
and look at it.

---

## 4. Branches and pull requests

`main` should always be releasable. Work on a branch named for what it does:

```bash
git switch -c fix-caption-alignment      # start
git add -p                               # stage in pieces, review as you go
git commit                               # template opens; write both halves
git push -u origin fix-caption-alignment
gh pr create --fill                      # or open it on github.com
```

`git add -p` walks you through your changes hunk by hunk and asks about each
one. It is the single easiest way to stop accidentally committing a stray
debug line — and to notice when what you have is really two commits.

Before asking anyone to review: read your own diff.

```bash
git diff main...HEAD
```

You will find the leftover `print()` yourself, which is nicer than being told.

---

## 5. You will break something

The video this is drawn from ends on it, and it is worth repeating. Its author
broke his own build by forgetting to `git add` a new file — tests passed
locally, CI failed, because the file existed on his machine and nowhere else.

That specific mistake is why CI byte-compiles everything from a clean checkout:
a file you never added is a file CI does not have, and it says so immediately.

When you break `main`, fix it forward with a normal commit. Do not rewrite
published history to hide it — a broken commit with an honest fix after it is
a much better record than a tidy lie, and force-pushing shared branches breaks
everyone else's clone.

---

## 6. Things worth knowing when you are new

```bash
git status                    # what have I changed?
git diff                      # ...and what exactly?
git log --oneline -20         # recent history, one line each
git log -p path/to/file       # every change to one file, with reasons
git blame path/to/file        # who last touched each line, and in which commit
git show <hash>               # one commit in full
git switch -c name            # new branch
git restore path/to/file      # throw away my changes to this file
git commit --amend            # fix the message of the commit I just made
```

`git blame` followed by `git show` on the hash it prints is the loop that turns
a good commit body into an answer. It is why the bodies are worth writing.

**Careful with:** `git reset --hard`, `git checkout .`, `git clean -fd`. Those
destroy uncommitted work with no undo. Run `git status` first, every time. If
you want a safety net, `git stash -u` parks everything instead of deleting it.

---

## Why bother

You are doing this for the person who maintains this after you, for Karson
reviewing your branch, and mostly for yourself in eight months when a caption
wall misbehaves ten minutes before an event and the only thing standing between
you and guesswork is what you wrote down at the time.
