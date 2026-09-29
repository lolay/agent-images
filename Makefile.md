# Makefile Reference

The [`Makefile`](./Makefile) is the single source of truth for building the agent image
and running VMs cloned from it. Run `make help` for the quick target list; this file is
the narrative reference.

## Target overview

```mermaid
graph LR
    init --> doctor
    build --> publish
    image-pull --> runner-run
    secret-set --> runner-run
    runner-run --> runner-install --> runner-status
    runner-install --> runner-stop
    runner-install --> runner-uninstall
    image-pull --> vm-create --> vm-up
    vm-up --> vm-status
    ci -.-> lint
    ci -.-> test
```

`runner-run` and `runner-install` are the normal way to run VMs: Claude's
orchestrator boots a fresh VM per session. `vm-up` (`vm-start` followed by `vm-configure`) gives a persistent VM for
debugging. Dotted arrows are independent gates.

## Variables

| Variable | Default | Used by |
| --- | --- | --- |
| `VM` | none (required) | all `vm-*` (`vm-create` refuses `runner-N`), optional for `secret-set` |
| `IMAGE_REF` | `agent-macos` (from `.env`) | `image-pull`, `vm-create`, runner VMs |
| `RUNNER_MAX` / `RUNNER_MIN_IDLE` | `2` / `1` (from `.env`) | Runner VMs at most / kept booted as standby |
| `RUNNER_SPAWN_SECONDS` | `300` | The orchestrator's `--expected-spawn-seconds` lease |
| `ORCHESTRATOR_HEALTH_PORT` | `8080` | The orchestrator's `/healthz` on the host |
| `RUNNER_LABEL_PREFIX` | this Mac's `LocalHostName` | Runner label and guest hostname: `<prefix>-<vm>` |
| `VM_BOOT_TIMEOUT` | `300` | Seconds `vm-configure` waits for the guest agent |
| `VM_CPU` / `VM_MEMORY_GB` | `4` / `12` | runner VMs, `vm-create` |
| `MODE` | `run` | `doctor`: `run` or `build` |
| `NAME` | none | `secret-set`: `claude-environment-secret` |
| `LOG_LINES` | `100` | `vm-logs` |
| `REGISTRY` | from `.env` | `publish` |

## Targets

### Develop

| Target | Description |
| --- | --- |
| `help` | List targets (default goal) |
| `init` | Creates `.env` from `.env.example`; runs `packer init` when Packer is installed |
| `doctor` | Checks host tools for `MODE` and `.env` presence. Read-only |
| `build` | `packer build`; needs `PKR_VAR_user_password` |
| `lint` | `packer fmt -check`, `shellcheck`, `shfmt -d`, `plutil -lint` |
| `format` | `packer fmt`, `shfmt -w` |
| `test` | `packer validate` and the kcpassword helper's unit tests |
| `ci` / `pre-commit` | `lint` + `test`, what CI runs |
| `clean` | Removes `build/logs/`. Leaves `build/runners/`, where live runner VMs hold their names |

### Runners

| Target | Description |
| --- | --- |
| `runner-run` | `scripts/orchestrator-run.sh` in the foreground: Keychain secret into the environment, then `claude self-hosted-runner orchestrator --hooks-dir hooks`. Runner VMs outlive Ctrl-C |
| `runner-install` | `scripts/orchestrator-service.sh install`: host LaunchAgent `com.agent-images.orchestrator`; logs in `build/logs/orchestrator.{out,err}` |
| `runner-uninstall` | Boots out the orchestrator, then stops and deletes every runner VM |
| `runner-stop` | Stops and deletes every runner VM (sessions requeue); a running orchestrator boots replacements |
| `runner-status` | Orchestrator state, its `/healthz` body and log tail, then `vm-status` for each runner VM |

Each runner VM is its own launchd job, `com.agent-images.runner-N`, running
`scripts/runner-once.sh`. Claims live in `build/runners/runner-N/`. Logs, all on the
host so they outlive the VM:

Every LaunchAgent and job writes stdout to `.out` and stderr to `.err`.

| Logs (`.out` / `.err`) | What |
| --- | --- |
| `build/logs/orchestrator` | The orchestrator and every `spawn-runner` run |
| `build/logs/runner-N.runner` | `runner-once`: clone, configure, wait, delete |
| `build/logs/runner-N.guest` | The VM's `agent-runner` (the self-hosted runner and `brew upgrade`) and watchdog output, streamed while it runs; a `=== time runner-N order … ===` header per VM |
| `build/logs/runner-N.tart` | `tart run` output |

### VMs (manual, persistent; for debugging)

| Target | Description |
| --- | --- |
| `image-pull` | `tart pull $(IMAGE_REF)` on run hosts |
| `vm-create` | `tart clone` then `tart set --cpu --memory` |
| `vm-start` | `tart run --no-graphics` in the background; log in `build/logs/` |
| `vm-configure` | `scripts/vm-configure.sh`: hostname, secrets, `runner.env`, runner restart. Idempotent |
| `vm-up` | `vm-start` + `vm-configure` |
| `vm-stop` / `vm-delete` | `tart stop`; `tart delete` after stopping |
| `vm-status` | `scripts/vm-status.sh`: runner, process, Claude `/healthz`, last log lines |
| `vm-logs` | Tails a running guest's `~/Library/Logs/agent-runner.out` and `.err` |
| `vm-versions` | macOS, Xcode, and Claude Code versions in the guest |
| `vm-list` | `tart list` |
| `secret-set` | `security add-generic-password` into service `agent-images.<NAME>`, account `<VM>` or `default`; prompts for the value |

### Release / Danger

| Target | Description |
| --- | --- |
| `bump` | Bumps `version.txt`; `LEVEL=patch\|minor\|major` |
| `publish` | `tart push` to `$(REGISTRY)/agent-macos:v$(VERSION)`. Requires `CONFIRM_PUBLISH=1` |

## `make doctor`

Checks tools with `command -v` rather than `triage`; swap in a `triage.yaml` when this
repo joins an estate that uses it.

## CI alignment

| Workflow | Target |
| --- | --- |
| `.github/workflows/ci.yml` | `make init`, `make ci` on `macos-latest` |

Image builds don't run in hosted CI: GitHub's macOS runners can't nest Tart VMs.
