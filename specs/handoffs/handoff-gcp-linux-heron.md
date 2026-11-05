# Handoff: m1 `linux` on GCP

Branch: `claude/eloquent-mayer-ivkml8` in `lolay/agent-images` (assigned by the
harness). `lolay/infrastructure` has a branch of the same name with nothing on it; m1
needs no Terraform change.

Plan: [plan-gcp-linux-heron.md](plan-gcp-linux-heron.md), tagged: 13 executable
steps plus two Owner gates (s13, s14).

## Done

- Read `specs/gcp.md`, `specs/gcp-infrastructure.md`, `specs/design.md`,
  `tests/sessions/linux.md`, the shared and Linux image scripts, and the applied
  Terraform in `lolay/infrastructure` `stacks/gcp/agents` (IAM, WIF, the VM's
  cloud-init, quotas).
- Wrote the plan. No code yet.

## Pending

Gary's approval of the plan, and one decision:

> Approve the m1 plan in specs/handoffs/plan-gcp-linux-heron.md? And for the `linux`
> standby: `MIN_IDLE=0` (recommended; cold start on every session, $0 idle) or
> `MIN_IDLE=1` as the spec says (about $270/month for a 4 vCPU, 16 GiB execution held
> all month)?

## Key findings from planning

- gcloud 570 can't create a disk-backed Cloud Run volume; the v2 REST API can
  (`emptyDir.medium: DISK`). Jobs get deployed by `PATCH` with `allowMissing=true`.
  The first deploy is the first proof Cloud Run accepts it for jobs.
- The VM's unit ignores a metadata value equal to the running image, so key rotation
  is a VM reset.
- The cost notice is out of m1, so the test's notice check needs its cost half marked
  skipped when unset (s6).
- Docker runs in this container (start `dockerd` first), so all three images build
  and the entrypoint's smoke mode runs locally. shellcheck and shfmt aren't installed
  yet.

## Steps that need Gary

Commands are fish. Run them after the plan's s12 (the PR is ready).

### s13: production environment, before the first deploy

Create the environment with custom branch policies:

```fish
echo '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}' | gh api -X PUT repos/lolay/agent-images/environments/production --input -
```

Allow `main` only:

```fish
gh api -X POST repos/lolay/agent-images/environments/production/deployment-branch-policies -f name=main -f type=branch
```

Check it:

```fish
gh api repos/lolay/agent-images/environments/production/deployment-branch-policies --jq '.branch_policies[] | .name + " " + .type'
```

Merge the PR, then watch the deploy:

```fish
gh run watch (gh run list --repo lolay/agent-images --workflow cloud-deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId') --repo lolay/agent-images
```

```fish
make cloud-status
```

The orchestrator's first minutes on the VM:

```fish
gcloud logging read 'resource.type="gce_instance" AND "agent-orchestrator"' --project lolay-agents --freshness 30m --limit 50 --format 'value(timestamp,textPayload,jsonPayload.message)'
```

### s14: the claude.ai environment and two test runs

In claude.ai, check that "Agent: Linux" is in the environment list. If it isn't and
you create it, its key replaces the one in Secret Manager:

```fish
read -s -P 'claude-env-linux key: ' key; and printf '%s' $key | gcloud secrets versions add claude-env-linux --project lolay-agents --data-file=-; set -e key
```

Then restart the orchestrator so it reads the new version (setting the same digest
again does nothing):

```fish
gcloud compute instances reset agent-orchestrator --zone us-central1-a --project lolay-agents
```

Run the test twice, each in a new session in "Agent: Linux" on `lolay/agent-images`,
sending: `Run the runner test in tests/sessions/linux.md. Follow it exactly.` The
second run's isolation check needs a fresh runner, so start it after the first ends.

Read the orchestrator's memory (one line per type every 5 minutes, plus a total):

```fish
gcloud logging read 'resource.type="gce_instance" AND "supervisor: memory"' --project lolay-agents --freshness 1d --limit 40 --format 'value(timestamp,textPayload,jsonPayload.message)'
```

Send back both session branches (or their `report.md`) and a few memory lines from
during a session.

## How to resume

Read the plan, then start at the first step without `(done)`. Before building: start
`dockerd` if `docker info` fails, and install shellcheck and shfmt into a venv or from
their releases (no global pip).
