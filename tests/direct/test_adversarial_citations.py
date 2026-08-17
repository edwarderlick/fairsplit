"""
Adversarial proof for the ProofReader-lesson guarantee: a justification
citing evidence that doesn't genuinely exist in the cited contributor's own
submission must be rejected before it is ever persisted -- whether the
citation is fabricated outright, borrowed from a different contributor's
evidence, or lifted from text nobody ever submitted at all.

This is pure contract logic (`_validate_citation` / `_parse_sample_response`
in contracts/fairsplit.py) with no dependency on live LLM behavior -- the
check runs identically regardless of which model produced the citation --
so it is proven here in direct mode with full control over the adversarial
input, per this task's guidance to use direct mode for mechanisms that
don't depend on live LLM behavior.
"""

import json

from conftest import CONTRACT, mock_all_samples


def _hex(addr) -> str:
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + addr.hex()
    return str(addr)


ALICE_TEXT = "I wrote the migration guide and fixed two broken links in the changelog."
BOB_TEXT = "I rewrote the caching layer to use LRU eviction and added stress tests for it."

# Three distinct forgery strategies:
FABRICATED = "this exact sentence was never written by anyone in this contract"
BORROWED_FROM_BOB = "I rewrote the caching layer to use LRU eviction and added stress tests for it."  # Bob's full sentence, verbatim -- but attributed to Alice
NEVER_SUBMITTED_EXTERNAL = "Copyright (c) the standard open source license template"


def test_all_three_citation_forgery_strategies_are_rejected_before_persisting(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """One round, three samples, three different forgery strategies applied
    to Alice's citation across the three samples -- fabricated, borrowed
    from Bob, and lifted from unrelated external text nobody submitted.
    Bob's citations are always genuine. Every sample with a forged Alice
    citation must be dropped in full (not partially stored); only samples
    with a real citation for both contributors may count toward
    convergence or ever appear in the persisted settlement report."""
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])
    direct_vm.value = 1_000_000
    contract.fund()
    direct_vm.value = 0
    direct_vm.sender = direct_alice
    contract.submit_contribution(ALICE_TEXT)
    direct_vm.sender = direct_bob
    contract.submit_contribution(BOB_TEXT)

    a, b = contract.get_state()["contributors"]

    forged_sample_fabricated = {
        a: (30, "wrote docs", FABRICATED),
        b: (70, "core work", "rewrote the caching layer to use LRU eviction"),
    }
    forged_sample_borrowed = {
        a: (30, "wrote docs", BORROWED_FROM_BOB),  # Alice's excerpt is literally Bob's text
        b: (70, "core work", "added stress tests for it"),
    }
    forged_sample_external = {
        a: (30, "wrote docs", NEVER_SUBMITTED_EXTERNAL),
        b: (70, "core work", "rewrote the caching layer to use LRU eviction"),
    }

    forged_round = [forged_sample_fabricated, forged_sample_borrowed, forged_sample_external]
    # Every sample in round 0 is forged (0 valid), so `_run_rounds` retries
    # a second round per the bounded MAX_ROUNDS=2 policy -- mock both
    # rounds identically forged, since a genuinely adversarial/broken
    # source would keep producing bad citations, not spontaneously fix
    # itself on retry.
    mock_all_samples(direct_vm, a, b, rounds_samples=[forged_round, forged_round])

    direct_vm.sender = direct_owner
    stage = contract.start_estimation()

    # Every sample in both rounds had a forged Alice citation, so none were
    # ever valid -- no round can even assess agreement (< 2 valid samples),
    # and the contract must NOT silently invent a result. It falls through
    # to NO_CONSENSUS after exhausting the bounded retry policy, exactly
    # like any other case with no usable data.
    assert stage == "NO_CONSENSUS"

    report = json.loads(contract.get_settlement_report())
    assert len(report["rounds"]) == 2  # bounded retry cap still holds
    for r in report["rounds"]:
        assert r["valid_samples"] == 0

    # None of the three forged strings can appear anywhere in what got
    # persisted -- proving they were dropped before storage, not stored
    # and merely excluded from the tally.
    persisted = contract.get_settlement_report()
    assert FABRICATED not in persisted
    assert BORROWED_FROM_BOB not in persisted
    assert NEVER_SUBMITTED_EXTERNAL not in persisted

    report_a = contract.get_contributor_report(a)
    assert report_a["justifications"] == []


def test_one_forged_sample_among_valid_ones_is_dropped_not_diluted(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """A more realistic adversarial mix: 1 forged sample (borrowed
    citation) alongside 2 genuinely valid, agreeing samples. The round
    should still be able to converge on the 2 valid samples, and the
    forged sample's numbers/text must be completely absent from what's
    persisted -- it must not get to influence the median even a little."""
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])
    direct_vm.value = 1_000_000
    contract.fund()
    direct_vm.value = 0
    direct_vm.sender = direct_alice
    contract.submit_contribution(ALICE_TEXT)
    direct_vm.sender = direct_bob
    contract.submit_contribution(BOB_TEXT)

    a, b = contract.get_state()["contributors"]

    # A forged sample that, if it counted, would drag the split toward
    # 5/95 -- but it must not count at all.
    forged_sample = {
        a: (5, "barely anything", BORROWED_FROM_BOB),
        b: (95, "did everything", "added stress tests for it"),
    }
    valid_sample_1 = {
        a: (25, "wrote docs", "fixed two broken links"),
        b: (75, "core work", "rewrote the caching layer to use LRU eviction"),
    }
    valid_sample_2 = {
        a: (23, "wrote docs", "wrote the migration guide"),
        b: (77, "core work", "added stress tests for it"),
    }

    mock_all_samples(
        direct_vm, a, b, rounds_samples=[[forged_sample, valid_sample_1, valid_sample_2]]
    )

    direct_vm.sender = direct_owner
    stage = contract.start_estimation()
    assert stage == "CONVERGED"  # the 2 genuine samples agree with each other

    report = json.loads(contract.get_settlement_report())
    assert report["rounds"][0]["valid_samples"] == 2
    persisted = contract.get_settlement_report()
    assert BORROWED_FROM_BOB not in persisted

    alice_bp = contract.get_settled_split_bp(a)
    bob_bp = contract.get_settled_split_bp(b)
    # Median of the two valid samples only (25, 23) -> nowhere near the
    # forged sample's 5 -- proving it had zero influence on the outcome.
    assert 2000 <= alice_bp <= 2600
    assert alice_bp + bob_bp == 10000
