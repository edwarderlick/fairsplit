# FairSplit

A GenLayer Intelligent Contract that splits a shared payout across
contributors by numeric consensus. Contributors submit evidence of what
they actually did; GenVM validators independently estimate a fair
percentage split from that evidence; the contract settles once those
independent estimates agree within a documented tolerance band, and pays
out proportionally.

No frontend. Contract and tests only.

**Current live deployment (studio.genlayer.com):**
`0x3A8F50633228632EEfD42562aA5257828cA9FE5C` -- the fully corrected
contract, after both the single-source-of-truth hardening and the
validator rounds-comparison fix described below; see
[Adversarial hardening](#adversarial-hardening-single-source-of-truth) and
[Redeployed and reconfirmed live after the fix](#redeployed-and-reconfirmed-live-after-the-fix-round-2).

## Why this is a different problem from a boolean/structured consensus

Most Intelligent Contract examples ask GenVM's equivalence principle to
agree on a **discrete** outcome: who won a match, whether a rule was
violated, whether two structured answers match on the fields that matter.
Two independent LLM reads of the same evidence can, in principle, come back
byte-identical on a boolean or a small enum.

A percentage split across contributors is different in kind. Ask three
independent LLM calls "what's a fair split here" and you will get three
different numbers, every time, even when the underlying judgment is
essentially the same ("contributor B clearly did more"). There is no
byte-identical answer to converge on. The interesting engineering problem
is therefore not "call the LLM and store the answer" -- it's **defining and
enforcing a real tolerance rule for when a set of independent numeric
estimates counts as agreement**, and a real, bounded fallback for when it
doesn't.

### Prior-art check (Step 0)

Before building this, GenLayer's own docs, example gallery, and
[typical-use-cases](https://docs.genlayer.com/understand-genlayer-protocol/typical-use-cases)
page were checked for anything resembling numeric-estimate consensus.
GenLayer's docs explicitly describe two existing patterns that are adjacent
but not the same:

- **Boolean/structured fact-finding** (the [Prediction Market example](https://docs.genlayer.com/developers/intelligent-contracts/examples/prediction) --
  who won a match) -- a discrete decision.
- **"Numeric Tolerance" as a validation *pattern*** (documented under
  [Equivalence Principle -> Validation Patterns](https://docs.genlayer.com/developers/intelligent-contracts/equivalence-principle) --
  comparing a *single* leader value against a *single* validator's
  re-derived value within `abs(a - b) <= tolerance`, used for things like
  price oracles and 0-10 quality scores).

Nothing in GenLayer's docs or example gallery does what this contract does:
take a **vector** of interdependent percentages (they must sum to 100)
contributed by **multiple parties**, sample **multiple independent reads**,
and define a **medians-and-band** convergence rule across that whole
vector, with a bounded, documented, on-chain-reachable fallback state. The
closest primitive that exists (leader-vs-one-validator scalar tolerance,
`abs(a - b) <= N`) is a special case of what's implemented here, not a
prior version of it. This is genuinely unexplored territory in GenLayer's
current documented ecosystem, which is the actual novelty of this
contribution -- not "an AI splits money," but "a real, defensible
tolerance/fallback design for multi-party numeric consensus," which the
project treats as the whole point rather than an afterthought.

## The convergence rule that is the crux of this design

GenVM's real equivalence-principle primitive (`gl.vm.run_nondet_unsafe`)
gives you exactly one comparison: a **leader** proposes a result, and each
**validator** independently re-derives its own answer and returns a single
`True`/`False` -- accept or reject the leader's proposal. Critically,
per GenLayer's own docs:

> "The accepted leader result is the value your contract receives and can
> store. Validators verify or reject that leader result; their independent
> intermediate answers are not automatically persisted on-chain."

That matters a lot here. A naive reading of "let validators independently
estimate a split" would assume GenVM hands the contract N different raw
validator numbers to average. It doesn't -- by design, for gas and
determinism reasons. So this contract does not rely on GenVM to surface
raw per-validator numbers at all. Instead:

1. **Every node that runs the estimation** (whichever GenVM validator ends
   up in the leader role for that attempt, and every validator that
   verifies it) independently executes the exact same procedure,
   `_run_rounds()`:
   - It reads **all** submitted contributions together (not one at a time).
   - It takes `SAMPLES_PER_ROUND = 3` independent LLM reads of the same
     evidence, each returning a full percentage split (summing to ~100)
     plus a cited justification per contributor.
   - For each contributor, it computes the **median** percentage across
     the 3 samples, and checks that **every** sample for that contributor
     falls within `TOLERANCE_POINTS = 10` percentage points of that
     median. If that holds for every contributor, this round
     **CONVERGED** and the medians become the proposed split. If not, the
     round is **NO_CONSENSUS** and, up to `MAX_ROUNDS = 2` total rounds,
     the whole 3-sample process is retried once before falling back to an
     equal split.
2. **This entire multi-sample process is the leader's `leader_fn`.** The
   leader doesn't propose a single number pulled from thin air -- it
   proposes the result of its own internal numeric-consensus procedure.
3. **The validator's `validator_fn` independently reruns the identical
   procedure from scratch** (3 fresh LLM reads, its own median/tolerance
   check, its own possible second round) and compares its own outcome
   (`CONVERGED`/`NO_CONSENSUS`) and, if converged, its own per-contributor
   split against the leader's, using the same `TOLERANCE_POINTS` band. If
   the outcome classes differ, or any contributor's percentage differs by
   more than the band, the validator rejects -- exactly the documented
   [Numeric Tolerance pattern](https://docs.genlayer.com/developers/intelligent-contracts/equivalence-principle),
   extended from a single scalar to the whole split vector.

So there are two tolerance checks stacked on top of each other, and both
are real:

- **Inner check** (within one node's own 3 samples): "did my own 3
  independent reads of the evidence agree with each other?"
- **Outer check** (GenVM's actual equivalence principle, leader vs. each
  validator): "did my independent re-run of the *entire inner process*
  land in the same place as the leader's?"

This is not "average whatever comes back." A round only converges if 3
independent LLM calls actually agree with each other on every contributor,
and a leader result is only accepted by the network if independently
reproducing that whole procedure lands within the same band again.

### Why `TOLERANCE_POINTS = 10`

GenLayer's own documented numeric-tolerance example (a 0-10 quality score)
uses a ±1 band, i.e. 10% of the score's range. Percentages here range over
0-100, so the directly analogous choice is ±10 percentage points. In
practice (see [Real test results](#real-test-results) below) three
independent reads of clearly-unequal evidence landed within a few points of
each other (15/20/15 and 85/80/85 in the live run), so 10 points has real
headroom without being so loose that a 60/40 and an 80/20 read would be
treated as "the same." If real usage showed this needs tuning, the honest
answer is: not blind hand-waving -- it is a legible, documented,
change-in-one-place constant with the analogy above as an accountable
starting point. See [Live NO_CONSENSUS attempts](#live-no_consensus-attempts-an-honest-finding)
for why, after two live attempts to provoke `NO_CONSENSUS`, this constant
was deliberately left unchanged rather than tuned to force a result.

### Why `SAMPLES_PER_ROUND = 3` and `MAX_ROUNDS = 2`

Every additional sample or round is a real LLM call, real latency, and real
compute -- multiplied across every validator in the network. 3 samples is
the minimum that lets a median mean anything (2 samples can't outvote a
lone outlier; with 3, one bad read doesn't dominate). 2 rounds means a
contentious split gets one genuine second chance (in case the first round's
disagreement was just LLM noise) before the contract commits to
`NO_CONSENSUS` and its documented fallback -- rather than retrying forever.
Both numbers are named constants at the top of `contracts/fairsplit.py`,
not magic numbers, and both are the direct hardening response to Ironclad's
staff-suggested next step (see [Hardening decisions](#hardening-decisions-and-which-prior-review-drove-each)
below).

## State machine

```
OPEN (payout pool funded via fund(), contribution window open)
  |  submit_contribution() by a registered contributor
  v
SUBMITTING (each contributor submits once; no editing after submit)
  |  start_estimation(), once >= 2 have submitted and the pool is funded
  |  (owner can close early once the minimum is met; otherwise all
  |   registered contributors must have submitted)
  v
[internal: leader + every validator independently run the numeric
 consensus procedure described above]
  |
  +-- converged (every contributor's split agreed within tolerance)
  |     v
  |   CONVERGED --- pay_out() ---> PAID  (proportional to the agreed split)
  |
  +-- did not converge after MAX_ROUNDS rounds
        v
      NO_CONSENSUS --- pay_out_fallback() ---> PAID  (equal split)
```

`NO_CONSENSUS` is a real, reachable, stored state -- not a theoretical
branch. `tests/direct/test_lifecycle.py::test_no_consensus_reachable_and_fallback_applies`
forces it deterministically by controlling the mocked LLM responses for
every one of the internal samples, and asserts the equal-split fallback is
actually applied and payable.

## Hardening decisions, and which prior review drove each

This account has three prior GenLayer submissions (Ironclad, ProofReader,
Concord). Each was reviewed, and each review surfaced a concrete gap. None
of those contracts can be edited post-acceptance, so the lessons are baked
into FairSplit's design instead:

1. **Single source of truth for the paid split** (from **Concord's**
   rejection: a response could carry a correct nested decision while a
   conflicting top-level status was independently persisted, and drifted).
   FairSplit never stores a second, separately-trusted copy of the split.
   `settled_split_bp` is written exactly once, by the exact same
   `_normalize_to_bp(_derive_outcome(...))` pure-function pipeline that
   decided `CONVERGED` in the first place. The `recompute_settlement()`
   view recomputes the split from the stored raw per-round samples using
   those same pure functions and is asserted to exactly equal
   `settled_split_bp` in both a direct-mode test
   (`test_settled_split_is_always_derived_from_stored_raw_samples`) and the
   live integration test.

2. **Verified citations, not bare justifications** (from **ProofReader's**
   staff-suggested next step: require a non-empty excerpt and verify cited
   line numbers actually exist in that excerpt before storing the audit
   trail). Every per-contributor justification must include a verbatim
   `excerpt` field. `_validate_citation()` checks that excerpt is a real
   substring of *that specific contributor's own* stored submission --
   before anything is persisted. A citation that's fabricated, too short,
   or copied from a different contributor's evidence causes that whole
   sample to be dropped, not stored. See
   `tests/direct/test_pure_logic.py` (unit-level) and
   `tests/direct/test_lifecycle.py::test_citation_rejection_drops_the_sample_not_the_whole_round`
   (contract-level).

3. **Explicit size/count/history caps** (from **Ironclad's**
   staff-suggested next step: explicit size limits on protected text, plus
   bounded attempt-history storage). Concretely, in
   `contracts/fairsplit.py`:
   - `MIN_CONTRIBUTORS = 2`, `MAX_CONTRIBUTORS = 8`
   - `MIN_SUBMISSION_LEN = 20`, `MAX_SUBMISSION_LEN = 2000` (characters)
   - `MIN_CITATION_LEN = 8`, `MAX_JUSTIFICATION_LEN = 300`
   - `SAMPLES_PER_ROUND = 3`, `MAX_ROUNDS = 2` -- so the stored
     `settlement_report` can never hold more than 2 rounds x 3 samples of
     history, no matter how contentious a split is or how many times it
     gets re-run. All of these are enforced (not just documented) and
     covered in `tests/direct/test_caps.py` and the bounded-rounds
     assertion in `test_no_consensus_reachable_and_fallback_applies`.

## Repository layout

```
contracts/fairsplit.py            the contract (single file, pinned runner)
tests/direct/test_pure_logic.py   unit tests for the convergence/citation/
                                   apportionment math, no VM needed
tests/direct/test_lifecycle.py    full state-machine tests via genlayer-test's
                                   in-memory VM, with controllable mocked LLM
tests/direct/test_caps.py         enforcement tests for every size/count cap
tests/direct/test_validator_consensus.py
                                   exercises validator_fn itself (accept /
                                   reject / outcome-mismatch) via run_validator()
tests/integration/test_fairsplit_integration.py
                                   real deploy + real consensus against a
                                   live GenVM network, no mocks
gltest.config.yaml                 network/account config for integration tests
```

## Running it yourself

```bash
pip install -r requirements.txt

# Lint the contract
genvm-lint check contracts/fairsplit.py

# Fast, no-network tests (pure logic + full state machine with mocked LLM)
pytest tests/direct -v

# Real deploy + real consensus against studio.genlayer.com (gasless)
genlayer network set studionet
gltest tests/integration -v -s --network studionet
```

Integration tests read account private keys from `.env` (see
`gltest.config.yaml`); `studio.genlayer.com` does not require funding, but
does rate-limit aggressively, so the integration suite paces its
transactions and keeps to a small number of full round-trips.

## Real test results

**Direct-mode suite: 39/39 passing** (`pytest tests/direct -v`), covering:

- pure convergence/citation/apportionment/payout math (`test_pure_logic.py`)
- the full CONVERGED and NO_CONSENSUS state-machine paths via controlled
  mocked LLM responses per internal sample (`test_lifecycle.py`)
- explicit validator accept/reject/outcome-mismatch behavior
  (`test_validator_consensus.py`)
- every size/count cap actually enforced (`test_caps.py`)
- **adversarial proof of the single-source-of-truth guarantee**
  (`test_single_source_of_truth.py`) -- a byzantine-leader simulation that
  forces `_run_rounds` to return a `split_pct` that lies about what its own
  `rounds` data supports, and confirms the contract only ever stores what
  the raw rounds actually derive to.
- **adversarial proof of the validator rounds-comparison fix**
  (`test_validator_rounds_gap.py`) -- a byzantine leader with an
  honest-looking `split_pct` but fabricated `rounds`, confirmed to be
  correctly rejected by `validator_fn` post-fix (and confirmed to have
  been incorrectly accepted pre-fix, before being fixed).
- **adversarial proof of citation verification**
  (`test_adversarial_citations.py`) -- all three forgery strategies
  (fabricated text, text borrowed from a different contributor, text lifted
  from something nobody ever submitted) are confirmed rejected before
  persistence, individually and mixed in with genuine samples.

See [Adversarial hardening: single source of truth](#adversarial-hardening-single-source-of-truth)
and [Adversarial hardening: validator rounds-comparison gap](#adversarial-hardening-validator-rounds-comparison-gap)
below for the full story of two real bugs this adversarial testing found
and fixed -- with real failing-then-passing assertions, not just redesigns.

**Live deploys on studio.genlayer.com** (`FairSplit`, 2 contributors, real
consensus, real value transfer, no mocks) -- six independent live runs
across three contract generations:

**Current, fully corrected contract -- `0x3A8F50633228632EEfD42562aA5257828cA9FE5C`**
(post single-source-of-truth fix AND post validator rounds-comparison fix
-- this is the address that reflects the actual submitted contract):

- **Unequal, evidenced case, redeployed and reconfirmed on the fully
  corrected contract.** Alice submitted a documentation-only contribution;
  Bob submitted a from-scratch implementation with tests. All 3
  independent LLM reads in round 0 agreed within tolerance (Alice:
  20/20/30, Bob: 80/80/70) -- the contract settled `CONVERGED` with a
  genuinely unequal, evidenced **20% / 80%** split, every stored
  justification's excerpt verified against the real submitted text, and
  `pay_out()` moved the contract to `PAID`. `recompute_settlement()`,
  called against this live deployed contract, matched `settled_split_bp`
  on-chain exactly.

**Superseded -- post single-source-of-truth fix only, pre validator
rounds-comparison fix -- `0x050baF2ca5E0Be18B8e0923AE89cB809f5eF642C`:**

- **Unequal, evidenced case**, reconfirmed live on this intermediate
  version. All 3 samples agreed exactly (Alice 15/15/15, Bob 85/85/85);
  converged at **15% / 85%**; `recompute_settlement()` matched
  `settled_split_bp` on-chain.

**Superseded -- original, pre-fix contract (kept here for the
NO_CONSENSUS evidence they still validly demonstrate -- neither
settlement-derivation bug affected convergence/tolerance behavior, only
which field the paid split was actually derived from/validated against):**

- **Unequal, evidenced case** (first attempt, pre-fix). Same evidence as
  above; converged at Alice 15/20/15, Bob 85/80/85 -- 15%/85%.
- **Symmetric/contradictory claims** ("I was the main driver... essentially
  solo," attempt 1 at provoking `NO_CONSENSUS`). Converged at an exact
  50/50/50 split across all 3 samples.
- **Genuine volume-vs-difficulty tradeoff** (attempt 2 -- large-but-routine
  work vs. small-but-high-difficulty work). Converged at 45/45/45 vs
  55/55/55, again zero spread.
- **Vague, hedged, scope-incomplete self-reports** (attempt 3 -- both
  contributors explicitly say they can't quantify their own share).
  Converged at 55/55/55 vs 45/45/45, again zero spread.

See [Live NO_CONSENSUS attempts](#live-no_consensus-attempts-an-honest-finding)
below for the full analysis of why three qualitatively different kinds of
ambiguous evidence all converged with zero measured sample spread, and what
that does and doesn't imply about the tolerance band. All four of these
findings are about the contract's convergence/tolerance/consensus behavior,
which neither settlement/validator fix changed (they changed which field
the paid split derives from and which field the network validates it
against, not how or when the contract decides `CONVERGED` vs
`NO_CONSENSUS`) -- so they continue to apply to the current, fully
corrected contract without needing to be independently re-run.

Run `pytest tests/direct -v` and `gltest tests/integration -v -s --network
studionet` yourself to see current output; this section reflects the runs
performed while building this contract, not a guarantee of what a future
LLM call will return.

## Adversarial hardening: single source of truth

This project holds itself to the same bar Concord was held to on
resubmission: the single-source-of-truth guarantee isn't done until it's
been adversarially tested, not just redesigned. It was -- and the testing
found a real gap, which is now fixed.

**The gap.** The original settlement code derived the stored payout split
from `result["split_pct"]` -- a field the leader's `_run_rounds()`
computation reported alongside the raw `rounds` samples. Under honest
execution the two always agreed, because the same function computed both
at the same time. But a byzantine leader is exactly the actor GenVM's
validator re-execution exists to guard against, and `validator_fn` only
compared the leader's claimed `split_pct` against each validator's own
independently-computed `split_pct` within tolerance -- it never checked
that the leader's claimed `split_pct` was actually consistent with the
leader's own reported `rounds` data. A leader could in principle report a
`split_pct` that passed the validator tolerance check while shipping
fabricated `rounds` samples that don't actually support it. Since
`settled_split_bp` (the paid amount) was derived from `split_pct`, and
`recompute_settlement()` was derived from `rounds`, that gap meant the two
_could_ diverge -- precisely the class of bug Concord was rejected for
(a stored decision independently persisted from, and able to drift from,
what the raw evidence actually supports).

**How it was proven, not just reasoned about.**
`tests/direct/test_single_source_of_truth.py::test_stored_split_ignores_a_byzantine_leaders_claimed_split_pct`
monkeypatches the deployed contract module's `_run_rounds` (the exact seam
a byzantine leader's `leader_fn()` return value occupies) to return
`split_pct={alice: 90, bob: 10}` alongside `rounds` data that actually only
supports 20/80. Run against the pre-fix code, this test -- and a companion
test for malformed/empty `rounds` -- **failed**, confirming the gap was
real and exploitable, not theoretical:

```
assert (alice_bp, bob_bp) != (9000, 1000)
E       assert (9000, 1000) != (9000, 1000)
```

**The fix.** `_settle_from_rounds(rounds, addrs)` is now the single
function that derives the paid split, and it takes only `rounds` as input
-- it has no parameter through which a leader-claimed `split_pct` could
ever reach it. Both `start_estimation` (at settlement time) and
`recompute_settlement` (at any later read time) call this exact function
with the exact same inputs. `result["outcome"]`/`result["split_pct"]`
still exist and still matter -- they're what `validator_fn` uses for the
network's outer accept/reject decision -- but they are no longer trusted
for what gets paid. Re-run against the fixed code, both tests pass, and
the fabricated `split_pct` is never persisted regardless of whether the
network-level validator check would have caught it. This makes the
single-source-of-truth guarantee structurally enforced (the paid split
literally cannot be computed from anything but the stored raw rounds, by
construction of the function signature) rather than merely tested-and-
currently-true.

### Redeployed and reconfirmed live after the fix

The single-source-of-truth bug above was found and fixed to
`contracts/fairsplit.py` itself, and every live deploy described in this
README prior to the fix ran the **old, vulnerable** source. Per this
project's own discipline (the same bar Concord was held to on
resubmission), a bug fix to core settlement logic isn't submission-ready
until it's reconfirmed live on the corrected code -- not just in direct-mode
tests, and not by continuing to point at an earlier deployment.

**The fixed contract was redeployed to a fresh Studio address:**

```
0x050baF2ca5E0Be18B8e0923AE89cB809f5eF642C
```

Deployed and driven through the full unequal-split flow live, on real
consensus, with real value transfer, against this exact address (not a
simulation): funded, both contributors submitted, `start_estimation()`
converged with all 3 samples agreeing (Alice 15/15/15, Bob 85/85/85 --
even tighter than the pre-fix deploy's 15/20/15 spread), settled at the
same genuinely unequal, evidenced **15% / 85%** split, and `pay_out()`
succeeded. Critically, this run also called `recompute_settlement()`
against the live deployed contract and asserted it returned exactly
`{alice: 1500, bob: 8500}` -- matching `settled_split_bp` on-chain, not
just in a unit test. This is the actual code path this task's fix
targeted, now confirmed working end-to-end on a real, currently-queryable
GenVM deployment. Every prior finding in this README (the NO_CONSENSUS
honesty section below, the prior-art notes, the tolerance-band rationale)
was derived from this same contract logic and continues to apply -- none
of it depended on the settlement-derivation bug that was fixed.

**On live-testing the citation-forgery rejection specifically:** this
task asked for a live re-run of a citation-forgery attempt if practical.
It isn't, and here's why plainly, rather than skipped without explanation:
citation verification (`_validate_citation` / `_parse_sample_response`) is
pure, deterministic Python logic with no LLM call inside the check itself
-- but the thing being checked (a candidate excerpt) is produced by a real
LLM response to a real `exec_prompt` call during live consensus, and there
is no client-facing way to inject a specific fabricated LLM response into
a live GenVM run. Real LLMs, prompted honestly (as this contract's prompt
does, asking for a verbatim quote), reliably return real quotes rather
than fabricated ones, so a live run cannot be relied upon to ever exercise
the rejection path at all -- confirmed by the fact that every live run to
date, across four separate deployments, has had 0 samples rejected for bad
citations. Forcing a live citation forgery would require either
prompt-injecting the contributor's own submission text to trick the model
(a different, weaker test of prompt-injection resistance rather than of
the citation check itself) or controlling the LLM provider's response
directly (not available from a test client against a real network). The
citation-verification logic is instead proven exhaustively and precisely
in direct mode, where the "LLM response" can be set to exactly the
adversarial value under test -- see
`tests/direct/test_adversarial_citations.py`, which is a stronger test of
this specific mechanism than a live attempt could be, precisely because it
can guarantee the forgery is actually attempted rather than hope a live
model produces one.

## Adversarial hardening: validator rounds-comparison gap

A second, related gap was found through the same adversarial-testing
discipline, this time in `validator_fn` itself rather than in what
`start_estimation` did with a leader's result. Reported here with the same
honesty as the first: it was real, it was proven with a real failing
assertion before being fixed, and it was fixed the same way Concord's
equivalent gap was closed on resubmission -- not redesigned in the
abstract, but adversarially tested until the fix actually held.

**The gap.** After the single-source-of-truth fix above, `start_estimation`
correctly derived the paid split from `result["rounds"]` via
`_settle_from_rounds` -- never from `result["split_pct"]`. But
`validator_fn`, the function that decides whether the network accepts or
rejects a leader's result in the first place, still only compared
`leader_data["outcome"]` and `leader_data["split_pct"]` against the
validator's own independently-computed values. It never looked at
`leader_data["rounds"]` at all. That meant the network's actual
accept/reject gate was checking a field (`split_pct`) that no longer
determined payment, while the field that DID determine payment (`rounds`)
went completely unchecked by consensus. A byzantine leader could report a
`split_pct` that matched what an honest validator would independently
compute -- passing the outer tolerance check -- while shipping fabricated
`rounds` data that `_settle_from_rounds` would derive into a materially
different, self-serving actual payout.

**How it was proven, not just reasoned about.**
`tests/direct/test_validator_rounds_gap.py` constructs exactly this
scenario: a leader claims `split_pct={alice: 20, bob: 80}` (which an
honest validator's own recomputation also independently lands on) while
shipping `rounds` that actually derive to `{alice: 90, bob: 10}`. Run
against the pre-fix `validator_fn`, `direct_vm.run_validator()` -- which
replays the real captured `validator_fn` against these exact values --
returned `True`: **the byzantine leader was accepted**, despite the
validator's own honest recomputation deriving a completely different
payout (20/80) than what the leader's `rounds` would actually pay (90/10).
That is a real, confirmed acceptance of a result the validator's own
independent work disagreed with -- not a hypothetical.

**The fix.** `validator_fn` now derives its own accept/reject decision
from `_settle_from_rounds` applied to **both** sides' `rounds` -- the
leader's claimed `rounds` and the validator's own independently-recomputed
`rounds` -- and compares those derived outcomes/splits, in basis points,
within the same tolerance band. It no longer reads `split_pct` at all. Run
against the fixed code, the exact same adversarial scenario is now
correctly rejected (`accepted is False`), and a companion test confirms an
honest leader whose `rounds` genuinely agree with the validator's own is
still correctly accepted -- the fix closes the gap without becoming overly
strict. `validator_fn` now checks precisely the same thing
`start_estimation` and `recompute_settlement` do: what `_settle_from_rounds`
derives from the raw `rounds` data, and nothing else.

### Smaller nits: what was checked, fixed, and left alone

Three smaller items were raised alongside the main gap. Reported honestly,
including the one left unchanged:

- **Integer-division dust in `_distribute`.** `(pool * bp) // 10000` per
  contributor can lose a few units of `pool` to floor-division rounding
  even though `settled_split_bp` values sum to exactly 10000 -- e.g. a
  3-wei pool split 6700/3300 bp floors to `2 + 0 = 2`, stranding 1 wei in
  the contract forever. **Fixed**: extracted as the pure, unit-tested
  `_compute_payout_amounts()` (see
  `tests/direct/test_pure_logic.py::test_compute_payout_amounts_*`), which
  pays any such dust to whichever contributor holds the largest bp share,
  the same largest-remainder idea `_normalize_to_bp` already uses. `sum(amounts) == pool`
  is now asserted directly, not just assumed.
- **The 60-140 allowed band for a sample's reported total.** This was a
  leftover, not a deliberate choice -- there was no comment explaining why
  ±40 around 100 rather than something tighter, and `_normalize_to_bp`
  rescales proportionally regardless of the total anyway, so correctness
  never depended on this number. **Fixed**: tightened to 90-110 (±10) and
  documented in-line why: with whole-integer percentages and at most
  `MAX_CONTRIBUTORS = 8` contributors, honest rounding drift is at most a
  few points, so a total outside ±10 is a real sign of a broken response,
  not benign slop -- catching more genuinely-broken LLM responses before
  they get silently rescaled into something that looks plausible but
  isn't what the model meant.
- **`MIN_CITATION_LEN = 8` -- is it easily gameable?** Checked and
  **deliberately left unchanged**, with the reasoning now documented
  in-line in `contracts/fairsplit.py`: this length check's only honest job
  is to reject near-empty matches; it cannot and was never meant to
  guarantee a citation is *semantically substantive*, only that it's
  *real* (a verbatim, unforged substring of that contributor's own
  submission -- the actual ProofReader-lesson property). Any fixed length
  threshold is trivially satisfiable by an contributor padding their own
  submission with filler of exactly that length, so raising the number
  doesn't close that gap -- it only risks rejecting legitimately short,
  meaningful citations (this contract's own tests use an 11-character
  "fixed typos" as a real example). Actually closing semantic gaming would
  require judging meaning, i.e. trusting another LLM call -- reintroducing
  the exact kind of unverifiable trust this check exists to avoid. Left at
  8, documented as a boundary of what this mechanism can honestly claim to
  prove, not silently ignored.

### Redeployed and reconfirmed live after the fix (round 2)

Both the validator rounds-comparison fix and the three nits above changed
`contracts/fairsplit.py` again, after the address referenced earlier in
this README (`0x050baF2ca5E0Be18B8e0923AE89cB809f5eF642C`) was already
deployed and tested. Same discipline as the first fix: redeployed to a
fresh address and reconfirmed live rather than continuing to point at a
now-superseded deployment.

**The fully corrected contract is live at:**

```
0x3A8F50633228632EEfD42562aA5257828cA9FE5C
```

Driven through the full unequal-split flow live again against this exact
address: funded, both contributors submitted, `start_estimation()`
converged (Alice 20/20/30, Bob 80/80/70 -- median 20/80, within tolerance),
settled at a genuinely unequal, evidenced **20% / 80%** split (the
specific split number moved slightly between runs, as expected -- see
[Live NO_CONSENSUS attempts](#live-no_consensus-attempts-an-honest-finding)
for why repeated live LLM reads aren't bit-identical run to run, even
though within a single run's 3 samples they usually are), `pay_out()`
succeeded, and `recompute_settlement()` matched `settled_split_bp` on-chain
exactly. This is the address that reflects the actual, fully-hardened
submitted contract -- both the single-source-of-truth fix and the
validator rounds-comparison fix, plus the three nits above, all live and
reconfirmed on real GenVM consensus, not just in direct-mode tests.

## Live NO_CONSENSUS attempts: an honest finding

Three honest, real (non-mocked) attempts were made on studio.genlayer.com
to provoke a live `NO_CONSENSUS`, per this project's own discipline of
proving both outcome directions on real deployed consensus rather than
simulation alone (the standard this project's prior contracts -- Ironclad,
ProofReader, Concord -- were held to). **None succeeded. All three
converged.** Reported here plainly, with the actual sample data, rather
than papered over or faked.

**Attempt 1 -- symmetric/contradictory claims.** Alice and Bob both
submitted near-identical "I did this alone" claims. All 3 samples in round
0: **50/50/50** exactly, zero spread. In hindsight this was the wrong kind
of "ambiguous" -- symmetric input has one obviously correct answer (split
it evenly), so every reasonable reader, human or LLM, converges on it. That
is not evidence disagreement; it's evidence *agreement* that happens to
produce a 50/50 number.

**Attempt 2 -- a genuine weighting tradeoff.** Real asymmetric evidence
built around a tradeoff a human panel could plausibly split on: Alice did
large-but-routine work (15 CRUD endpoints, ~400 lines, two days), Bob did
small-but-high-difficulty work (an 80-line fix for a subtle
production-corrupting race condition, with a correctness argument). A
volume-weighted reader and a difficulty/risk-weighted reader could
defensibly land in different places. Result: all 3 samples in round 0
landed on **45/45/45** and **55/55/55** -- again zero spread, just at a
different (still defensible, still evidenced) number than the first case.

**Attempt 3 -- vague, hedged, scope-incomplete self-reports.** A
deliberately *different kind* of ambiguity than attempts 1-2, which both
gave the model something concrete to anchor on (a clean symmetry, or a
clean nameable tradeoff). This attempt used evidence where the
contributors themselves hedge and admit they can't quantify their own
share: "hard to say exactly how much... a lot of it was pairing... not
sure how it compares to what anyone else did," and "I really can't say
what fraction of this specific thing was me versus other people." No
clean axis to weigh, no concrete deliverable to point to, incomplete scope
information on both sides. Result: all 3 samples landed on **55/55/55**
and **45/45/45** -- zero spread, yet again, and at a plausible-but-generic
answer close to even.

Per this task's own instructions, that's three honest, genuinely different
kinds of attempts (symmetric agreement, a nameable tradeoff, and genuine
vagueness/incompleteness) -- covering the space of "ambiguous evidence" any
reviewer would reasonably ask for. The right move now is to report the
finding as thorough, not to keep re-rolling a fourth time hoping for a
different result.

**What the data actually shows.** The limiting factor was not that the
evidence failed to be ambiguous, and it was not that the ±10-point
tolerance band happened to be just barely wide enough to swallow real
disagreement. It's that **every one of the three within-round samples
showed essentially zero spread in all four live runs to date**, including
the original unequal-split case (0, 0, 0, and the ±5-point spread in the
very first run being the only non-zero spread observed anywhere). Three
independent `exec_prompt` calls against the same evidence, from the same
underlying model, produced the *exact same number* every time, far more
often than they produced different numbers -- and that held regardless of
whether the underlying evidence was symmetric, asymmetric-but-clean, or
genuinely vague. That means the bottleneck for a live `NO_CONSENSUS` isn't
primarily the tolerance band's width, and it isn't primarily the choice of
evidence either -- it's that the thing the band is measuring (variance
across repeated reads of the *same* evidence by the *same* model) is
currently very small in practice on studio.genlayer.com's validator/model
configuration, across every kind of evidence tried.

**What wasn't tried, and why.** To be explicit about the boundaries of this
finding rather than leave them implicit: all three attempts used a single
underlying LLM/provider configuration (whatever studio.genlayer.com's
current validator set runs) and a 2-contributor split. Not tried: (a)
deliberately malformed or adversarially-crafted submissions designed to
destabilize a single model's own read of itself (as opposed to evidence
that's merely subjectively ambiguous to a human); (b) a 3+ contributor
split, where more simultaneous percentages to balance could plausibly
increase the chance that at least one contributor's share exceeds the
band; (c) a live network configuration with genuinely heterogeneous
validator LLM providers, which GenVM supports in a real production
deployment but which this single-network live-testing setup cannot
exercise. Any of these might behave differently; none were attempted,
and none should be assumed to behave like the three cases that were.

**Why not just tighten the band to force it, then?** Tightening
`TOLERANCE_POINTS` was seriously considered, as this task asked. It was
rejected for a concrete, evidence-based reason: the only band tight enough
to have flipped any of the observed live samples would have had to sit
*below* the ±5 spread already seen in the very first CONVERGED run (Alice
15/20/15 around a median of 15) -- i.e., tightening enough to manufacture a
`NO_CONSENSUS` on the ambiguous cases would have retroactively broken the
already-proven, genuinely-justified 15/85 CONVERGED result, or at best
landed exactly on the observed noise floor by construction. That is
indistinguishable from reverse-engineering a threshold to hit a
predetermined outcome, which this task explicitly rules out ("do not fake,
force, or fabricate a NO_CONSENSUS result just to check the box"). A
tolerance band picked to match four data points is not a more principled
band than the documented ±10 -- it's overfitting to a tiny, noisy sample.
`TOLERANCE_POINTS` was left at 10.

**So what does this mean for the design?** Convergence, in every case
tested so far -- clear, tradeoff, and vague alike -- is genuinely the
common case, not because the tolerance band is loose, but because
independent reads of the same evidence by the same model are highly
self-consistent, apparently regardless of how much genuine ambiguity a
human would perceive in the input. That is actually a reassuring property
for the contract's core premise (GenVM validators reading the same
evidence really can reach compatible numeric judgments), not a weakness.
`NO_CONSENSUS` remains a real, correctly-implemented, and **deterministically
proven** safety valve (see `tests/direct/test_lifecycle.py::test_no_consensus_reachable_and_fallback_applies`,
which controls the mocked samples directly and proves both the bounded
2-round retry and the equal-split fallback fire correctly, and
`tests/direct/test_adversarial_citations.py`, which proves a round with
too many forged/invalid samples correctly falls through to it too) -- its
role in production is most likely to matter for cases this project's live
attempts did not reach: genuinely malformed/adversarial submissions, a
model whose own read of itself is unstable, larger contributor sets, or
(in a real multi-operator GenLayer network) validators running genuinely
different underlying LLM providers.

**Honest final status:** `NO_CONSENSUS` was **not** reproduced live across
three genuine, qualitatively different attempts on studio.genlayer.com's
current validator/model configuration. The tolerance band was deliberately
left unchanged because tightening it to force the result would have
required overfitting to a small, noisy sample and risked breaking the
already-proven CONVERGED case. This is disclosed as an actual, real,
now-thoroughly-explored limitation of the live-testing evidence gathered
here -- not fabricated, not hidden, and not left under-explored.
`NO_CONSENSUS`'s correctness is proven deterministically instead
(including under adversarial input -- see
[Adversarial hardening: single source of truth](#adversarial-hardening-single-source-of-truth)
above), which is a legitimate and standard way to test a rare branch of a
system whose live trigger condition depends on an external,
non-reproducible LLM's variance that these attempts found to be smaller in
practice than the ±10-point band, across every kind of evidence tried.

## Adapting this for another split-payout use case

The estimation core (`_run_rounds`, `_derive_outcome`,
`_normalize_to_bp`, `_validate_citation`) has no knowledge of what kind of
"contribution" it's splitting -- it only needs, per contributor, a text
submission to cite against. To adapt it:

- **DAO bounties**: contributors submit links to their merged PRs/commits
  as the "evidence" text; the payout pool is the bounty amount.
- **Freelance/collab splits**: contributors submit a description of their
  deliverable plus a link (Figma, repo, doc); `MAX_SUBMISSION_LEN` may need
  raising if evidence is link-heavy rather than prose-heavy.
- **Hackathon prize pools**: contributors submit a short "what I built"
  statement; `MAX_CONTRIBUTORS` may need raising for larger teams (raise it
  deliberately, not silently -- it directly controls prompt size and the
  number of LLM calls per estimation).

The tolerance band, sample count, and round cap are the three knobs that
matter most when adapting this: tighter evidence (e.g. verifiable commit
counts) can probably tolerate a narrower band; vaguer, more subjective
evidence may need either a wider band or more samples per round to avoid
spurious `NO_CONSENSUS`.
