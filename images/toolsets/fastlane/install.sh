#!/bin/bash
# The fastlane toolset: fastlane from RubyGems into Ubuntu's Ruby, as GitHub's
# runner and CircleCI's Android image install it. A project that pins it in a
# Gemfile runs its own through `bundle exec fastlane`. Run by install-toolset.sh
# after the toolset's apt packages, as the image's user (who has sudo).
set -euo pipefail

sudo gem install --no-document --quiet fastlane 2>&1
printf '==> install-toolset: %s\n' "$(gem list --exact fastlane | tail -n 1)"
