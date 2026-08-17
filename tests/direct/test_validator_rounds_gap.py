"""
Adversarial regression test for a gap that was found and fixed in
`validator_fn`: it used to compare only the leader's claimed
`outcome`/`split_pct` against the validator's own independent
recomputation, but never compared the leader's claimed `rounds` -- even
though `rounds`, not `split_pct`, is what `_settle_from_rounds` (and
therefore `start_estimation`) actually derives the paid split from. A
byzantine leader could report a `split_pct` that matches what an honest
validator would independently compute (passing the outer tolerance check)
while shipping fabricated `rounds` data that derives to a materially
different, self-serving payout.

This was CONFIRMED as a real, exploitable gap before being fixed: run
against the pre-fix `validator_fn`, the scenario below was accepted
(`run_validator()` returned `True`) despite the leader's actual payout
(derived from its fabricated `rounds`) being 90/10 while every honest
party -- including the validator's own independent recomputation --
agreed on 20/80. See README.md for the full writeup. `validator_fn` now
derives its own accept/reject decision from `_settle_from_rounds` on both
sides' `rounds`, exactly the function that determines payment, so this
exact scenario is asserted below to be correctly REJECTED.

Same technique as tests/direct/test_single_source_of_truth.py: monkeypatch
the deployed module's `_run_rounds` to inject the exact adversarial value a
byzantine leader's `leader_fn()` would need to return, then use
genlayer-test's `run_validator()` to replay the actual captured
`validator_fn` against it.
"""

import sys

from conftest import CONTRACT, mock_all_samples


def _hex(addr) -> str:
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + addr.hex()
    return str(addr)


ALICE_TEXT = "I wrote the docs, fixed typos, and reviewed three pull requests for the project."
BOB_TEXT = "I built the core matching engine end to end and wrote forty unit tests for it."


def _deploy_fund_submit(direct_deploy, direct_vm, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])
    direct_vm.value = 1_000_000
    contract.fund()
    direct_vm.value = 0
    direct_vm.sender = direct_alice
    contract.submit_contribution(ALICE_TEXT)
    direct_vm.sender = direct_bob
    contract.submit_contribution(BOB_TEXT)
    return contract


def _contract_module():
    return sys.modules["_contract_fairsplit"]


def _byzantine_scenario(a, b):
    """leader claims split_pct={a:20,b:80} (honest-looking) but ships
    `rounds` that actually derive to {a:90,b:10}."""
    honest_looking_split = {a: 20, b: 80}
    fabricated_rounds = [
        {
            "round": 0,
            "valid_samples": 3,
            "outcome": "CONVERGED",
            "samples": [
                {a: {"pct": 90, "justification": "j", "excerpt": "e"}, b: {"pct": 10, "justification": "j", "excerpt": "e"}},
                {a: {"pct": 90, "justification": "j", "excerpt": "e"}, b: {"pct": 10, "justification": "j", "excerpt": "e"}},
                {a: {"pct": 90, "justification": "j", "excerpt": "e"}, b: {"pct": 10, "justification": "j", "excerpt": "e"}},
            ],
        }
    ]

    def byzantine_run_rounds(addrs, submissions_map):
        return {"outcome": "CONVERGED", "split_pct": honest_looking_split, "rounds": fabricated_rounds}

    return byzantine_run_rounds


def test_validator_rejects_leader_with_honest_split_pct_but_fabricated_rounds(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    contract = _deploy_fund_submit(direct_deploy, direct_vm, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    module = _contract_module()
    original_run_rounds = module._run_rounds
    module._run_rounds = _byzantine_scenario(a, b)
    try:
        direct_vm.sender = direct_owner
        stage = contract.start_estimation()
    finally:
        module._run_rounds = original_run_rounds

    assert stage == "CONVERGED"
    # What actually gets paid still correctly comes from the fabricated
    # `rounds` (90/10), not the honest-looking claimed split_pct (20/80)
    # -- the single-source-of-truth guarantee from the prior fix still
    # holds; that was never the part that was broken.
    assert contract.get_settled_split_bp(a) == 9000
    assert contract.get_settled_split_bp(b) == 1000

    # An honest validator independently recomputes and gets exactly 20/80
    # (matching the leader's claimed split_pct, NOT the fabricated rounds
    # it actually shipped).
    matching_sample = {a: (20, "docs", "fixed typos"), b: (80, "core", "core matching engine")}
    mock_all_samples(direct_vm, a, b, rounds_samples=[[matching_sample, matching_sample, matching_sample]])

    accepted = direct_vm.run_validator()

    # Post-fix: validator_fn now derives its own accept/reject decision
    # from `_settle_from_rounds` on both sides' `rounds` -- the actual
    # payment-determining data -- not from the separately-reported
    # `split_pct`/`outcome` shortcut fields. The validator's own honest
    # rounds derive to 20/80; the leader's fabricated rounds derive to
    # 90/10; those are nothing alike, so this must be rejected.
    assert accepted is False


def test_validator_rejects_leader_claiming_no_consensus_with_rounds_that_actually_converge(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """Reverse-direction variant of the same gap: a leader claims
    outcome="NO_CONSENSUS" (with a decoy equal-split split_pct=50/50),
    betting that a shallower validator might only compare outcome CLASS
    and wave through anything both sides call "NO_CONSENSUS" without
    scrutinizing the numbers -- while its `rounds` data actually contains
    3 tightly-agreeing samples that cleanly converge to a self-serving
    90/10, smuggled in under the NO_CONSENSUS label.

    validator_fn never reads `leader_data["outcome"]` or
    `leader_data["split_pct"]` at all -- it derives its own judgment via
    `_settle_from_rounds(leader_data.get("rounds", []), addrs)`, which
    recomputes convergence purely from the sample percentages and ignores
    whatever label the leader attached. So the claimed "NO_CONSENSUS"
    label here is inert: the leader's rounds are correctly seen as
    CONVERGED-at-90/10 regardless, and rejected because the validator's
    own honestly-computed rounds land somewhere else entirely."""
    contract = _deploy_fund_submit(direct_deploy, direct_vm, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    module = _contract_module()
    original_run_rounds = module._run_rounds

    decoy_split = {a: 50, b: 50}
    cleanly_converging_but_self_serving_rounds = [
        {
            "round": 0,
            "valid_samples": 3,
            "outcome": "NO_CONSENSUS",  # the label lies too, but it's never read
            "samples": [
                {a: {"pct": 90, "justification": "j", "excerpt": "e"}, b: {"pct": 10, "justification": "j", "excerpt": "e"}},
                {a: {"pct": 90, "justification": "j", "excerpt": "e"}, b: {"pct": 10, "justification": "j", "excerpt": "e"}},
                {a: {"pct": 90, "justification": "j", "excerpt": "e"}, b: {"pct": 10, "justification": "j", "excerpt": "e"}},
            ],
        }
    ]

    def byzantine_run_rounds(addrs, submissions_map):
        return {
            "outcome": "NO_CONSENSUS",
            "split_pct": decoy_split,
            "rounds": cleanly_converging_but_self_serving_rounds,
        }

    module._run_rounds = byzantine_run_rounds
    try:
        direct_vm.sender = direct_owner
        stage = contract.start_estimation()
    finally:
        module._run_rounds = original_run_rounds

    # Confirms the single-source-of-truth guarantee already catches the
    # mislabeling on the settlement side: what's stored is CONVERGED at
    # 90/10 (derived from the real rounds), not NO_CONSENSUS/50-50 (the
    # claimed label) -- the label was never trusted for storage either.
    assert stage == "CONVERGED"
    assert contract.get_settled_split_bp(a) == 9000
    assert contract.get_settled_split_bp(b) == 1000

    # An honest validator, reading the same genuine evidence, independently
    # lands on a real (and very different) split -- e.g. close to 50/50 --
    # nowhere near the leader's fabricated 90/10.
    honest_sample = {a: (48, "docs", "fixed typos"), b: (52, "core", "core matching engine")}
    mock_all_samples(direct_vm, a, b, rounds_samples=[[honest_sample, honest_sample, honest_sample]])

    accepted = direct_vm.run_validator()

    # The NO_CONSENSUS label was inert -- validator_fn derived CONVERGED
    # from both sides' real rounds and correctly rejected because 90/10
    # and ~48/52 are nothing alike.
    assert accepted is False


def test_validator_accepts_when_rounds_genuinely_agree(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """Sanity check in the other direction: an honest leader whose
    `rounds` genuinely derive to the same split the validator
    independently computes must still be accepted -- the fix must not
    have made validator_fn overly strict."""
    contract = _deploy_fund_submit(direct_deploy, direct_vm, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    leader_round = [
        {a: (25, "docs", "fixed typos"), b: (75, "core", "core matching engine")},
        {a: (22, "docs", "reviewed three pull requests"), b: (78, "core", "forty unit tests")},
        {a: (28, "docs", "wrote the docs"), b: (72, "core", "core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[leader_round])
    direct_vm.sender = direct_owner
    assert contract.start_estimation() == "CONVERGED"

    direct_vm.clear_mocks()
    close_round = [
        {a: (27, "docs", "fixed typos"), b: (73, "core", "core matching engine")},
        {a: (24, "docs", "reviewed three pull requests"), b: (76, "core", "forty unit tests")},
        {a: (26, "docs", "wrote the docs"), b: (74, "core", "core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[close_round])

    accepted = direct_vm.run_validator()
    assert accepted is True
