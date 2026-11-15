# BLOCKED (P6.TRADEMSG, phase A): frozen tests that contradict D16

The owner approved updating tests that pin the old behaviour; the orchestrator should update these asserts.

- `tests/flows/test_any_user.py:503-504`: `rep["disclaimer"] == config.DISCLAIMER[locale]` and
  `rep["explanation"]["disclaimer"] == config.DISCLAIMER[locale]` for a profile made by `POST /profiles`.
  D16: such a profile (`meta.origin = "user"`) carries the text WITHOUT the fictional-profiles clause.
- `tests/flows/test_new_user_no_portfolio.py:484`: `rep["disclaimer"] == config.DISCLAIMER[locale]` for a
  `POST /profiles` user. Same D16 point.

Not contradicted if `config.DISCLAIMER[locale]` stays the seed text (tests/flows/test_cli_disclaimer.py:74-75 pin that);
the new tests (tests/flows/test_trade_statements_and_disclaimers.py) assume it does and find the user text through
any `config.DISCLAIMER*` attribute.
