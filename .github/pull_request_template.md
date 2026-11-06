## What problem does this solve?

Describe the observable user/research problem and the smallest change that addresses it.

## Branch and dependency

- Target base:
- Prerequisite PRs (if any):
- Is this feature on `main`, in a draft experiment, or proposed only?

## Evidence and correctness

- [ ] Source identity, model/preprocessing revision and timestamp basis are explicit.
- [ ] Evidence, hypotheses and human labels remain separate.
- [ ] Unknown/missing coverage is represented, not silently classified negative.
- [ ] Negative tests cover camera motion, sampling gaps, and other failure modes relevant to this change.
- [ ] Existing source videos, human reviews and protected intervals remain untouched by default.
- [ ] No automatic upload, public publishing, or model download has been added without explicit opt-in.

## Test results

```text
Exact commands, platform, number passed / failed, and any skips:
```

## Qualification

- Baseline compared:
- Real-data evaluation available? If not, state **not validated**:
- Held-out unit (outing/trip/source):
- Quality metrics and uncertainty:
- Compute / decode / disk costs:
- Most serious remaining failure:

## Interface / documentation

- [ ] README claims match the default branch or clearly link to the experimental PR.
- [ ] UX changes preserve existing navigation and review history.
- [ ] Docs describe setup, rollback/abstention and known limitations.
