"""
Adversarial proof for the Concord-lesson guarantee: the paid split can
never diverge from what `recompute_settlement()` derives from the stored
raw rounds, even under a maximally adversarial (byzantine) leader that
reports a `split_pct` field inconsistent with the `rounds` data it also
reports.

Method: `_run_rounds` is the one function whose return value becomes the
leader's proposed result inside `start_estimation`. In real GenVM, a
byzantine leader node is the only party that could ever get an
inconsistent value into that position (the contract's own code always
computes `split_pct` and `rounds` consistently together -- see
`_run_rounds` in contracts/fairsplit.py). To test the contract's defense
against that worst case without needing a compromised real network, this
suite monkeypatches the deployed contract module's `_run_rounds` to
directly return a hand-crafted, internally-INCONSISTENT result -- exactly
what a lying leader's `leader_fn()` call would need to produce to attempt
the Concord-class bug (a stored decision that disagrees with the actual
raw evidence). This is pure contract logic with no live-LLM dependency, so
it is proven here in direct mode rather than live, per this task's own
guidance.
"""

import sys

from conftest import CONTRACT


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
    """The direct-mode loader imports the contract file as
    `_contract_<stem>` (see gltest.direct.loader._load_module). Grabbing it
    from sys.modules lets this test monkeypatch `_run_rounds` -- the exact
    seam a byzantine leader's return value would occupy -- without touching
    contracts/fairsplit.py itself."""
    return sys.modules["_contract_fairsplit"]


def _consistent_honest_rounds(a, b, a_pct, b_pct):
    sample = {
        a: {"pct": a_pct, "justification": "honest", "excerpt": "docs"},
        b: {"pct": b_pct, "justification": "honest", "excerpt": "engine"},
    }
    return [{"round": 0, "valid_samples": 3, "outcome": "CONVERGED", "samples": [sample, sample, sample]}]


def test_stored_split_ignores_a_byzantine_leaders_claimed_split_pct(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """The core adversarial proof: force `_run_rounds` (the leader's
    computation) to return `split_pct={90,10}` while its own `rounds` data
    actually only supports 20/80. If the contract trusted the claimed
    `split_pct` (as the pre-hardening version of this contract did), the
    stored split would be 90/10 -- diverging from what `recompute_settlement()`
    derives from the same stored rounds. It must not."""
    contract = _deploy_fund_submit(direct_deploy, direct_vm, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    module = _contract_module()
    original_run_rounds = module._run_rounds

    lied_rounds = _consistent_honest_rounds(a, b, 20, 80)  # rounds actually say 20/80

    def byzantine_run_rounds(addrs, submissions_map):
        return {
            "outcome": "CONVERGED",
            "split_pct": {a: 90, b: 10},  # the LIE the leader claims
            "rounds": lied_rounds,
        }

    module._run_rounds = byzantine_run_rounds
    try:
        direct_vm.sender = direct_owner
        stage = contract.start_estimation()
    finally:
        module._run_rounds = original_run_rounds

    assert stage == "CONVERGED"  # rounds do genuinely converge -- just not at 90/10

    alice_bp = contract.get_settled_split_bp(a)
    bob_bp = contract.get_settled_split_bp(b)

    # The lie must NOT have been persisted.
    assert (alice_bp, bob_bp) != (9000, 1000)
    # What's actually stored must equal what the raw rounds derive to.
    assert (alice_bp, bob_bp) == (2000, 8000)

    # And recompute_settlement(), reading only the stored raw rounds, must
    # land on exactly the same numbers as what got persisted -- structurally
    # the same computation, not a coincidence.
    recomputed = contract.recompute_settlement()
    assert recomputed == {a: alice_bp, b: bob_bp}


def test_malformed_or_empty_rounds_fail_safe_to_equal_split_not_a_crash(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """A byzantine/broken leader could also just ship no rounds at all
    alongside a plausible-looking split_pct claim. That must not crash and
    must not honor the claimed split_pct either -- it must fail safe to the
    documented equal-split fallback, exactly as a genuine NO_CONSENSUS
    would."""
    contract = _deploy_fund_submit(direct_deploy, direct_vm, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    module = _contract_module()
    original_run_rounds = module._run_rounds

    def empty_rounds_leader(addrs, submissions_map):
        return {"outcome": "CONVERGED", "split_pct": {a: 99, b: 1}, "rounds": []}

    module._run_rounds = empty_rounds_leader
    try:
        direct_vm.sender = direct_owner
        stage = contract.start_estimation()
    finally:
        module._run_rounds = original_run_rounds

    assert stage == "NO_CONSENSUS"  # claimed CONVERGED, but no rounds to back it up
    alice_bp = contract.get_settled_split_bp(a)
    bob_bp = contract.get_settled_split_bp(b)
    assert (alice_bp, bob_bp) == (5000, 5000)
    assert contract.recompute_settlement() == {a: alice_bp, b: bob_bp}


def test_settlement_cannot_be_replayed_to_overwrite_a_different_split(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """Once settled, `start_estimation` refuses to run again -- there is no
    way to call it a second time with a different (honest or adversarial)
    result and overwrite the first settlement."""
    contract = _deploy_fund_submit(direct_deploy, direct_vm, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    module = _contract_module()
    original_run_rounds = module._run_rounds
    module._run_rounds = lambda addrs, submissions_map: {
        "outcome": "CONVERGED",
        "split_pct": {a: 30, b: 70},
        "rounds": _consistent_honest_rounds(a, b, 30, 70),
    }
    try:
        direct_vm.sender = direct_owner
        assert contract.start_estimation() == "CONVERGED"
    finally:
        module._run_rounds = original_run_rounds

    first_alice_bp = contract.get_settled_split_bp(a)
    first_bob_bp = contract.get_settled_split_bp(b)
    assert (first_alice_bp, first_bob_bp) == (3000, 7000)

    # Try to replay with a wildly different, self-serving result.
    module._run_rounds = lambda addrs, submissions_map: {
        "outcome": "CONVERGED",
        "split_pct": {a: 1, b: 99},
        "rounds": _consistent_honest_rounds(a, b, 1, 99),
    }
    try:
        direct_vm.sender = direct_owner
        try:
            contract.start_estimation()
            assert False, "expected the second start_estimation() call to be rejected"
        except Exception as e:
            assert "not in SUBMITTING stage" in str(e) or "SUBMITTING" in str(e)
    finally:
        module._run_rounds = original_run_rounds

    # Storage is untouched by the rejected replay attempt.
    assert contract.get_settled_split_bp(a) == first_alice_bp
    assert contract.get_settled_split_bp(b) == first_bob_bp
    assert contract.recompute_settlement() == {a: first_alice_bp, b: first_bob_bp}
