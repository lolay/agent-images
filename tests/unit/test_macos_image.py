"""The macOS image's Packer template (images/macos/build.pkr.hcl). Packer can't
build it off a Mac, so these read the template."""

import re
import unittest

from support import REPO

TEMPLATE = REPO / "images/macos/build.pkr.hcl"


def provisioners(template: str) -> list:
    """Each provisioner block's text, in order."""
    starts = [match.start() for match in re.finditer(r'^\s*provisioner\s+"', template, re.M)]
    return [template[start:end] for start, end in zip(starts, starts[1:] + [len(template)])]


class MacosTemplateTests(unittest.TestCase):
    def test_the_last_provisioner_turns_remote_login_off(self):
        # Runner VMs are reached with tart exec; SSH (admin/admin) is the build's
        # alone (r1.f23). It must be the last step, so nothing after it needs SSH
        # beyond the plugin's shutdown, which the running sshd still serves.
        last = provisioners(TEMPLATE.read_text())[-1]
        self.assertIn('"sudo launchctl disable system/com.openssh.sshd"', last)
        # The check has to be able to fail: print-disabled exits 0 either way, so
        # the build fails only through grep -q matching sshd's disabled state.
        self.assertRegex(
            last,
            r"print-disabled system \| grep -Eq '\\\"com\.openssh\.sshd\\\" => \(disabled\|true\)'\",",
            "the build fails if the disable didn't take",
        )
        self.assertNotRegex(last, r"\|\|\s*true", "nothing swallows the check's failure")
        # Turning sshd off now (systemsetup, bootout, unload) would cut the
        # plugin's SSH shutdown and leave the build waiting.
        self.assertNotRegex(last, r"setremotelogin|bootout|unload|kill")


if __name__ == "__main__":
    unittest.main()
