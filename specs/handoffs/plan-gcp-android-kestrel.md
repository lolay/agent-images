# Plan: m2, `android` on Compute Engine

Status: s1 to s6 done 2026-11-06, in lolay/agent-images#21; s7 starts when the owner merges it.
Branch `claude/youthful-dijkstra-09v7kk` (assigned by the harness), cut
from `main` at `ecc54c2`, after lolay/agent-images#18, #19 and #20.

The Android half of m2 in [specs/gcp.md](../gcp.md) ("Compute Engine VM per session").
Done when [tests/sessions/android.md](../../tests/sessions/android.md) passes in a
session on the new type, with the emulator's boot time compared against the Linux
host's (about 9 s from the snapshot in the hosted `android-image-smoke` runs).

## Shape

```
orchestrator (android)  --spawn-runner-->  marker in GCS (as today)
                                           POST compute/v1 .../instances
                                             agent-android-<order prefix>
                                             family agent-android, n2-standard-8,
                                             nested virt, no external IP, agent-runner SA,
                                             maxRunDuration + DELETE,
                                             metadata: work-order, runner-env
                                           poll the insert operation (async errors:
                                             quota, capacity) before exit 0
guest boot: agent-gce-order.service (root, oneshot)
  read work-order once -> ~ubuntu/.claude-runner/environment-secret (600)
  write runner.env (runner-env + EPHEMERAL=true + RUNNER_LABEL) -> agent-runner.path starts
  set guest attribute agent-images/order=read
agent-runner (EPHEMERAL=true): one session, then shutdown -h -> TERMINATED
supervisor reaper, every 60 s:
  delete TERMINATED/STOPPED/SUSPENDED agent-android-*; delete any past MAX_RUN_DURATION_SEC
  remove the work-order key from instances whose guest attribute says read
```

Deploy, on `main` only: `changed-targets.sh` lists `android` under `vms`;
a new `vm-images` job runs Packer (image `agent-android-<date>-<sha>` in family
`agent-android-next`), boots a smoke VM from it, then patches its family to
`agent-android` (images.patch accepts `family`) and deletes the older images in
both families, keeping one.

## Decisions made here (say if you want otherwise)

- **The guest doesn't delete its own metadata key; the reaper does.** Deleting it
  from inside needs `compute.instances.setMetadata` on `agent-runner`. Granted
  project-wide, that lets any `android` session rewrite another session VM's
  `startup-script` and run code in it; IAM conditions can't say "only yourself".
  Instead the guest marks the order read in a guest attribute (no IAM needed), and
  the reaper, which already has `compute.instanceAdmin.v1`, removes the key within a
  minute. The key is spent at registration anyway, the same reasoning as the Cloud
  Run override, which stays readable for the execution's life. This is the one
  departure from the brief; it's the question at the STOP below.
- **Scratch is the boot disk, buckets later.** `AGENT_SCRATCH=/scratch` on the 100 GB
  boot disk. `$AGENT_OUTPUTS` and `$AGENT_DATASETS` through gcsfuse are left out of
  m2: the test doesn't use them, and they'd add a toolset and a mount unit. Listed as
  an open item in gcp.md.
- **Layout:** the Packer template and the GCE-only guest files live in
  `images/android/gce/`, beside the LXD image's `scripts` and `files`, which it reuses
  unchanged. The LXD build ignores `gce/`.
- **Guest user `ubuntu`:** Ubuntu's GCE image has no `ubuntu` user (cloud-init makes
  one only on the cloud images LXD uses), so the template's first provisioner creates
  it with passwordless sudo, then runs `setup-user.sh` and `install-packages.sh` as
  it, exactly as `build-image.sh` does.
- **Logs:** journald forwards the runner and watchdog units to the serial console,
  and the VM sets `serial-port-logging-enable`, so the guest's log reaches Cloud
  Logging after the VM is gone. If the org policy `compute.disableSerialPortLogging`
  blocks that, s7 finds out; the fallback (the reaper reading the serial output
  before it deletes a VM) is left out for now, since it can be up to 1 MB per VM
  into an `e2-micro`'s log.
- **The smoke VM measures the emulator.** It boots with metadata `agent-smoke=true`
  and no service account, runs the checks (`claude --version`, `kvm-ok`, sudo,
  `agent-emulator start` and `wait` timed twice: the first boot reads a lazily
  restored disk, the second is warm), and reports through guest attributes. So every
  deploy records the GCE boot time, and the open question gets a number before any
  session runs.
- **Build VM:** `n2-standard-8`, `enable_nested_virtualization`, IAP SSH
  (`use_iap`, tag `packer`, `omit_external_ip`, `use_internal_ip`), the `agents`
  subnet, `disable_default_service_account`, a 100 GB `pd-balanced` disk,
  `max_run_duration_in_seconds` so a hung build can't run up a bill. Plugin
  `googlecompute` v1.2.9 (latest, 2026-11-02).

## Steps

#### s1 - Type settings and guest flow [deep] (done)

- `infra/gcp/android.env`: `BACKEND=compute`, `MACHINE_TYPE=n2-standard-8`,
  `DISK_GB=100`, `DISK_TYPE=pd-balanced`, `IMAGE_FAMILY=agent-android`,
  `MAX_RUN_DURATION_SEC` (9 h 30 min, as the jobs' task timeout), `RETIRE_MARGIN_SEC`,
  orchestrator and runner settings as the other types (`MIN_IDLE=0`,
  `EXPECTED_SPAWN_SECONDS` set from s7's measurement, 240 to start).
- `images/android/gce/files/agent-gce-order.sh` and its unit: the flow above, plus
  smoke mode, plus "no order and no smoke": log and power off.
- `images/android/gce/files/journald` drop-in for the serial console.
- Unit tests against a fake metadata server (extend `tests/unit/fake_gcp.py`): order
  written mode 600 and owned by the guest user, runner.env contents, guest attribute
  set, work order never on stdout or stderr, smoke results.

#### s2 - Packer template [deep] (done)

- `images/android/gce/{plugins,variables,build}.pkr.hcl` as above; staging mirrors
  `scripts/linux/build-image.sh`; each script's stderr is collected and the build
  fails on any (green builds).
- Cleanup provisioner: SSH keys, machine-id, apt lists, the build user.
- `packer fmt -check` and `packer validate` (with `-syntax-only` where it needs GCP)
  in `make cloud-lint` and the pull request check.

#### s3 - The hook's Compute Engine branch [deep] (done)

- `hooks/gcp/lib.sh`: `GCP_COMPUTE_URL`. `spawn-runner`: branch on `BACKEND`; build
  the instance body with `jq` from the env files (work order from the file on stdin,
  never a command line); poll the zone operation for up to 40 s.
- Exit codes: 409 (instance exists, a redelivered order) is 0; quota and capacity
  errors (`QUOTA_EXCEEDED`, `ZONE_RESOURCE_POOL_EXHAUSTED`, in the response or the
  operation) are 1; other 403 and 404 (no image, no permission) are 2. Every failure
  after the marker deletes it, as now.
- Tests in `test_spawn_runner.py` against fake Compute endpoints.

#### s4 - The reaper [deep] (done)

- In `supervisor.sh`'s loop, every `REAPER_INTERVAL_SEC` (60), for each type with
  `BACKEND=compute`: list instances by label `agent-images-type=<type>`, delete the
  stopped ones and any older than `MAX_RUN_DURATION_SEC` plus a grace, and clear the
  work-order key where the guest attribute says read. Deletes don't wait for their
  operation; a failure is logged and retried next pass. A curl in this path must not
  stall the loop beyond its `--max-time`.
- Tests in `test_supervisor.py`.

#### s5 - Deploy wiring [deep] (done)

- `changed-targets.sh`: `types` stays the Cloud Run list as it was, and a new
  `vms` list names the compute types whose image needs a build (kept apart rather
  than a `backend` field on every entry, so the existing contract and tests didn't
  change shape). A compute type's image paths are
  `images/android/**`, `images/shared/**`, the two toolset scripts, and the toolsets
  in `images/android/toolsets.txt`; its env file alone rebuilds nothing (the hook
  reads it, so the orchestrator redeploys, as today).
- `deploy.sh vm-image <type> <image> <commit>`: the smoke VM, its results into the
  step summary (boot times included), delete it, patch the family, delete older
  images. `deploy.sh prune` also deletes images of a type whose env file is gone.
  `deploy.sh status` shows the family's image.
- `cloud-deploy.yml`: the `types` matrix takes only `backend: run`; a new `vm-images`
  job (`hashicorp/setup-packer`, `packer init`, `packer build`, then `deploy.sh
  vm-image`); the orchestrator job waits on both.
- Tests in `test_changed_targets.py` and `test_deploy.py`.

#### s6 - Docs [exec] (done)

- `specs/gcp.md` ("Compute Engine VM per session" as built, the deploy map, open
  items), the GCE guest's `CLAUDE.md` lines (scratch, sudo, one session),
  `Makefile.md`, README, and this plan's status. Then a review guide and the PR draft
  for you.

**STOP: owner's go before s7.** s7 spends money: merging deploys, which builds the
image and boots a smoke VM. Roughly $0.40 per image build (an `n2-standard-8` for
about 45 min), under $0.10 per smoke VM, $1 to $2 a month to store one image, and
about $0.40 an hour per session VM (`n2-standard-8` plus its disk). Also yours before
s7:

- Create the "Agent: Android" environment in claude.ai and the `claude-env-android`
  secret (until then the android orchestrator retries, as `gis` did).
- Check two things in `lolay/infrastructure`, or let s7 find out: whether OS Login is
  on project-wide (then Packer needs `use_os_login` and CI needs
  `roles/compute.osAdminLogin`), and whether `compute.disableSerialPortLogging` is
  enforced.

#### s7 - First deploy [exec]

Watch the `main` deploy: the Packer build (does the build VM's nested KVM boot the
emulator and save its snapshot?), the smoke VM's boot times, the promotion. Set
`EXPECTED_SPAWN_SECONDS` from VM create to runner registration.

#### s8 - Session test and the decision [deep]

Run [tests/sessions/android.md](../../tests/sessions/android.md) in an "Agent:
Android" session; record the results in gcp.md (an "m2 results" table), the emulator
boot time on GCE next to the Linux host's 9 s, and a recommendation: `android` stays
on GCP or goes back to the Linux host.

## Open questions this settles

| Question | Answered by |
| --- | --- |
| Emulator speed under nested virtualization on Compute Engine | s7's smoke VM (boot from snapshot, cold disk and warm), then s8's builds and UI test |
| Whether the Packer build VM's nested KVM boots the emulator for the snapshot | s7's Packer build (the `android` toolset's install.sh fails the build if not) |
