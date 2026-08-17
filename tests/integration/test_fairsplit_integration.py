"""
Real end-to-end tests against a live GenVM network (studio.genlayer.com by
default, see gltest.config.yaml). Full consensus: real LLM calls, real
leader + validator agreement, real value transfers. No mocks.

studio.genlayer.com rate-limits aggressively (tens of requests/minute), so
this suite keeps to as few full round-trips as practical and pauses between
transactions rather than firing them back to back.
"""

import json
import time

from gltest import get_contract_factory, get_accounts
from gltest.assertions import tx_execution_succeeded

CONTRACT_NAME = "FairSplit"

WAIT_INTERVAL = 8000
WAIT_RETRIES = 80

ALICE_TEXT_UNEQUAL = (
    "I did a documentation pass on this project: rewrote the README's quickstart "
    "section, fixed three broken code samples, and added a troubleshooting FAQ."
)
BOB_TEXT_UNEQUAL = (
    "I designed and implemented the entire payment settlement engine from scratch: "
    "the matching algorithm, the retry/idempotency logic, and forty-two unit tests "
    "covering edge cases like partial fills and concurrent settlement."
)


def _accounts():
    owner, alice, bob, carol = get_accounts()
    return owner, alice, bob, carol


def test_converged_unequal_split_pays_out_with_verified_citations():
    """One full round trip covering CONVERGED-vs-not, the unequal/justified
    payout, the citation-verification guarantee, and the single-source-of-
    truth (Concord-lesson) property -- kept in one test to stay inside
    studio.genlayer.com's rate limits."""
    owner, alice, bob, carol = _accounts()
    factory = get_contract_factory(CONTRACT_NAME)
    contract = factory.deploy(args=[[alice.address, bob.address]], account=owner)
    print(f"DEPLOYED FairSplit (post single-source-of-truth fix) AT: {contract.address}")

    time.sleep(2)
    fund_receipt = (
        contract.connect(account=owner)
        .fund(args=[])
        .transact(value=1_000_000_000_000_000_000, wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES)
    )
    assert tx_execution_succeeded(fund_receipt)

    time.sleep(2)
    submit_a = (
        contract.connect(account=alice)
        .submit_contribution(args=[ALICE_TEXT_UNEQUAL])
        .transact(wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES)
    )
    assert tx_execution_succeeded(submit_a)

    time.sleep(2)
    submit_b = (
        contract.connect(account=bob)
        .submit_contribution(args=[BOB_TEXT_UNEQUAL])
        .transact(wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES)
    )
    assert tx_execution_succeeded(submit_b)

    time.sleep(2)
    est_receipt = (
        contract.connect(account=owner)
        .start_estimation(args=[])
        .transact(wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES)
    )
    assert tx_execution_succeeded(est_receipt)

    state = contract.get_state(args=[]).call()
    print("STATE AFTER start_estimation:", state)
    assert state["stage"] in ("CONVERGED", "NO_CONSENSUS")

    report_a = contract.get_contributor_report(args=[alice.address]).call()
    report_b = contract.get_contributor_report(args=[bob.address]).call()
    print("REPORT A:", json.dumps(report_a, indent=2)[:1500])
    print("REPORT B:", json.dumps(report_b, indent=2)[:1500])
    assert report_a["submission"] == ALICE_TEXT_UNEQUAL
    assert report_b["submission"] == BOB_TEXT_UNEQUAL
    # ProofReader-lesson guarantee: every justification the contract actually
    # stored cites real, verbatim text from that contributor's own submission.
    for j in report_a["justifications"]:
        assert j["excerpt"] in ALICE_TEXT_UNEQUAL
    for j in report_b["justifications"]:
        assert j["excerpt"] in BOB_TEXT_UNEQUAL

    if state["stage"] == "CONVERGED":
        alice_bp = contract.get_settled_split_bp(args=[alice.address]).call()
        bob_bp = contract.get_settled_split_bp(args=[bob.address]).call()
        print("alice_bp", alice_bp, "bob_bp", bob_bp)
        assert alice_bp + bob_bp == 10000
        assert bob_bp > alice_bp  # Bob's evidence clearly dwarfs Alice's

        # Concord-lesson guarantee: the paid split is derivable from the
        # stored raw samples alone, with no separately-trusted duplicate.
        recomputed = contract.recompute_settlement(args=[]).call()
        assert recomputed == {alice.address: alice_bp, bob.address: bob_bp}

        time.sleep(2)
        pay_receipt = (
            contract.connect(account=alice)
            .pay_out(args=[])
            .transact(wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES)
        )
        assert tx_execution_succeeded(pay_receipt)
        assert contract.get_state(args=[]).call()["stage"] == "PAID"
    else:
        alice_bp = contract.get_settled_split_bp(args=[alice.address]).call()
        bob_bp = contract.get_settled_split_bp(args=[bob.address]).call()
        print(
            "Real LLM run landed on NO_CONSENSUS for a clearly-unequal case "
            f"(alice_bp={alice_bp}, bob_bp={bob_bp}); reporting honestly "
            "instead of asserting a specific outcome. Equal-split fallback "
            "still applies and is payable via pay_out_fallback()."
        )
        assert alice_bp == bob_bp == 5000


def test_ambiguous_evidence_attempt_at_no_consensus():
    """A real, non-mocked attempt to provoke validator disagreement:
    contradictory, symmetric, near-identical evidence with no distinguishing
    detail is exactly the case where independent LLM reads are most likely
    to diverge by more than TOLERANCE_POINTS. This is inherently
    probabilistic against a real model -- unlike the deterministic
    direct-mode test (tests/direct/test_lifecycle.py), which proves the
    NO_CONSENSUS code path and fallback with full control. Whatever the
    real network actually decides is reported honestly below."""
    owner, alice, bob, carol = _accounts()
    factory = get_contract_factory(CONTRACT_NAME)
    contract = factory.deploy(args=[[alice.address, bob.address]], account=owner)

    ambiguous_a = (
        "I was the main driver of this feature from start to finish, handling "
        "design, implementation, and review, with no meaningful help from anyone else."
    )
    ambiguous_b = (
        "I was the main driver of this feature from start to finish, handling "
        "design, implementation, and review, essentially solo the whole way."
    )

    time.sleep(2)
    contract.connect(account=owner).fund(args=[]).transact(
        value=1_000_000_000_000_000_000, wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES
    )
    time.sleep(2)
    contract.connect(account=alice).submit_contribution(args=[ambiguous_a]).transact(
        wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES
    )
    time.sleep(2)
    contract.connect(account=bob).submit_contribution(args=[ambiguous_b]).transact(
        wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES
    )

    time.sleep(2)
    est_receipt = (
        contract.connect(account=owner)
        .start_estimation(args=[])
        .transact(wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES)
    )
    assert tx_execution_succeeded(est_receipt)

    state = contract.get_state(args=[]).call()
    report = json.loads(contract.get_settlement_report(args=[]).call())
    print("AMBIGUOUS-CASE OUTCOME:", state["stage"])
    print("ROUNDS:", json.dumps(report["rounds"], indent=2)[:3000])
    assert len(report["rounds"]) <= 2  # bounded retry policy holds either way

    if state["stage"] == "NO_CONSENSUS":
        alice_bp = contract.get_settled_split_bp(args=[alice.address]).call()
        bob_bp = contract.get_settled_split_bp(args=[bob.address]).call()
        assert alice_bp == bob_bp == 5000
        print("NO_CONSENSUS reached on real infra: independent reads diverged "
              "beyond the tolerance band; equal-split fallback applied as documented.")
    else:
        print("This particular real run still converged (both LLM reads leaned "
              "the same way on symmetric evidence) -- NO_CONSENSUS reachability "
              "is proven deterministically in tests/direct/test_lifecycle.py "
              "instead, since forcing real-model disagreement on demand isn't "
              "guaranteed run to run.")


def _run_ambiguous_case(label, text_a, text_b):
    """Shared driver for a live genuine-ambiguity attempt. Deploys fresh,
    funds, submits both contributions, runs real consensus, and prints the
    full per-round sample spread so a human (or this report) can see
    exactly how close/far the independent reads actually landed -- not just
    the final CONVERGED/NO_CONSENSUS label."""
    owner, alice, bob, carol = _accounts()
    factory = get_contract_factory(CONTRACT_NAME)
    contract = factory.deploy(args=[[alice.address, bob.address]], account=owner)

    time.sleep(2)
    contract.connect(account=owner).fund(args=[]).transact(
        value=1_000_000_000_000_000_000, wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES
    )
    time.sleep(2)
    contract.connect(account=alice).submit_contribution(args=[text_a]).transact(
        wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES
    )
    time.sleep(2)
    contract.connect(account=bob).submit_contribution(args=[text_b]).transact(
        wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES
    )

    time.sleep(2)
    est_receipt = (
        contract.connect(account=owner)
        .start_estimation(args=[])
        .transact(wait_interval=WAIT_INTERVAL, wait_retries=WAIT_RETRIES)
    )
    assert tx_execution_succeeded(est_receipt)

    state = contract.get_state(args=[]).call()
    report = json.loads(contract.get_settlement_report(args=[]).call())
    print(f"[{label}] OUTCOME:", state["stage"])
    for r in report["rounds"]:
        spread_a = [s[alice.address]["pct"] for s in r["samples"] if alice.address in s]
        spread_b = [s[bob.address]["pct"] for s in r["samples"] if bob.address in s]
        print(f"[{label}] round {r['round']}: outcome={r['outcome']} "
              f"alice_samples={spread_a} bob_samples={spread_b}")
    assert len(report["rounds"]) <= 2

    if state["stage"] == "NO_CONSENSUS":
        alice_bp = contract.get_settled_split_bp(args=[alice.address]).call()
        bob_bp = contract.get_settled_split_bp(args=[bob.address]).call()
        assert alice_bp == bob_bp == 5000
        print(f"[{label}] NO_CONSENSUS reached live: independent reads genuinely "
              "diverged beyond the tolerance band; equal-split fallback applied.")
    else:
        alice_bp = contract.get_settled_split_bp(args=[alice.address]).call()
        bob_bp = contract.get_settled_split_bp(args=[bob.address]).call()
        print(f"[{label}] Converged live at alice_bp={alice_bp}, bob_bp={bob_bp}.")
    return state["stage"], report


def test_genuinely_ambiguous_volume_vs_difficulty_tradeoff():
    """Attempt 1 at a genuinely ambiguous case. Not vague/symmetric text
    (that just gives every reader the same obvious "equal split" answer,
    which is why the earlier symmetric-claims attempt converged) -- this is
    asymmetric evidence where two defensible weighting philosophies
    (reward breadth/volume vs. reward difficulty/risk) plausibly point to
    different splits. A reasonable human panel could genuinely disagree
    here, which is the actual bar for a good NO_CONSENSUS attempt."""
    text_a = (
        "I implemented 15 new REST API endpoints covering full CRUD operations "
        "for the inventory module: create, read, update, delete, and search, "
        "each with request validation and a basic unit test. Roughly 400 lines "
        "of new code across two days of straightforward, well-understood work."
    )
    text_b = (
        "I found and fixed the root cause of a subtle race condition in the "
        "inventory update path that had been silently corrupting stock counts "
        "in production for weeks. The fix itself is about 80 lines implementing "
        "a distributed lock manager, but it took real investigation to isolate, "
        "and I included a short proof sketch showing the new algorithm is "
        "deadlock-free, since a wrong fix here would have made things worse."
    )
    _run_ambiguous_case("volume_vs_difficulty", text_a, text_b)


def test_genuinely_vague_and_incomplete_evidence_attempt_at_no_consensus():
    """Attempt 2 (a genuinely different kind of case than attempt 1). The
    first two live attempts both gave the model something concrete to
    anchor on: identical claims (-> obvious 50/50) or a clean, nameable
    volume-vs-difficulty tradeoff (-> a stable weighted answer). Both are
    "clear inputs to average," which this task's own review pointed out.
    This case is different in kind: vague, hedged, scope-incomplete
    self-reports where even the contributors themselves say they can't
    quantify their own share. There's no clean axis to weigh -- a model
    has to genuinely guess how to fill the gaps, which is where repeated
    independent reads are most likely to actually diverge rather than
    converge on a well-reasoned middle ground."""
    text_a = (
        "I helped out with the onboarding flow work over the last sprint, mostly "
        "on the frontend side I think, though it's hard to say exactly how much "
        "since a lot of it was pairing with people and quick fixes here and there. "
        "Not sure how it compares to what anyone else did on this."
    )
    text_b = (
        "I was also involved in the onboarding flow at some point, more on making "
        "sure things worked end to end I guess, but honestly a good chunk of my "
        "time that sprint went to other stuff too so I really can't say what "
        "fraction of this specific thing was me versus other people."
    )
    _run_ambiguous_case("vague_incomplete_scope", text_a, text_b)
