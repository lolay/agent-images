# agent-images Makefile
#
# Single source of truth for building the macOS agent-runner image and running
# VMs cloned from it. Humans, agents, and CI run the same verbs; CI calls
# `make <target>`.
#
# Two host roles (make doctor checks each with triage; see triage.yaml):
#   build host  tart, packer, python3, shellcheck, shfmt, triage  (make build, ci)
#   run host    tart, claude, triage                              (make runner-*)
#
# Conventions:
#   - `.DEFAULT_GOAL := help`; bare `make` prints grouped targets.
#   - Self-documenting: `## comment` after a target; `##@ Section` for headers.
#   - Remote-mutating targets under `##@ Danger` with CONFIRM_* guards.
#
# See Makefile.md for the narrative reference.

SHELL := bash

.DEFAULT_GOAL := help

# Local secrets and overrides (PKR_VAR_user_password, REGISTRY, IMAGE_REF). Never committed.
-include .env
export

.PHONY: help init build lint format test ci pre-commit doctor clean \
        runner-run runner-install runner-uninstall runner-stop runner-status \
        image-pull image-prune vm-create vm-start vm-configure vm-up vm-stop vm-status vm-logs \
        vm-versions vm-list vm-delete secret-set \
        gh-runs-list gh-runs-watch gh-runs-status bump publish

MODE ?= run
GH_LIMIT ?= 50
LEVEL ?= patch

MACOS_DIR := images/macos
BUILD_DIR := build
LOG_DIR := $(BUILD_DIR)/logs
IMAGE_NAME := agent-macos
# Packer builds here, then build swaps it into IMAGE_NAME, so runner VMs (which
# clone IMAGE_NAME) never clone a half-built image.
PKR_VAR_vm_name := $(IMAGE_NAME)-next
IMAGE_REF ?= $(IMAGE_NAME)
VM ?=
VM_CPU ?= 4
VM_MEMORY_GB ?= 12
LOG_LINES ?= 100
TART_CACHE_GB ?= 200
AGENT_USER := agent
VERSION := $(shell cat version.txt)
REGISTRY ?=
SECRET_NAMES := claude-environment-secret

# Base image: the newest Xcode image for the build host's macOS, pulled fresh on
# every build. A guest can't run a newer macOS than its host, so the host decides.
# Cirrus names images by macOS codename; a new major version needs one line here.
HOST_MACOS_MAJOR := $(shell sw_vers -productVersion 2>/dev/null | cut -d. -f1)
MACOS_CODENAME_15 := sequoia
MACOS_CODENAME_26 := tahoe
MACOS_CODENAME_27 := golden-gate
MACOS_CODENAME ?= $(MACOS_CODENAME_$(HOST_MACOS_MAJOR))
PKR_VAR_base_image ?= ghcr.io/cirruslabs/macos-$(MACOS_CODENAME)-xcode:latest

SHELL_SCRIPTS := $(wildcard $(MACOS_DIR)/scripts/*.sh $(MACOS_DIR)/files/*.sh \
                   $(MACOS_DIR)/files/runners/*.sh $(MACOS_DIR)/files/claude/hooks/*.sh \
                   scripts/*.sh hooks/spawn-runner)

confirm = @if [ -z "$($(1))" ]; then \
  printf 'Refusing to run "make %s": %s\nRe-run with %s=1.\n' "$@" "$(2)" "$(1)"; \
  exit 1; \
fi

require_vm = @[ -n "$(VM)" ] || { printf 'Set VM=<name>, e.g. make $@ VM=debug-1\n' >&2; exit 1; }

# runner-N names belong to the orchestrator; a manual VM by that name would block a slot.
require_manual_vm = @[ -n "$(VM)" ] || { printf 'Set VM=<name>, e.g. make $@ VM=debug-1\n' >&2; exit 1; }; \
  if [[ "$(VM)" =~ ^runner-[0-9]+$$ ]]; then printf '%s is reserved for runner VMs; pick another name\n' "$(VM)" >&2; exit 1; fi

##@ Develop

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_.-]+:.*?##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2} /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0,5)}' $(MAKEFILE_LIST)

init: ## Install Packer plugins (build hosts) and create .env if missing
	@[ -f .env ] || { cp .env.example .env; printf 'Created .env from .env.example.\n'; }
	@if command -v packer >/dev/null 2>&1; then packer init $(MACOS_DIR); \
	else printf 'packer not found; skipping plugin install (fine on a run host).\n'; fi

doctor: ## Check this host with triage (read-only). MODE=run|build
	@command -v triage >/dev/null 2>&1 || { printf 'triage not found; run:\n  brew tap lolay/tap && brew trust lolay/tap && brew install lolay/tap/triage\n' >&2; exit 1; }
	@case "$(MODE)" in run) profile=default;; build) profile=build;; \
	  *) printf 'MODE must be run or build\n' >&2; exit 1;; esac; \
	triage --profile "$$profile" --var image_ref=$(IMAGE_REF)

build: ## Build the agent image on the newest Xcode image for this host's macOS
	@[ -n "$$PKR_VAR_user_password" ] || { printf 'PKR_VAR_user_password is not set (see .env.example)\n' >&2; exit 1; }
	@[ -n "$(MACOS_CODENAME)" ] || { printf 'No Cirrus image codename for macOS %s; add MACOS_CODENAME_%s to the Makefile\n' \
	  "$(HOST_MACOS_MAJOR)" "$(HOST_MACOS_MAJOR)" >&2; exit 1; }
	@# Clone would reuse a cached :latest; pull checks for a newer one first.
	tart pull $(PKR_VAR_base_image)
	mkdir -p $(BUILD_DIR)
	packer build -force $(MACOS_DIR)
	@# Only a finished build reaches IMAGE_NAME. A runner VM cloning in the moment
	@# between delete and rename fails its spawn, and the session is re-offered.
	@if tart get $(IMAGE_NAME) >/dev/null 2>&1; then tart delete $(IMAGE_NAME); fi
	tart rename $(PKR_VAR_vm_name) $(IMAGE_NAME)
	@printf 'Built %s on %s\n' "$(IMAGE_NAME)" "$(PKR_VAR_base_image)"

lint: ## Check formatting and static analysis (no writes)
	packer fmt -check -recursive $(MACOS_DIR)
	shellcheck -x $(SHELL_SCRIPTS)
	shfmt -d $(SHELL_SCRIPTS)
	@for plist in $(MACOS_DIR)/files/*.plist; do plutil -lint "$$plist"; done
	@python3 -m json.tool $(MACOS_DIR)/files/claude/settings.json >/dev/null && printf '%s: OK\n' $(MACOS_DIR)/files/claude/settings.json

format: ## Auto-fix Packer and shell formatting
	packer fmt -recursive $(MACOS_DIR)
	shfmt -w $(SHELL_SCRIPTS)

test: ## Validate the Packer build and run helper unit tests
	PKR_VAR_user_password=validate-only packer validate $(MACOS_DIR)
	python3 -m unittest discover -s $(MACOS_DIR)/scripts -p 'test_*.py'

ci: lint test ## Run the full pre-push gate (what CI runs)

pre-commit: ci ## Run the local gate before committing or pushing (alias of ci)

clean: ## Remove host logs (not VMs, images, or runner claims in build/runners)
	rm -rf $(LOG_DIR)

##@ Runners

runner-run: ## Run the orchestrator in the foreground; runner VMs outlive Ctrl-C (see runner-stop)
	scripts/orchestrator-run.sh

runner-install: ## Keep the orchestrator running as a host LaunchAgent (starts at login)
	scripts/orchestrator-service.sh install

runner-uninstall: ## Stop the orchestrator and every runner VM (their sessions requeue)
	scripts/orchestrator-service.sh uninstall

runner-stop: ## Stop and delete every runner VM; a running orchestrator boots new ones
	scripts/orchestrator-service.sh stop-runners

runner-status: ## Orchestrator state and health, then each runner VM
	@scripts/orchestrator-service.sh status

##@ VMs (manual, persistent; for debugging)

image-pull: ## Pull IMAGE_REF from the registry (run hosts)
	tart pull $(IMAGE_REF)

image-prune: ## Shrink Tart's image cache (old base images) to TART_CACHE_GB (default 200)
	tart prune --entries caches --space-budget $(TART_CACHE_GB)

vm-create: ## Clone IMAGE_REF into a VM and size it: VM=debug-1 [VM_CPU=4 VM_MEMORY_GB=12]
	$(require_manual_vm)
	tart clone $(IMAGE_REF) $(VM)
	tart set $(VM) --cpu $(VM_CPU) --memory $$(( $(VM_MEMORY_GB) * 1024 ))

vm-start: ## Start a VM headless: VM=debug-1. Max two macOS VMs run at once
	$(require_vm)
	@mkdir -p $(LOG_DIR)
	@nohup tart run --no-graphics $(VM) >"$(LOG_DIR)/$(VM).log" 2>&1 & \
	  printf 'started %s (log: %s/%s.log)\n' "$(VM)" "$(LOG_DIR)" "$(VM)"

vm-configure: ## Write runner config and secrets into a running VM and restart its runner: VM=debug-1
	$(require_vm)
	scripts/vm-configure.sh $(VM)

vm-up: vm-start vm-configure ## Start a VM and configure its runner: VM=debug-1

vm-stop: ## Stop a VM: VM=debug-1
	$(require_vm)
	tart stop $(VM)

vm-status: ## Runner health for VMs: VM=runner-1 or VM="runner-1 runner-2"
	$(require_vm)
	@scripts/vm-status.sh $(VM)

vm-logs: ## Tail a running VM's runner stdout and stderr: VM=runner-1 [LOG_LINES=100]
	$(require_vm)
	tart exec $(VM) sudo tail -n $(LOG_LINES) /Users/$(AGENT_USER)/Library/Logs/agent-runner.out \
	  /Users/$(AGENT_USER)/Library/Logs/agent-runner.err

vm-versions: ## Report macOS, Xcode, and runner CLI versions in a VM: VM=runner-1
	$(require_vm)
	@tart exec $(VM) sw_vers -productVersion | sed 's/^/  macOS   /'
	@tart exec $(VM) xcodebuild -version | head -n 1 | sed 's/^/  /'
	@tart exec $(VM) sudo -u $(AGENT_USER) -H /bin/zsh -lc 'claude --version' | sed 's/^/  claude  /'

vm-list: ## List local VMs and images
	tart list

vm-delete: ## Stop and delete a VM (the image is kept): VM=debug-1
	$(require_vm)
	-tart stop $(VM)
	tart delete $(VM)

secret-set: ## Store a runner secret in the host Keychain (prompts): NAME=claude-environment-secret [VM=debug-1]
	@if [[ " $(SECRET_NAMES) " != *" $(NAME) "* ]]; then \
	  printf 'NAME must be one of: %s\n' "$(SECRET_NAMES)" >&2; exit 1; fi
	security add-generic-password -U -s "agent-images.$(NAME)" -a "$(or $(VM),default)" -w

##@ GitHub

gh-runs-list: ## List this repo's in-flight Actions runs (status != completed)
	@out=$$(gh run list --limit $(GH_LIMIT) \
	  --json status,workflowName,headBranch,event,url \
	  --jq '.[] | select(.status != "completed") | "  \(.status)\t\(.workflowName)\t\(.headBranch)\t\(.event)\t\(.url)"' 2>&1) \
	  || { printf '  \033[33m⚠\033[0m gh run list failed (auth? run `gh auth login`)\n'; exit 0; }; \
	if [ -z "$$out" ]; then printf '  \033[2mno active runs\033[0m\n'; \
	else printf '%s\n' "$$out" | column -t -s "$$(printf '\t')"; fi

gh-runs-watch: ## Watch this repo's in-flight Actions runs until each completes
	@ids=$$(gh run list --limit $(GH_LIMIT) --json status,databaseId \
	  --jq '.[] | select(.status != "completed") | .databaseId' 2>/dev/null); \
	if [ -z "$$ids" ]; then printf '  \033[2mno active runs\033[0m\n'; exit 0; fi; \
	for id in $$ids; do \
	  gh run watch "$$id" --compact || printf '  \033[33m⚠\033[0m watch failed for run %s\n' "$$id"; \
	done

gh-runs-status: ## Show pass/fail of the last completed run per workflow
	@out=$$(gh run list --limit $(GH_LIMIT) \
	  --json conclusion,workflowName,headBranch,url,status,updatedAt \
	  --jq '[.[] | select(.status == "completed")] | group_by(.workflowName) | map(sort_by(.updatedAt) | last) | sort_by(.updatedAt) | .[] | (now - (.updatedAt | fromdateiso8601)) as $$age | "\(.conclusion)\t\(.workflowName)\t\(.headBranch)\t\(.url)\t\($$age | floor)"' \
	  2>&1) \
	  || { printf '  \033[33m⚠\033[0m gh run list failed (auth? run `gh auth login`)\n'; exit 0; }; \
	if [ -z "$$out" ]; then printf '  \033[2mno completed runs\033[0m\n'; exit 0; fi; \
	esc=$$(printf '\033'); \
	printf '%s\n' "$$out" | while IFS=$$'\t' read -r conclusion name branch url age_secs; do \
	  if [ "$$conclusion" = "success" ]; then mark="ok"; \
	  elif [ "$$conclusion" = "skipped" ] || [ "$$conclusion" = "neutral" ]; then mark="skip"; \
	  else mark="fail"; fi; \
	  if [ "$$age_secs" -lt 60 ]; then age="$${age_secs}s"; \
	  elif [ "$$age_secs" -lt 3600 ]; then age="$$((age_secs / 60))m"; \
	  elif [ "$$age_secs" -lt 86400 ]; then age="$$((age_secs / 3600))h"; \
	  else age="$$((age_secs / 86400))d"; fi; \
	  printf '%s\t%s\t%s\t%s\t%s\n' "$$mark" "$$name" "$$branch" "$$age" "$$url"; \
	done | column -t -s "$$(printf '\t')" \
	| sed -e "s/^ok  /$${esc}[32m✓$${esc}[0m   /" \
	      -e "s/^skip/$${esc}[2m-$${esc}[0m   /" \
	      -e "s/^fail/$${esc}[31m✗$${esc}[0m   /" \
	      -e 's/^/  /'

##@ Release

bump: ## Bump version.txt (LEVEL=patch|minor|major); prints the new version
	@awk -F. -v level=$(LEVEL) '{ \
	  if (level == "major") { $$1++; $$2 = 0; $$3 = 0 } \
	  else if (level == "minor") { $$2++; $$3 = 0 } \
	  else if (level == "patch") { $$3++ } \
	  else { print "LEVEL must be patch, minor, or major" > "/dev/stderr"; exit 1 } \
	  printf "%d.%d.%d\n", $$1, $$2, $$3 }' version.txt > version.txt.tmp \
	  && mv version.txt.tmp version.txt && cat version.txt \
	  || { rm -f version.txt.tmp; exit 1; }

##@ Danger

publish: ## [danger] Push the image to REGISTRY tagged from version.txt
	@[ -n "$(REGISTRY)" ] || { printf 'REGISTRY is not set (see .env.example)\n' >&2; exit 1; }
	$(call confirm,CONFIRM_PUBLISH,this pushes $(IMAGE_NAME) to $(REGISTRY). The image holds /etc/kcpassword so use a private registry)
	tart push $(IMAGE_NAME) $(REGISTRY)/$(IMAGE_NAME):v$(VERSION)
