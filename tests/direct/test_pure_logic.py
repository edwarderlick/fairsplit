"""
Unit tests for the pure convergence/citation/apportionment helpers in
contracts/fairsplit.py. These need no VM, no mocks, no deployment -- they
directly exercise the exact functions the leader, every validator, and the
on-chain recompute view all share, so this is where the tolerance rule and
the citation-verification rule (ProofReader lesson) get their most precise
coverage.
"""

import ast
import types
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "fairsplit.py"

# These six functions are the pure convergence/citation/apportionment core:
# no `self`, no `gl`, no non-determinism. We lift their real AST straight out
# of the contract file and exec it standalone -- no stubbing of the GenVM SDK
# needed, and no risk of testing a copy that could drift from the deployed
# contract.
_PURE_FUNCS = {
    "_median",
    "_validate_citation",
    "_parse_sample_response",
    "_derive_outcome",
    "_normalize_to_bp",
    "_equal_split_bp",
}
_PURE_CONSTS = {"MIN_CITATION_LEN", "MAX_JUSTIFICATION_LEN", "TOLERANCE_POINTS"}


def _load_module():
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CONTRACT_PATH))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _PURE_FUNCS:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in _PURE_CONSTS for t in node.targets
        ):
            keep.append(node)
    assert len(keep) >= len(_PURE_FUNCS), "some pure functions were not found in fairsplit.py"
    pruned = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(pruned)
    module = types.ModuleType("_fairsplit_pure")
    exec(compile(pruned, str(CONTRACT_PATH), "exec"), module.__dict__)
    return module


fairsplit = _load_module()


# --------------------------------------------------------------- citations


def test_citation_accepts_verbatim_excerpt_from_own_submission():
    submission = "Implemented the payment router and wrote 40 tests for edge cases."
    assert fairsplit._validate_citation("wrote 40 tests for edge cases", submission)


def test_citation_rejects_fabricated_text():
    submission = "Implemented the payment router and wrote 40 tests for edge cases."
    assert not fairsplit._validate_citation("rewrote the entire architecture from scratch", submission)


def test_citation_rejects_text_borrowed_from_a_different_contributor():
    alice_submission = "Wrote the README and fixed three typos in the docs."
    bob_submission = "Implemented the payment router end to end."
    # Someone tries to justify Alice's share by quoting Bob's evidence.
    assert not fairsplit._validate_citation("Implemented the payment router end to end", alice_submission)
    assert fairsplit._validate_citation("Implemented the payment router end to end", bob_submission)


def test_citation_rejects_too_short_excerpt():
    submission = "Fixed a bug in the parser."
    assert not fairsplit._validate_citation("Fixed", submission)  # below MIN_CITATION_LEN


def test_parse_sample_response_rejects_sample_with_bad_citation():
    addrs = ["alice", "bob"]
    submissions = {
        "alice": "Wrote the README and fixed three typos in the docs.",
        "bob": "Implemented the payment router end to end.",
    }
    parsed = {
        "splits": {
            "alice": {"pct": 50, "justification": "wrote docs", "excerpt": "Implemented the payment router end to end"},
            "bob": {"pct": 50, "justification": "did backend", "excerpt": "Implemented the payment router end to end"},
        }
    }
    try:
        fairsplit._parse_sample_response(parsed, addrs, submissions)
        assert False, "expected ValueError for alice's fabricated/borrowed citation"
    except ValueError as e:
        assert "alice" in str(e)


def test_parse_sample_response_accepts_valid_citations():
    addrs = ["alice", "bob"]
    submissions = {
        "alice": "Wrote the README and fixed three typos in the docs.",
        "bob": "Implemented the payment router end to end.",
    }
    parsed = {
        "splits": {
            "alice": {"pct": 20, "justification": "docs pass", "excerpt": "fixed three typos in the docs"},
            "bob": {"pct": 80, "justification": "core work", "excerpt": "Implemented the payment router end to end"},
        }
    }
    entries = fairsplit._parse_sample_response(parsed, addrs, submissions)
    assert entries["alice"]["pct"] == 20
    assert entries["bob"]["pct"] == 80


# --------------------------------------------------------------- tolerance


def test_derive_outcome_converges_within_tolerance():
    addrs = ["alice", "bob"]
    samples = [
        {"alice": 20.0, "bob": 80.0},
        {"alice": 25.0, "bob": 75.0},
        {"alice": 18.0, "bob": 82.0},
    ]
    outcome, split, _ = fairsplit._derive_outcome(samples, addrs, tolerance_points=10.0)
    assert outcome == "CONVERGED"
    assert split["alice"] == 20.0  # median
    assert split["bob"] == 80.0


def test_derive_outcome_no_consensus_beyond_tolerance():
    addrs = ["alice", "bob"]
    samples = [
        {"alice": 10.0, "bob": 90.0},
        {"alice": 50.0, "bob": 50.0},
        {"alice": 90.0, "bob": 10.0},
    ]
    outcome, split, medians = fairsplit._derive_outcome(samples, addrs, tolerance_points=10.0)
    assert outcome == "NO_CONSENSUS"
    assert split is None
    assert medians  # medians are still reported for transparency


def test_derive_outcome_needs_at_least_two_samples():
    outcome, split, _ = fairsplit._derive_outcome([{"alice": 50.0}], ["alice"], tolerance_points=10.0)
    assert outcome == "NO_CONSENSUS"
    assert split is None


# ---------------------------------------------------------- apportionment


def test_normalize_to_bp_sums_to_exactly_10000():
    pct = {"alice": 33.333, "bob": 33.333, "carol": 33.334}
    bp = fairsplit._normalize_to_bp(pct)
    assert sum(bp.values()) == 10000


def test_normalize_to_bp_preserves_large_inequality():
    pct = {"alice": 90.0, "bob": 10.0}
    bp = fairsplit._normalize_to_bp(pct)
    assert bp["alice"] > bp["bob"]
    assert sum(bp.values()) == 10000


def test_equal_split_bp_sums_to_10000_for_odd_counts():
    addrs = ["a", "b", "c"]
    bp = fairsplit._equal_split_bp(addrs)
    assert sum(bp.values()) == 10000
    # every share within 1 bp of an exact third
    for a in addrs:
        assert abs(bp[a] - 10000 / 3) <= 1


# ---------------------------------------------------- single source of truth


def test_recompute_matches_derive_outcome_given_same_raw_samples():
    """This is the Concord-lesson property at the pure-function level: the
    exact same raw samples fed through the exact same functions always
    produce the exact same split -- there is no second code path that could
    diverge."""
    addrs = ["alice", "bob"]
    samples = [
        {"alice": 30.0, "bob": 70.0},
        {"alice": 28.0, "bob": 72.0},
        {"alice": 32.0, "bob": 68.0},
    ]
    outcome1, split1, _ = fairsplit._derive_outcome(samples, addrs, tolerance_points=10.0)
    outcome2, split2, _ = fairsplit._derive_outcome(samples, addrs, tolerance_points=10.0)
    assert outcome1 == outcome2 == "CONVERGED"
    assert split1 == split2
    bp1 = fairsplit._normalize_to_bp(split1)
    bp2 = fairsplit._normalize_to_bp(split2)
    assert bp1 == bp2
