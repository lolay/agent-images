Every cloud session's `make doctor` failed on a missing `packer`, which only the android Packer template's lint needs, so agents learned to carry on past a failing doctor. Now the session doctor checks exactly what the session gate runs, the gate is the standard `make pre-commit`, one `MODE=android` switch adds Packer to doctor, `cloud-lint` and `pre-commit` together, and the cloud PR check runs the triage profile so a broken `triage.yaml` can't land.

## Review guide

Review tier: [check], because the session gate and the cloud PR check (`gate`'s inputs) change shape.
Blast radius: cloud sessions' `make doctor`/`pre-commit`, and `cloud-build.yml`'s `check` job; macOS `make ci` is unchanged. A revert is safe.
Review time: ~12 min

1. [check] [`Makefile`](https://github.com/lolay/agent-images/compare/main...claude/make-doctor-triage-config-q65n8z#diff-76ed074a9305c04054cdebb9e9aad2d818052b07091de1f20cad0bbac34ffb52): session `CI_GATE`, `cloud-lint`'s `MODE=android` steps and guard, `cloud-ci` forcing android, `doctor`'s profile pick
2. [check] [`.github/workflows/cloud-build.yml`](https://github.com/lolay/agent-images/compare/main...claude/make-doctor-triage-config-q65n8z#diff-64990e1881d393d21b12b347f88b51be46ae77ca42e9db2bc8d2b230c57b3c06): triage-action step, `MODE=android`, `triage.yaml` in the path filter
3. [check] [`tests/unit/test_session_gate.py`](https://github.com/lolay/agent-images/compare/main...claude/make-doctor-triage-config-q65n8z#diff-b97a3a7040fb30bd58edd1604b4a3e7b6fed3678951e16aff09d369a4ab5d5f9): new, `make -n` on the gate with and without `AGENT_TYPE`
4. [skim] [`triage.yaml`](https://github.com/lolay/agent-images/compare/main...claude/make-doctor-triage-config-q65n8z#diff-6c888fa5630a58b1f40931c1cb9281fb4daa914750c9230a5d385f520781e741): `session` drops packer, new `session-android`
5. [skim] [`AGENTS.md`](https://github.com/lolay/agent-images/compare/main...claude/make-doctor-triage-config-q65n8z#diff-a54ff182c7e8acf56acfd6e4b9c3ff41e2c41a31c9b211b2deb9df75d9a478f9): "make doctor must pass"
6. [skim] [`Makefile.md`](https://github.com/lolay/agent-images/compare/main...claude/make-doctor-triage-config-q65n8z#diff-dfb4213ffedca557a5dcbc30215f3eb1897f7e1f58f20ed04d20ad48d14568a9)

## Validation

- `make doctor` in a cloud session: pass (4 ok)
- `make pre-commit` in a cloud session: pass, 425 tests (24 skipped)
- `make pre-commit MODE=android`, `make doctor MODE=android`: fail on packer not installed, as intended
- `test_session_gate`: each guard removed in turn (MODE check, optional android step, `cloud-ci` override, packer out of session lint) turns it red
- `actionlint` on `cloud-build.yml`: pass
- triage-action step: not run, first runs on this PR
- macOS `make ci`: not run (no macOS here); its `CI_GATE` is `lint test` as before, checked with `make -n` in the test

## Rollback

Revert the squash commit. No state, no flags.

<details>
<summary>[check] Gate and MODE</summary>

| Command (cloud session) | Before | After |
| --- | --- | --- |
| `make doctor` | fails on packer | passes on the linux image |
| `make pre-commit` | macOS gate, can't run | `cloud-lint cloud-test` |
| `MODE=android` | doctor only n/a | doctor, cloud-lint, pre-commit add Packer |
| PR `check` job | `cloud-lint cloud-test` | triage `session-android`, then `cloud-lint cloud-test MODE=android` |

- An unknown `MODE` makes `cloud-lint` fail rather than quietly skip the template.
- `cloud-ci: override MODE := android`, so the full local check always includes it.

</details>

<details>
<summary>Decisions</summary>

- `MODE=android` over a `cloud-lint-android`/`pre-commit-android` target: one switch shared with `make doctor` keeps the two in step.
- Packer stays out of the linux image: every repo's sessions would pay for one repo's need.
- CI calls the triage-action with `profile: session-android` rather than `make doctor`, which picks the session profile only when `AGENT_TYPE` is set.
- No `severity: warn` on packer: doctor would be green while the gate is red.

</details>

https://claude.ai/code/session_01BNKwNYU6ccJ3eqZcKx7vYE

Assisted-by: Claude Code
