"""
Direct-mode tests for the explicit size/count caps -- the Ironclad-lesson
requirement that limits be real, documented numbers that are actually
enforced, not aspirational comments.
"""

from conftest import CONTRACT


def _hex(addr) -> str:
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + addr.hex()
    return str(addr)


def test_deploy_rejects_too_few_contributors(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("at least 2 contributors"):
        direct_deploy(CONTRACT, [_hex(direct_alice)])


def test_deploy_rejects_more_than_max_contributors(direct_vm, direct_deploy, direct_owner, direct_accounts):
    direct_vm.sender = direct_owner
    too_many = [_hex(a) for a in direct_accounts[:9]]  # MAX_CONTRIBUTORS == 8
    assert len(too_many) == 9
    with direct_vm.expect_revert("at most 8 contributors"):
        direct_deploy(CONTRACT, too_many)


def test_deploy_accepts_exactly_max_contributors(direct_vm, direct_deploy, direct_owner, direct_accounts):
    direct_vm.sender = direct_owner
    exactly_max = [_hex(a) for a in direct_accounts[:8]]
    contract = direct_deploy(CONTRACT, exactly_max)
    assert len(contract.get_state()["contributors"]) == 8


def test_deploy_rejects_duplicate_contributor(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner
    dup = [_hex(direct_alice), _hex(direct_alice)]
    with direct_vm.expect_revert("duplicate contributor"):
        direct_deploy(CONTRACT, dup)


def test_submission_too_short_is_rejected(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("too short"):
        contract.submit_contribution("too short")  # < MIN_SUBMISSION_LEN (20)


def test_submission_too_long_is_rejected(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("too long"):
        contract.submit_contribution("x" * 2001)  # > MAX_SUBMISSION_LEN (2000)


def test_submission_at_exact_boundaries_is_accepted(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])

    direct_vm.sender = direct_alice
    contract.submit_contribution("y" * 20)  # exactly MIN_SUBMISSION_LEN
    assert contract.get_submission(_hex(direct_alice)) == "y" * 20

    direct_vm.sender = direct_bob
    contract.submit_contribution("z" * 2000)  # exactly MAX_SUBMISSION_LEN
    assert contract.get_submission(_hex(direct_bob)) == "z" * 2000


def test_cannot_submit_twice(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])

    direct_vm.sender = direct_alice
    contract.submit_contribution("My first and only submission goes here.")
    with direct_vm.expect_revert("already submitted"):
        contract.submit_contribution("Trying to edit my submission after the fact.")


def test_non_contributor_cannot_submit(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob, direct_charlie):
    direct_vm.sender = direct_owner
    contract = direct_deploy(CONTRACT, [_hex(direct_alice), _hex(direct_bob)])

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("not a registered contributor"):
        contract.submit_contribution("I was never invited to this split but I'll try anyway.")
