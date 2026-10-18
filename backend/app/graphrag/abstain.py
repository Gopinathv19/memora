"""Does an answer decline to answer? Shared by the benchmark and the graph answer step (a neutral module,
so neither imports the other)."""

import re

# The answer prompt tells the model to say the knowledge is "insufficient", and the
# no-context text says it "does not contain enough information". A paraphrase this
# misses counts as an answer, so abstention rates are a lower bound.
_ABSTAIN = re.compile(
    r"(knowledge (is|was) insufficient|insufficient (information|context|knowledge)"
    r"|does not contain (enough|sufficient) information|not enough information"
    r"|(could not|couldn't|cannot|can't|unable to) find|no (relevant )?information"
    r"|not (stated|mentioned|provided|available) in (the|your))",
    re.IGNORECASE,
)


def is_abstention(answer: str | None) -> bool:
    return bool(answer and _ABSTAIN.search(answer))
