# BLOCKED (P6.PROVISIONAL, phase A): frozen tests that pin the pre-D11 rule

docs/spec-decisions.md D11 (owner-approved 2026-11-15, point 5) says an unapproved, gated `max_theme_weight` rule with a
numeric `max_pct` is promoted into `limits.max_theme_weight` at once as a provisional limit. These frozen tests pin the old
rule ("unapproved parsed rules never reach limits") with a numeric unapproved theme rule, so they contradict D11. The owner
approved updating them; the orchestrator does it after phase A. Phase A did not touch them.

- tests/profile/test_limits_no_llm_inputs.py
  - test_llm_fields_do_not_change_limits (perturbation has an unapproved tech_heavy max_pct 50)
  - test_llm_fields_do_not_change_limits_without_rederive (same perturbation)
  - test_unapproved_parsed_rules_do_not_appear_in_limits (asserts no `tech_heavy`, no max_theme_weight)
- tests/profile/test_intake.py
  - test_unapproved_parsed_rules_are_ignored_by_build_limits (unapproved THEME_RULE, max_pct 50; also `trace == base_trace`)
  - test_only_the_approved_rule_of_a_mixed_batch_is_promoted (`not lim.get("max_theme_weight")` for an unapproved numeric rule)
- tests/profile/test_rules.py
  - test_unapproved_parsed_rules_are_not_promoted (unapproved max_pct 50 rule, `not lim.get("max_theme_weight")`)

Compatible with D11 and left as is: unapproved rules WITHOUT a number (exclude_activity, theme rule with no max_pct, theme-less
rules), approved rules, S4 reminders.

New oracle: tests/profile/test_provisional_limits.py.
