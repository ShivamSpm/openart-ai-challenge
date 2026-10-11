# Director setting and assessment assumptions

The requested component chooses context before one Director turn. A user can explore character or scene branches, return to an earlier revision, and merge work. Rejected facts must not become authoritative merely because their cards were recently written. Some decisions require earlier facts, exclusive groups represent alternatives, and additional pairwise contradictions can cross those groups.

For this work sample, a hypothetical upstream extractor supplies cards, revision links, requirements, conflict groups, contradiction pairs and coverage metadata. A hypothetical product owner supplies feature weights and a tokenizer supplies token costs. These are explicit assessment assumptions, not statements that OpenArt already operates this extractor, branch graph, contradiction service or retrieval pipeline.

Consume the supplied metadata. Do not call an LLM to infer contradictions, rewrite card text, invent replacement requirements, change importance weights or silently repair invalid requests. A pair represents an incompatibility between those exact records; another version does not inherit it automatically. Handling incomplete or erroneous upstream metadata belongs in the operational note, not in an undocumented change to the input semantics.

The union-coverage objective is intentionally limited. It does not measure model obedience, rendering overhead, semantic nuance, output continuity, production drift or whether an extractor omitted a real conflict. Discuss those limits and a safe fallback when introducing the component into a real product.

The small example and larger requests are synthetic work data. The 250–500 active-card workload is an assessment requirement, not a measured production distribution. Do not turn benchmark latency or quality into a claim about OpenArt's current customers or production failure rates. Document measured environment differences and distinguish measurements from estimates.
