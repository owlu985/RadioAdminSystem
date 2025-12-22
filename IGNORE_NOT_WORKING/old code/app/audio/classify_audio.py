from collections import Counter

def classify(states, config):
	if not states:
		return {
			"classification": "unknown",
			"reason": "no_audio"
		}

	counts = Counter(states)
	total = len(states)

	live_pct = counts["live"] / total
	dead_pct = counts["dead_air"] / total

	if dead_pct > config.MAX_DEAD_AIR_PERCENT:
		return {
			"classification": "missed_show",
			"reason": "excessive_dead_air"
		}

	if live_pct >= config.MIN_LIVE_PERCENT:
		return {
			"classification": "live_show",
			"reason": "sufficient_live_audio"
		}

	if counts["automation"] > counts["live"]:
		return {
			"classification": "automation_only",
			"reason": "automation_dominant"
		}

	return {
		"classification": "unknown",
		"reason": "ambiguous_audio"
	}
