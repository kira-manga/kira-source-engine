#!/usr/bin/env python3
"""Read-only completed byte intake with fresh official observations; no release authority."""

import argparse
from pathlib import Path
import sys

import github_publication_receipts as receipts
import publication_policy as policy


def connected_intake(root, expected, archive, gh_asset, attestation, *, token=None):
    """Validate bytes/native evidence, acquire official metadata, then recheck local bytes.

    The selection is independently supplied and copied before any work. No supplied
    receipt JSON or decoded attestation can stand in for the real adapters. Optional
    token custody is explicit and programmatic only; native verification never gets it.
    Success returns no cached receipt, reservation, approval or publication permission.
    """
    try:
        expected = receipts.validated_request(expected, token)
        source, measured = policy.verify_completed_bytes(root, expected, archive, gh_asset, attestation)
        receipts.acquire_official_receipts(expected, token)
        policy.recheck_completed_bytes(root, expected, archive, source, measured)
    except Exception:
        # Native/provider errors can contain paths, response content or credentials.
        raise policy.Refusal("Connected candidate intake refused") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Reject attempted token/saved-receipt arguments without echoing their values.
        self.exit(2, "Connected intake arguments refused. No release authority.\n")


def main(argv=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    for name in ("expected", "archive", "gh-asset", "attestation"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        expected = policy.json_data(policy.read_small(args.expected))
        # Deliberately anonymous: no token argument, environment/config discovery or prompt.
        connected_intake(args.root, expected, args.archive, args.gh_asset, args.attestation)
    except Exception:
        print("Connected candidate intake refused. No release authority.", file=sys.stderr)
        return 1
    print("Completed byte intake and fresh official observations matched. NOT release authority or a reservation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
