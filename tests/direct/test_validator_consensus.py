"""
Exercises the actual validator_fn logic (not just the leader path) using
genlayer-test's `run_validator()` cheat, which replays a captured
gl.vm.run_nondet_unsafe call's validator function against fresh mocks --
simulating an independent GenVM validator re-running the same estimation
and comparing its own result to the leader's.
"""

from conftest import CONTRACT, mock_all_samples


def _hex(addr) -> str:
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + addr.hex()
    return str(addr)


ALICE_TEXT = "I wrote the docs, fixed typos, and reviewed three pull requests."
BOB_TEXT = "I built the core matching engine and wrote forty unit tests for it."


def _setup(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
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


def test_validator_accepts_a_leader_result_it_independently_reproduces(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    contract = _setup(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    leader_round = [
        {a: (25, "docs", "fixed typos"), b: (75, "core", "core matching engine")},
        {a: (22, "docs", "reviewed three pull requests"), b: (78, "core", "forty unit tests")},
        {a: (28, "docs", "wrote the docs"), b: (72, "core", "built the core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[leader_round])
    direct_vm.sender = direct_owner
    assert contract.start_estimation() == "CONVERGED"

    # A validator re-running the same estimation independently gets close
    # but not identical numbers -- still within TOLERANCE_POINTS (10).
    direct_vm.clear_mocks()
    close_round = [
        {a: (27, "docs", "fixed typos"), b: (73, "core", "core matching engine")},
        {a: (24, "docs", "reviewed three pull requests"), b: (76, "core", "forty unit tests")},
        {a: (26, "docs", "wrote the docs"), b: (74, "core", "built the core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[close_round])

    accepted = direct_vm.run_validator()
    assert accepted is True


def test_validator_rejects_a_leader_result_it_cannot_reproduce(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    contract = _setup(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    leader_round = [
        {a: (25, "docs", "fixed typos"), b: (75, "core", "core matching engine")},
        {a: (22, "docs", "reviewed three pull requests"), b: (78, "core", "forty unit tests")},
        {a: (28, "docs", "wrote the docs"), b: (72, "core", "built the core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[leader_round])
    direct_vm.sender = direct_owner
    assert contract.start_estimation() == "CONVERGED"  # leader settles ~25/75

    # This "validator" independently converges on a wildly different split
    # (~70/30) -- far outside TOLERANCE_POINTS from the leader's ~25/75.
    # A real leader proposing this would get rotated out by GenVM.
    direct_vm.clear_mocks()
    disagreeing_round = [
        {a: (68, "docs", "fixed typos"), b: (32, "core", "core matching engine")},
        {a: (72, "docs", "reviewed three pull requests"), b: (28, "core", "forty unit tests")},
        {a: (70, "docs", "wrote the docs"), b: (30, "core", "built the core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[disagreeing_round])

    accepted = direct_vm.run_validator()
    assert accepted is False


def test_validator_rejects_when_leader_reports_a_different_outcome_class(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    """If the leader claims CONVERGED but an independent validator's own
    K-sample process lands on NO_CONSENSUS (or vice versa), that is a
    disagreement on the outcome itself and must be rejected -- not silently
    reconciled."""
    contract = _setup(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob)
    a, b = contract.get_state()["contributors"]

    leader_round = [
        {a: (25, "docs", "fixed typos"), b: (75, "core", "core matching engine")},
        {a: (22, "docs", "reviewed three pull requests"), b: (78, "core", "forty unit tests")},
        {a: (28, "docs", "wrote the docs"), b: (72, "core", "built the core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[leader_round])
    direct_vm.sender = direct_owner
    assert contract.start_estimation() == "CONVERGED"

    direct_vm.clear_mocks()
    diverging_round = [
        {a: (10, "docs", "fixed typos"), b: (90, "core", "core matching engine")},
        {a: (90, "docs", "reviewed three pull requests"), b: (10, "core", "forty unit tests")},
        {a: (50, "docs", "wrote the docs"), b: (50, "core", "built the core matching engine")},
    ]
    mock_all_samples(direct_vm, a, b, rounds_samples=[diverging_round, diverging_round])

    accepted = direct_vm.run_validator()
    assert accepted is False
