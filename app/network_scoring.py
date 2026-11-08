def score_network(file):

    score = 0
    reasons = []

    if file.get("hasFreeUploadSlot"):
        score += 15
        reasons.append("+15 Free Slot")

    if file.get("queueLength", 9999) == 0:
        score += 20
        reasons.append("+20 Queue")

    upload_speed = file.get("uploadSpeed", 0)

    if upload_speed >= 5_000_000:
        score += 20
        reasons.append("+20 Fast User")

    elif upload_speed >= 1_000_000:
        score += 10
        reasons.append("+10 Good Speed")

    return score, reasons