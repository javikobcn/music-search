def score_quality(file):
    score = 0
    reasons = []

    if file.get("extension", "").casefold() == "flac":
        score += 20
        reasons.append("+20 FLAC")

    if file.get("bitDepth", 0) == 24:
        score += 20
        reasons.append("+20 24-bit")

    if file.get("sampleRate", 0) >= 96000:
        score += 10
        reasons.append("+10 Hi-Res")

    return score, reasons