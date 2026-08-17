"""
Direct-mode tests for the full FairSplit state machine, run against the
real contract (not a copy) via genlayer-test's in-memory VM. Direct mode
executes the leader path only (see skill docs), which is exactly the code
path that runs `_run_rounds` -- so it fully exercises the convergence
logic, the citation check, and the bounded re-run/storage policy.
"""

import json

from conftest import CONTRACT, mock_all_samples


def _hex(addr) -> str:
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + addr.hex()
    return str(addr)


def _deploy(direct_deploy, direct_vm, direct_owner, addrs):
    direct_vm.sender = direct_owner
    return direct_deploy(CONTRACT, [_hex(a) for a in addrs])


def _fund_and_submit(contract, direct_vm, owner, alice, bob, alice_text, bob_text, fund=1_000_000):
    direct_vm.sender = owner
    contract.fund()
    direct_vm.value = 0

    direct_vm.sender = alice
    contract.submit_contribution(alice_text)

    direct_vm.sender = bob
    contract.submit_contribution(bob_text)


ALICE_TEXT = (
    "I wrote the initial documentation pass: README overhaul and fixed three "
    "broken code samples in the quickstart guide."
)
BOB_TEXT = (
    "I designed and implemented the entire payment router module, wrote the "
    "core matching engine, and added forty new unit tests covering edge cases "
    "in settlement math."
)


def test_unequal_converged_split_and_payout(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    contract = _deploy(direct_deploy, direct_vm, direct_owner, [direct_alice, direct_bob])
    direct_vm.value = 1_000_000
    _fund_and_submit(contract, direct_vm, direct_owner, direct_alice, direct_bob, ALICE_TEXT, BOB_TEXT)

    a, b = contract.get_state()["contributors"]
    # Three independent reads all agree Bob did the bulk of the real work.
    mock_all_samples(
        direct_vm,
        a,
        b,
        rounds_samples=[
            [
                {a: (15, "docs pass", "fixed three broken code samples"), b: (85, "core work", "designed and implemented the entire payment router module")},
                {a: (20, "docs pass", "README overhaul"), b: (80, "core work", "wrote the core matching engine")},
                {a: (18, "docs pass", "quickstart guide"), b: (82, "core work", "forty new unit tests covering edge cases")},
            ]
        ],
    )

    direct_vm.sender = direct_owner
    stage = contract.start_estimation()
    assert stage == "CONVERGED"

    state = contract.get_state()
    assert state["settlement_outcome"] == "CONVERGED"

    alice_bp = contract.get_settled_split_bp(a)
    bob_bp = contract.get_settled_split_bp(b)
    assert alice_bp + bob_bp == 10000
    assert bob_bp > alice_bp  # genuinely unequal, justified split
    assert alice_bp < 3000  # roughly the ~18% median

    report = contract.get_contributor_report(b)
    assert report["submission"] == BOB_TEXT
    assert len(report["justifications"]) == 3
    for j in report["justifications"]:
        assert j["excerpt"] in BOB_TEXT  # every stored citation is real

    direct_vm.sender = direct_alice
    contract.pay_out()
    assert contract.get_state()["stage"] == "PAID"
    assert contract.get_state()["paid"] is True


def test_no_consensus_reachable_and_fallback_applies(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    contract = _deploy(direct_deploy, direct_vm, direct_owner, [direct_alice, direct_bob])
    direct_vm.value = 1_000_000
    _fund_and_submit(
        contract,
        direct_vm,
        direct_owner,
        direct_alice,
        direct_bob,
        "Both of us say we contributed the most to this ambiguous joint effort.",
        "Both of us say we contributed the most to this ambiguous joint effort, honestly.",
    )

    a, b = contract.get_state()["contributors"]
    # Genuinely contradictory reads in every round: samples swing between
    # "alice did almost everything" and "bob did almost everything",
    # deliberately exceeding TOLERANCE_POINTS in both rounds.
    diverging_round = [
        {a: (10, "alice did little", "ambiguous joint effort"), b: (90, "bob did most", "ambiguous joint effort, honestly")},
        {a: (90, "alice did most", "ambiguous joint effort"), b: (10, "bob did little", "ambiguous joint effort, honestly")},
        {a: (50, "unclear", "ambiguous joint effort"), b: (50, "unclear", "ambiguous joint effort, honestly")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[diverging_round, diverging_round])

    direct_vm.sender = direct_owner
    stage = contract.start_estimation()
    assert stage == "NO_CONSENSUS"

    report = json.loads(contract.get_settlement_report())
    # Bounded retry policy: exactly MAX_ROUNDS=2 rounds attempted and stored,
    # never more, regardless of how contentious the split is.
    assert len(report["rounds"]) == 2
    for r in report["rounds"]:
        assert r["outcome"] == "NO_CONSENSUS"
        assert len(r["samples"]) <= 3

    # Fallback is a real, documented equal split -- not a silent pick of one
    # validator's answer and not a silent average.
    alice_bp = contract.get_settled_split_bp(a)
    bob_bp = contract.get_settled_split_bp(b)
    assert alice_bp == bob_bp == 5000

    direct_vm.sender = direct_bob
    contract.pay_out_fallback()
    assert contract.get_state()["stage"] == "PAID"


def test_settled_split_is_always_derived_from_stored_raw_samples(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """The Concord-lesson test: recompute_settlement() rebuilds the split
    purely from the stored raw per-round samples, using the same pure
    functions the contract used to settle. It must exactly equal
    settled_split_bp -- there is no second, separately-trusted field that
    could have drifted from what was actually agreed."""
    contract = _deploy(direct_deploy, direct_vm, direct_owner, [direct_alice, direct_bob])
    direct_vm.value = 1_000_000
    _fund_and_submit(contract, direct_vm, direct_owner, direct_alice, direct_bob, ALICE_TEXT, BOB_TEXT)

    a, b = contract.get_state()["contributors"]
    mock_all_samples(
        direct_vm,
        a,
        b,
        rounds_samples=[
            [
                {a: (30, "docs", "README overhaul"), b: (70, "core", "core matching engine")},
                {a: (33, "docs", "README overhaul"), b: (67, "core", "core matching engine")},
                {a: (28, "docs", "README overhaul"), b: (72, "core", "core matching engine")},
            ]
        ],
    )

    direct_vm.sender = direct_owner
    assert contract.start_estimation() == "CONVERGED"

    stored = {a: contract.get_settled_split_bp(a), b: contract.get_settled_split_bp(b)}
    recomputed = contract.recompute_settlement()
    assert recomputed == stored


def test_citation_rejection_drops_the_sample_not_the_whole_round(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """The ProofReader-lesson test: a sample whose justification cites text
    that does not exist in the cited contributor's own submission must be
    rejected outright -- not stored, not counted toward convergence."""
    contract = _deploy(direct_deploy, direct_vm, direct_owner, [direct_alice, direct_bob])
    direct_vm.value = 1_000_000
    _fund_and_submit(contract, direct_vm, direct_owner, direct_alice, direct_bob, ALICE_TEXT, BOB_TEXT)

    a, b = contract.get_state()["contributors"]
    FABRICATED = "this sentence was never submitted by anyone"
    mock_all_samples(
        direct_vm,
        a,
        b,
        rounds_samples=[
            [
                # Sample 0: Alice's excerpt is fabricated -> whole sample dropped.
                {a: (15, "docs", FABRICATED), b: (85, "core", "core matching engine")},
                {a: (20, "docs", "README overhaul"), b: (80, "core", "core matching engine")},
                {a: (18, "docs", "quickstart guide"), b: (82, "core", "forty new unit tests")},
            ]
        ],
    )

    direct_vm.sender = direct_owner
    stage = contract.start_estimation()
    assert stage == "CONVERGED"  # the two valid samples still agree

    report = json.loads(contract.get_settlement_report())
    assert report["rounds"][0]["valid_samples"] == 2  # one sample was thrown out
    for sample in report["rounds"][0]["samples"]:
        assert sample[a]["excerpt"] != FABRICATED
        assert FABRICATED not in json.dumps(sample)
