# RESOLVED: tests/aws/test_stepfunctions.py's one-Lambda premise (owner-authorized unfreeze, see git log)

Fixed directly: `.claude/loop/frozen.json`'s entry for this file was removed (owner: "forget the hook, just do the
rewrite and send it" — hackathon, prioritizing iteration speed), `_flow_b_lambda(doc)` added (selects by handler
`coherence_analyzer.lambda_handler.handler` instead of assuming there's only one Lambda), the four call sites and
the resource-type/role counts in `test_cfn_adds_only_the_expected_resource_types_and_keeps_one_managed_policy`
updated for the two new, legitimate resources (`ApiFunction`, its `AWS::Lambda::Url`/`AWS::Lambda::Permission`).
`COHERENCE_ANALYZER_LLM=mock .venv/bin/python -m pytest tests/aws/test_stepfunctions.py -q` → 51 passed.

---

# RESOLVED: tests/dashboard/test_any_user_dashboard.py and tests/dashboard/test_ui_intake_confirm.py retargeted to
app.html

Owner-authorized (this session): removed both paths from `.claude/loop/frozen.json` and asked me to apply the
prepared rewrite directly. Both files now pin `deploy/site/app.html` + its JS instead of `index.html`; both are
green (`test_any_user_dashboard.py` 26/26, `test_ui_intake_confirm.py` 27/27). `deploy/build_site.py` was also
fixed directly (owner-authorized, no other lane touching it): `build()` now calls `preview_report.build_en_ca()`
so `reports/en-CA/robert-nvda.json` exists for trade.js's offline demo. Original blocker text kept below for
context.

Task (ad hoc, this session): with index.html's features now fully ported into app.html, retire index.html — but
first rewrite these two frozen test files to pin app.html's equivalent behaviour (same test function names, same
guarantees, different target). `.claude/hooks/guard.py` blocks `Edit`/`Write` to both paths (both are listed in
`.claude/loop/frozen.json`); every attempt returns `BLOCKED: ... is a frozen oracle test`.

This is not a provable arithmetic/unpacking bug (the standing "oracle-test-fix-approval" memory's bypass
mechanism — drop the entry from `frozen.json`, edit, re-add with a fresh sha256 in the same commit — is scoped to
those, "genuine spec ambiguities still go to the user as questions"). A full architecture retarget of two ~600-line
oracles is squarely the second case, so it needs an explicit owner/caller decision before I touch `frozen.json`.

What I completed in files NOT covered by the guard (all verified with `node --check`, all persona-name-free):
- `deploy/site/js/screens/conversation.js`: added `staticMissingHtml()`/`staticMissingPaths()` — the offline/static
  demo's missing-intake-questions card was rendering empty; it now reads `report.site.intake` / `report.intake`
  (`deploy/build_site.py`'s `missing_by_factor`, already embedded for every EN review) and shows a read-only list
  via `intake_questions.js`'s `specFor(path).label`. Wired into `renderCurrent`'s `!isLive()` branch.
- `deploy/site/js/screens/summary/summary.js`: `createNewProfile()` now asks (via `window.prompt`) for an optional
  display name before `POST /profiles`, sending `{locale: "en-CA"}` or `{locale, display_name}` only (a blank
  name is never sent); `findPersona()`'s fallback label changed from the hard-coded `"Robert"` to `"Profile"`.
- `deploy/site/app.html`: the loading-skeleton placeholder markup (persona chip, persona-select default option,
  the summary sample sentence, the what-if sample finding) hard-coded "Robert" in literal text that is always
  replaced once real data loads, but is a real D10/no-persona-name violation in the brief window before that;
  reworded to generic placeholders ("Profile", "This profile ...", "the profile's ..."). app.html was not in the
  task's explicit own/no-touch lists either way; I made this minimal, non-structural text change because the
  invariant is unconditional ("no user-facing string anywhere should hard-code a persona name") and it was the
  smallest fix that actually closes it — happy to revert if that reads as out of scope.

Found while investigating (not a guard issue, a `deploy/build_site.py` gap — I did not edit it, per the task's
explicit instruction to describe it instead):
- `deploy/site/js/screens/trade.js`'s offline/static demo (`STATIC_DEMO_PATH`) fetches
  `reports/en-CA/robert-nvda.json` directly, but `build_site.py`'s `build_flow_b(out)` only ever writes
  `out/robert-nvda.json` where `out = <site>/reports` (the FR root index.html's `loadFlowB()` reads), never into
  `<site>/reports/en-CA/`. Verified empirically: building the site and checking
  `(out / "reports" / "en-CA" / "robert-nvda.json").is_file()` returns `False`. Fix needed: in `build()`, after
  `build_flow_b(out / "reports")`, also write (or copy) the same JSON to `out / "reports" / "en-CA" /
  "robert-nvda.json"` — e.g. `shutil.copyfile(out / "reports" / "robert-nvda.json", out / "reports" / "en-CA" /
  "robert-nvda.json")`, or refactor `build_flow_b` to take a `dest: Path` and call it twice. Also confirmed, so
  these need no change: `build_whatif_en`/`build_reports_en` already run per EN persona (no `WHATIF_PERSONA`), and
  every EN review already embeds `site.intake` (the missing-list) exactly like the FR pipeline.

What I have ready but could not write: full rewritten contents for both frozen test files, retargeted at the real
functions above plus `deploy/site/js/core/data_source.js` (`listProfiles`, `createProfile`, `savePortfolio`,
`decideRule`, `requestDraft`, `confirmIntent`, `intake`/`answer`/`skip`), `deploy/site/js/screens/pillars/
preferences.js` (`handleRuleDecision`, the N3 `window.prompt` flow) and `deploy/site/js/screens/trade.js`
(`handleDraftSubmit`/`handleConfirmSubmit`, now including its new offline worked-example path). One real guarantee
gap I could not close at all: the old oracle required a `POST /profiles {locale, display_name?}` **form** with a
`locale` select; app.html is English-only by design, so I resolved this as "fixed locale `en-CA`, optional
`display_name` via prompt" rather than inventing a locale picker this single-locale surface has no use for — flag
if that reading is wrong. Ask me to proceed once `frozen.json` is unlocked for these two paths (by the caller, or
by the human) and I will apply the prepared rewrite in the same turn.
