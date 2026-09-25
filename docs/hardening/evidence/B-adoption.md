# B-adoption — protected-file change procedure and biometric-track adoption

Date: 20 September 2026. Scope: governance only. No engine, SDK, model, policy,
threshold or test file was changed. No protected file was changed. Nothing was
deployed and the VPS was not contacted.

This closes the blocker raised in [B1.md](B1.md) §7 open question 1. The reviewer
chose option (a): adopt the biometric track, write a named change procedure and
re-baseline deliberately.

## 1. Files added or changed

| File | Action | sha256 |
|---|---|---|
| `HARDENING_PLAN.md` | changed | `22b6119f6c46d0f5815c5bbee5743b5ece28be717cedd1f52883098f51ed0abf` |
| `docs/hardening/BIOMETRIC_TRACK.md` | changed | `7f92b71f9870a883da47a090a7902989a28b8dd877d79da4c1a452454f2b960b` |
| `tools/hardening_verify_baseline.py` | added | `9d1e06880966fc810c4ca537832cd6236d401c8bba485bc06bac72b27baca9ab` |
| `docs/hardening/evidence/baseline/source-sha256.v2.json` | added | `7e70bc88919248023e25a95009e3b0ca56f062b02bcdc56ef04286fd65dfdec6` |
| `docs/hardening/evidence/baseline/source-sha256.json` | **untouched** | `dde3dafd536ae2885159dae1aecf5b878ce52c81a96452ff8abd58256c6d81c6` |

Neither plan document is a protected file; grepping the manifest for
`HARDENING_PLAN` or `docs/hardening` returns nothing. The frozen policy path is
untouched: no file under `engine/app/` or `packages/face-sdk/src/` was opened for
writing in this phase, and the hash check in §4 covers all of them.

## 2. What changed in the plan

`HARDENING_PLAN.md`:

- Header now states the biometric track is ADOPTED as of 20 September 2026.
- New section **Biometric track B1-B6** — a real status table using the same
  statuses as the milestone table. B1 is recorded COMPLETE with its evidence link
  and its 4-pass/5-fail scoreboard named as the fixed comparison for B2.
- New section **Protected-file change procedure** — see §3.
- **Deferred work: UX and models** no longer defers head-sequence tuning. That
  work moved to B2, explicitly as a new development-only policy version and never
  as an in-place edit of `pad-sequence-v2-slow`.

`docs/hardening/BIOMETRIC_TRACK.md`: status `proposed` to `ACTIVE`, with the
adoption date, the governing procedure, and a restatement that unfreezing local
source is not deploy authority.

## 3. The change procedure, in short

- **Which files may change**: only a file named by a row in the procedure's
  table, and only in the way that row allows. Five rows are written for the
  B2/B3 work (`head_sequence.py`, `challenge.py`, `liveness.py`, `main.py`, and
  the three SDK files). **All five are marked `pending` and authorize nothing.**
  No protected file may be edited against a pending row.
- **Never changeable by any milestone**: model files and hashes, preprocessing,
  match 0.55, heuristic liveness 0.50, the PAD decision rule, the all-frames-live
  rule, 12 frames at 1400 ms, target 0.20, the 0.15 stillness limit as v2-slow
  applies it, and the FaceScan wire format. The encoder golden test passes
  unedited or the change is wrong.
- **Who approves**: the user approves a row before the edit; the judge accepts or
  rejects the result. An agent proposes a row and stops; it never adds or widens
  one itself.
- **Recording**: every accepted change writes a new manifest; the phase evidence
  lists each moved hash, both manifest names, and the row the change rests on.
- **No deploy authority**: local work and isolated staging only. The VPS stays
  untouched, live capture UX, timing, policy and thresholds stay as they are, and
  M8 still governs every live change and is not shortened by any of this.

## 4. The re-baseline

`docs/hardening/evidence/baseline/source-sha256.json` is retained byte-for-byte
as evidence of the M0-M6 freeze; its mtime is still 19 September 14:24. The new
ACTIVE manifest is `source-sha256.v2.json`, written from the current contents of
the same 100 paths:

    wrote source-sha256.v2.json with 100 paths; 0 differ from source-sha256.json

**Zero of the 100 hashes moved.** The re-baseline is a deliberate act of
governance, not a cover for drift: it records that at the moment the freeze
gained a change path, nothing had changed. The two files are equal as data (same
100 keys, same 100 hashes, verified by loading both with `json.load` and
comparing); they differ only in JSON key ordering, because the new writer sorts
ASCII-wise so `Caddyfile` precedes `apps/`.

Verification after the re-baseline:

    manifest source-sha256.v2.json: 100 protected, 0 mismatched, 0 missing

`tools/hardening_verify_baseline.py` replaces the ad-hoc hash command each
milestone re-typed. It verifies by default and never writes; `--rebaseline NAME`
refuses to overwrite an existing file, so a baseline cannot be silently
rewritten. Adding it is slightly more than the reviewer asked for; the
justification is that "every change is re-baselined and recorded" is not a
procedure unless the step is runnable and refuses to clobber history. It is 73
lines, nothing imports it, and it deletes cleanly if unwanted.

## 5. Commands run, and results

    .venv-pad\Scripts\python.exe tools\hardening_verify_baseline.py
        --manifest docs/hardening/evidence/baseline/source-sha256.json
        --rebaseline source-sha256.v2.json
    -> wrote ... 100 paths; 0 differ from source-sha256.json

    .venv-pad\Scripts\python.exe tools\hardening_verify_baseline.py
    -> manifest source-sha256.v2.json: 100 protected, 0 mismatched, 0 missing

    .venv-pad\Scripts\python.exe -m ruff check tools\hardening_verify_baseline.py
    -> All checks passed!

    .venv-pad\Scripts\python.exe -m ruff format --check tools\hardening_verify_baseline.py
    -> 1 file already formatted   (it was reformatted once, then re-checked)

    .venv-pad\Scripts\python.exe -m pytest tests-hardening -q
    -> 672 passed, 100 skipped, 1 warning in 101.20s

    cd engine && ..\.venv-pad\Scripts\python.exe -m pytest tests\test_app_encoder.py -q
    -> 4 passed

    node --test apps\tests\face-guide.test.js
    -> 56 tests, 55 pass, 1 fail  (unchanged; see §7)

The one warning is the long-standing unsuppressed Authlib HTTPX deprecation.

### Test count: +16 that are not mine

B1 recorded `tests-hardening` at 656 passed / 100 skipped. It now reads
**672 passed / 100 skipped**, 772 collected. The extra 16 are
`tests-hardening/test_trusted_proxy.py`, created at **17:41:02 today** — during
this phase, but **not by me**. I added no test in this phase and did not open
that file for writing. Its docstring describes an M4 follow-up closing a
`Panel.source()` defect, and four files under
`packages/face-auth/src/facetech_auth/` (`http.py`, `limits.py`, `panel.py`,
`serve.py`) carry mtimes in the same window.

`packages/face-auth` is not in the protected manifest at all, which is why the
hash check still reads 100 of 100 unchanged. I have not reviewed that work and I
make no claim about it. It is flagged because a test count that rose by 16 with
no corresponding entry in this report would otherwise read as mine.

No count went down. No test was removed, edited or skipped by this phase.

## 6. Correction carried from B1

The reviewer corrected the peak-to-peak range for the four `not_frontal_and_still`
rejections to 0.1649-0.1909 (scan 14 is 0.1909). **B1.md already states
0.1649-0.1909** at line 115, and its per-trace table at line 99 already shows
0.1909 for scan 14. The wrong figure, 0.1649-0.1810, appeared only in the summary
printed to chat, not in any file. Grepping `docs/` for `0.1649` returns those two
correct lines and nothing else. No file needed a fix; the misstatement is
corrected here for the record.

## 7. The stale `?v=ui-v6` test — the freeze is the only reason

Asked for explicitly, so, plainly: **the freeze is the only reason it is still
failing.**

`apps/tests/face-guide.test.js:614` asserts the guided page's script tags are
`./evaluation-form.js` and `./page.js`. The page actually serves them as
`./evaluation-form.js?v=ui-v6` and `./page.js?v=ui-v6`. The cache-buster is
correct and intended; `ui-v6` is the live UI version in the plan's own baseline
table. The test's expectation is stale, the page is right, and the fix is one
line in the test.

Both files are protected:

    apps/integration-demo/index.html  88a7c495...
    apps/tests/face-guide.test.js     421598bd...

so there is no substantive blocker, only the freeze. I have **not** fixed it,
because the procedure written in this same phase says an agent proposes a row and
stops. Fixing it under my own new procedure without an approved row would be
exactly the self-authorization the procedure exists to prevent.

It is a one-line change to the test's `assert.deepEqual` expectation. If a row is
approved for `apps/tests/face-guide.test.js`, the fix lands, the baseline
re-bases to `source-sha256.v3.json`, and that suite goes 55/1 to 56/0.

Timing note: `source-sha256.v2.json` now pins the stale test as the active
baseline. That is harmless — it is a failing assertion, not a behaviour — but it
does mean the fix must come through the procedure rather than around it.

## 8. What I could not check — unverified

- The 100 skipped `tests-hardening` cases are the PostgreSQL/Keycloak-gated ones.
  The owned loopback stack is still stopped, as M4 left it, and nothing in this
  phase touches those paths. **Unverified** for this phase.
- The 16 new `test_trusted_proxy.py` cases and the four changed
  `packages/face-auth` source files are **unverified** by me. Not my work, not
  reviewed here.
- `tests-contract`, `tests-panel`, `mock-gateway/tests` and the full node suite
  were not re-run in this phase. This phase changed two Markdown files and added
  one standalone tool that nothing imports, so there is no mechanism by which
  their counts could move — but that is reasoning, not a run. **Unverified.**
- Nothing in this phase measured anything biometric. No claim is made here about
  recognition accuracy or spoof resistance.

## 9. Open questions

1. **Approve the five pending rows?** B2 and B3 cannot start until at least the
   `head_sequence.py` row is approved. They can be approved individually or as a
   block; each row names exactly what it permits.
2. **Approve a row for `apps/tests/face-guide.test.js`** so the stale `?v=ui-v6`
   assertion can be fixed? (§7)
3. Phase 3c's coarse retry hint still needs a **gateway contract change**, which
   the reviewer says needs separate approval. Not assumed, not started.
4. Phase 5's frames still need **separate explicit approval**. Not assumed, not
   requested here.
5. Should the four changed `packages/face-auth` files and their 16 new tests be
   reviewed by me, or do they belong to another agent's phase?

## 10. Next action

Stop. Do not start Phase 2 or Phase 3. Await the judge's review of this adoption
and the user's decision on the pending rows.
