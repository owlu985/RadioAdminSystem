import os
import math
import wave
import contextlib

def rms_dbfs(samples):
	if not samples:
		return -100.0
	rms = math.sqrt(sum(s*s for s in samples) / len(samples))
	if rms == 0:
		return -100.0
	return 20 * math.log10(rms)

def analyze_wav(filepath, window_seconds, config):
	results = []

	try:
		with contextlib.closing(wave.open(filepath, 'rb')) as wf:
			rate = wf.getframerate()
			channels = wf.getnchannels()
			width = wf.getsampwidth()

			frames_per_window = int(rate * window_seconds)
			total_frames = wf.getnframes()

			while True:
				frames = wf.readframes(frames_per_window)
				if not frames:
					break

				samples = []
				for i in range(0, len(frames), width):
					sample = int.from_bytes(
						frames[i:i+width],
						byteorder='little',
						signed=True
					)
					samples.append(sample / (2 ** (8 * width - 1)))

				db = rms_dbfs(samples)

				if db <= config.DEAD_AIR_DB:
					state = "dead_air"
				elif config.AUTOMATION_DB_MIN <= db <= config.AUTOMATION_DB_MAX:
					state = "automation"
				else:
					state = "live"

				results.append(state)

	except Exception as e:
		return {
			"error": str(e),
			"states": []
		}

	return {
		"states": results,
		"total_windows": len(results)
	}
