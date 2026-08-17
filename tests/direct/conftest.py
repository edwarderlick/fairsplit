import json

CONTRACT = "contracts/fairsplit.py"


def sample_response(splits: dict) -> str:
    """splits: {addr_str: (pct, justification, excerpt)}"""
    return json.dumps(
        {
            "splits": {
                a: {"pct": pct, "justification": j, "excerpt": e}
                for a, (pct, j, e) in splits.items()
            }
        }
    )


def mock_all_samples(direct_vm, addr_a, addr_b, rounds_samples):
    """Register one mock_llm rule per (round, sample) tag with a distinct
    response, so each of the internal K independent reads can be
    controlled individually -- this is what lets direct mode force
    CONVERGED or NO_CONSENSUS deterministically without a real LLM.

    rounds_samples: list of rounds; each round is a list of per-sample
    dicts {addr: (pct, justification, excerpt)}.
    """
    for round_idx, samples in enumerate(rounds_samples):
        for sample_idx, splits in enumerate(samples):
            tag = f"r{round_idx}s{sample_idx}"
            direct_vm.mock_llm(f"Sample: {tag}\n", sample_response(splits))
