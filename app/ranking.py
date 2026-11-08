import re
from rapidfuzz import fuzz

from .similarity_scoring import (
    score_similarity,
    get_artist_similarity,
)
from .quality_scoring import score_quality
from .network_scoring import score_network
from .version_scoring import score_version
from .scoring_rules import apply_penalties, apply_bonuses
from .album_scoring import score_album
from .version_detector import detect_version



def apply_rule(score, reasons, result):

    rule_score, rule_reasons = result

    score += rule_score
    reasons.extend(rule_reasons)

    return score, reasons

def score_file(file, artist, track, album=None):

    filename = file["filename"].lower()

    search_context = " ".join(
        value for value in (artist, track, album) if value
    )   

    score = 0
    reasons = []

    # ---------- Similaridad ----------

    score, reasons = apply_rule(
        score,
        reasons,
        score_similarity(
            filename,
            artist,
            track
        ),
    )

    # ---------- Album ----------

    score, reasons = apply_rule(
        score,
        reasons,
        score_album(
            filename,
            album,
        ),
    )

    # ---------- Calidad ----------

    score, reasons = apply_rule(
        score,
        reasons,
        score_quality(file),
    )

    # ---------- Red ----------

    score, reasons = apply_rule(
        score,
        reasons,
        score_network(file),
    )

    # ---------- Version ----------

    version_score, version_reasons = score_version(
        search_context,
        filename,
    )
    score += version_score
    reasons.extend(version_reasons)

    # ---------- Penalties ----------

    penalty_score, penalty_reasons = apply_penalties(
        filename,
        search_text=search_context,
    )
    score += penalty_score
    reasons.extend(penalty_reasons)

    # ---------- Bonuses ----------

    score, reasons = apply_rule(
        score,
        reasons,
        apply_bonuses(filename),
    )
    
    return {
        "score": score,
        "reasons": reasons,
    }

def normalize_match_text(value: str) -> str:
    value = value.casefold()

    value = (
        value
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("/", " ")
        .replace("+", " ")
        .replace("&", " ")
    )

    value = re.sub(r"[^\w\s']", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def find_best_match(results, artist, track, album=None):
    if not results:
        return None

    target = normalize_match_text(track)

    search_context = " ".join(
        value
        for value in (artist, track, album)
        if value
    )

    requested_version = detect_version(search_context)

    valid_candidates = []

    for candidate_result in results:
        candidate = normalize_match_text(
            candidate_result.filename
        )

        # --------------------------------------------------
        # ARTISTA OBLIGATORIO
        # --------------------------------------------------

        artist_identity = get_artist_similarity(
            candidate_result.filename,
            artist,
        )

        if artist_identity < 75:
            print(
                f"[MATCH REJECT ARTIST] "
                f"requested={artist!r} | "
                f"score={artist_identity:.1f} | "
                f"file={candidate_result.filename!r}",
                flush=True,
            )

            continue

        # --------------------------------------------------
        # TÍTULO
        # --------------------------------------------------

        identity_score = fuzz.token_set_ratio(
            target,
            candidate,
        )

        if identity_score < 65:
            continue

        candidate_version = detect_version(
            candidate_result.filename
        )

        # La versión solicitada tiene prioridad absoluta.
        # Un original no puede sustituir a un remix y viceversa.
        if candidate_version != requested_version:
            continue

        valid_candidates.append(
            (
                candidate_result,
                identity_score,
            )
        )

    if not valid_candidates:
        print(
            f"[MATCH] ningún candidato válido "
            f"para track={track!r} | "
            f"version={requested_version!r}",
            flush=True,
        )
        return None

    # Si existen candidatos con identidad alta,
    # descartamos los de identidad dudosa.
    high_identity = [
        item
        for item in valid_candidates
        if item[1] >= 90
    ]

    if high_identity:
        valid_candidates = high_identity

    lossless_extensions = {
        "flac",
        "wav",
        "alac",
        "aiff",
    }

    lossless_candidates = [
        item
        for item in valid_candidates
        if (
            item[0].extension or ""
        ).casefold() in lossless_extensions
    ]

    def lossless_quality_key(item):
        candidate_result, identity_score = item

        return (
            candidate_result.bitDepth or 0,
            candidate_result.sampleRate or 0,
            candidate_result.score,
            identity_score,
        )

    # 1. Siempre buscamos primero el mejor lossless.
    if lossless_candidates:
        lossless_candidates.sort(
            key=lossless_quality_key,
            reverse=True,
        )

        best, identity_score = lossless_candidates[0]

        print(
            f"[MATCH] track={track!r} | "
            f"identity={identity_score:.1f} | "
            f"version={requested_version} | "
            f"quality="
            f"{best.extension}/"
            f"{best.bitDepth}/"
            f"{best.sampleRate} | "
            f"score={best.score} | "
            f"file={best.filename!r}",
            flush=True,
        )

        return best

    # 2. Si NO existe lossless válido,
    # permitimos MP3 ~320 kbps como último recurso.
    def bitrate_kbps(candidate_result):
        bitrate = candidate_result.bitRate or 0

        # slskd puede devolver kbps o bps dependiendo
        # de los metadatos disponibles.
        if bitrate > 10000:
            return bitrate / 1000

        return bitrate

    mp3_320_candidates = [
        item
        for item in valid_candidates
        if (
            (item[0].extension or "").casefold() == "mp3"
            and bitrate_kbps(item[0]) >= 300
        )
    ]

    if mp3_320_candidates:
        mp3_320_candidates.sort(
            key=lambda item: (
                bitrate_kbps(item[0]),
                item[0].score,
                item[1],
            ),
            reverse=True,
        )

        best, identity_score = mp3_320_candidates[0]

        print(
            f"[MATCH MP3 FALLBACK] "
            f"track={track!r} | "
            f"identity={identity_score:.1f} | "
            f"version={requested_version} | "
            f"bitrate={bitrate_kbps(best):.0f} kbps | "
            f"score={best.score} | "
            f"file={best.filename!r}",
            flush=True,
        )

        return best

    print(
        f"[MATCH] sin lossless ni MP3 320 válido "
        f"para track={track!r} | "
        f"version={requested_version!r}",
        flush=True,
    )

    return None