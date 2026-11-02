#!/usr/bin/env bash
set -uo pipefail

SOURCE="${1:?source required}"
HOURS="${2:-48}"
ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
UPSTREAM="$ROOT/vendor/iptv-org-epg"
mkdir -p "$ROOT/output/source-build" "$ROOT/output/final" "$ROOT/output/morocco" "$ROOT/reports" "$ROOT/data"

empty_xml() {
  printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?><tv></tv>' > "$1"
}

has_programmes() {
  grep -q '<programme ' "$1" 2>/dev/null
}

grab_upstream() {
  key="$1"; channels="$2"; output="$3"; delay="${4:-}"
  set +e
  cd "$UPSTREAM"
  cmd=(npm run grab -- "--channels=$channels" "--output=$output" --days=3 --maxConnections=1 --timeout=20000)
  [ -n "$delay" ] && cmd+=("--delay=$delay")
  timeout --signal=TERM --kill-after=30s 900s "${cmd[@]}"
  rc=$?
  cd "$ROOT"
  [ -s "$output" ] || empty_xml "$output"
  return $rc
}

pack_source() {
  key="$1"; input="$2"; catalogue="${3:-}"
  args=(python tools/source_feed_pack.py --input "$input" --output-gz "output/final/$key.xml.gz" --output-txt "output/final/$key.txt" --stats "output/final/$key.json" --label "$key" --window-hours "$HOURS")
  [ -n "$catalogue" ] && [ -s "$catalogue" ] && args+=(--catalogue "$catalogue")
  "${args[@]}"
}

case "$SOURCE" in
  bein)
    grab_upstream bein "$ROOT/output/source-build/bein.channels.xml" "$ROOT/output/source-build/bein.raw.xml" "" || true
    grab_upstream bein-en "$ROOT/output/source-build/bein_en.channels.xml" "$ROOT/output/source-build/bein.en.raw.xml" "500" || true
    pack_source bein output/source-build/bein.raw.xml output/source-build/bein.channels.xml || true
    if [ -s output/final/bein.xml.gz ]; then
      python tools/bein_arabize.py --input output/final/bein.xml.gz --output output/final/bein.ar.xml.gz --cache data/bein_translation_cache.json --report reports/bein-translation.json || true
      [ -s output/final/bein.ar.xml.gz ] && mv output/final/bein.ar.xml.gz output/final/bein.xml.gz
    fi
    # Titles come from the full English beIN guide; descriptions remain/are translated to Arabic.
    if [ -s output/final/bein.xml.gz ] && has_programmes output/source-build/bein.en.raw.xml; then
      python tools/bein_hybrid.py --input output/final/bein.xml.gz --english output/source-build/bein.en.raw.xml --policy config/bein-language-policy.json --cache data/bein_hybrid_translation_cache.json --output output/final/bein.hybrid.xml.gz --report reports/bein-hybrid.json || true
      if [ -s output/final/bein.hybrid.xml.gz ]; then
        # Re-pack the final hybrid XML so bein.json / bein.txt always describe
        # exactly the XML that will be published to the receiver.
        pack_source bein output/final/bein.hybrid.xml.gz output/source-build/bein.channels.xml || true
        rm -f output/final/bein.hybrid.xml.gz
      fi
    fi
    ;;

  osn)
    cfg="$UPSTREAM/sites/osn.com/osn.com.config.js"
    cfg_orig="$UPSTREAM/sites/osn.com/osn.com.config.js.epgmanager.multi.orig"
    cp "$cfg" "$cfg_orig"

    patch_osn_platform() {
      platform="$1"
      cp "$cfg_orig" "$cfg"
      python - "$cfg" "$platform" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
platform = sys.argv[2]
text = p.read_text(encoding="utf-8")
if "platform=Android" not in text:
    raise SystemExit("OSN adapter no longer contains platform=Android")
text = text.replace("platform=Android", f"platform={platform}")
if "boxAndroid" in text:
    text = text.replace("boxAndroid", f"box{platform}")
# Legacy currently contains many catalogue rows with an empty GUID.  The
# upstream adapter used to batch those empty values into schedule requests,
# which made valid Legacy channels return zero programmes.  Never send blank
# provider GUIDs in OSN schedule batches (harmless for Android too).
old_segments = "const _channels = [...allChannels.map(item => item.guid)]"
new_segments = "const _channels = [...allChannels.map(item => item.guid).filter(Boolean)]"
if old_segments in text:
    text = text.replace(old_segments, new_segments, 1)
p.write_text(text, encoding="utf-8")
print(f"OSN adapter platform={platform}; blank GUIDs filtered")
PY
    }

    merge_osn_raw() {
      primary="$1"
      secondary="$2"
      output="$3"
      if has_programmes "$primary"; then
        if has_programmes "$secondary"; then
          python tools/source_gap_retry.py merge \
            --primary "$primary" \
            --retry "$secondary" \
            --output "$output" || cp "$primary" "$output"
        else
          cp "$primary" "$output"
        fi
      elif has_programmes "$secondary"; then
        cp "$secondary" "$output"
      else
        empty_xml "$output"
      fi
    }

    patch_osn_platform Android
    grab_upstream osn-android-ar \
      "$ROOT/output/source-build/osn_android.channels.xml" \
      "$ROOT/output/source-build/osn.android.raw.xml" "" || true
    grab_upstream osn-android-en \
      "$ROOT/output/source-build/osn_android_en.channels.xml" \
      "$ROOT/output/source-build/osn.android.en.raw.xml" "" || true

    other_platform=""
    if [ -s "$ROOT/output/source-build/osn_other_platform.txt" ]; then
      other_platform="$(tr -d '\r\n' < "$ROOT/output/source-build/osn_other_platform.txt")"
    fi

    if [ -n "$other_platform" ] && grep -q '<channel ' "$ROOT/output/source-build/osn_other.channels.xml" 2>/dev/null; then
      patch_osn_platform "$other_platform"
      grab_upstream osn-other-ar \
        "$ROOT/output/source-build/osn_other.channels.xml" \
        "$ROOT/output/source-build/osn.other.raw.xml" "" || true
      grab_upstream osn-other-en \
        "$ROOT/output/source-build/osn_other_en.channels.xml" \
        "$ROOT/output/source-build/osn.other.en.raw.xml" "" || true
    else
      empty_xml "$ROOT/output/source-build/osn.other.raw.xml"
      empty_xml "$ROOT/output/source-build/osn.other.en.raw.xml"
    fi

    cp "$cfg_orig" "$cfg"

    merge_osn_raw \
      output/source-build/osn.android.raw.xml \
      output/source-build/osn.other.raw.xml \
      output/source-build/osn.raw.xml
    merge_osn_raw \
      output/source-build/osn.android.en.raw.xml \
      output/source-build/osn.other.en.raw.xml \
      output/source-build/osn.en.raw.xml

    # OSN remains authoritative. Fill only IDs that still have no future EPG
    # using fallbacks already verified by the repository audits.
    python tools/osn_zero_epg_fallback.py \
      --input output/source-build/osn.raw.xml \
      --output output/source-build/osn.fallback.raw.xml \
      --report reports/osn-zero-epg-fallback.json || true
    if [ -s output/source-build/osn.fallback.raw.xml ]; then
      mv output/source-build/osn.fallback.raw.xml output/source-build/osn.raw.xml
    fi

    if has_programmes output/source-build/osn.raw.xml && has_programmes output/source-build/osn.en.raw.xml; then
      python tools/osn_hybrid.py \
        --arabic output/source-build/osn.raw.xml \
        --english output/source-build/osn.en.raw.xml \
        --policy config/osn-language-policy.json \
        --mode hybrid \
        --output output/source-build/osn.hybrid.raw.xml \
        --report reports/osn-hybrid.json || true
      [ -s output/source-build/osn.hybrid.raw.xml ] && mv output/source-build/osn.hybrid.raw.xml output/source-build/osn.raw.xml
    fi
    pack_source osn output/source-build/osn.raw.xml output/source-build/osn.channels.xml || true
    ;;

  shahid)
    grab_upstream shahid "$ROOT/output/source-build/shahid.channels.xml" "$ROOT/output/source-build/shahid.raw.xml" "" || true
    pack_source shahid output/source-build/shahid.raw.xml output/source-build/shahid.channels.xml || true
    ;;

  elcinema)
    grab_upstream elcinema "$ROOT/output/source-build/elcinema.channels.xml" "$ROOT/output/source-build/elcinema.raw.xml" "1000" || true
    python tools/elcinema_hybrid.py catalogue --fallback-catalogue output/source-build/elcinema_fallback.channels.xml --policy config/elcinema-language-policy.json --output output/source-build/elcinema_hybrid_en.channels.xml || true

    python tools/source_gap_retry.py build --catalogue output/source-build/elcinema.channels.xml --fallback-catalogue output/source-build/elcinema_fallback.channels.xml --raw output/source-build/elcinema.raw.xml --output output/source-build/elcinema.retry.channels.xml || true
    if grep -q '<channel ' output/source-build/elcinema.retry.channels.xml 2>/dev/null; then
      grab_upstream elcinema-retry "$ROOT/output/source-build/elcinema.retry.channels.xml" "$ROOT/output/source-build/elcinema.retry.raw.xml" "1000" || true
      if has_programmes output/source-build/elcinema.retry.raw.xml; then
        python tools/source_gap_retry.py merge --primary output/source-build/elcinema.raw.xml --retry output/source-build/elcinema.retry.raw.xml --output output/source-build/elcinema.merged.xml || true
        [ -s output/source-build/elcinema.merged.xml ] && mv output/source-build/elcinema.merged.xml output/source-build/elcinema.raw.xml
      fi
    fi

    # Fill only ElCinema catalogue IDs that still have no future EPG.
    # Priority: verified production feeds -> EPGShare exact match -> official Al-Manar.
    python tools/elcinema_zero_epg_fallback.py \
      --input output/source-build/elcinema.raw.xml \
      --output output/source-build/elcinema.fallback.raw.xml \
      --report reports/elcinema-zero-epg-fallback.json || true
    if [ -s output/source-build/elcinema.fallback.raw.xml ]; then
      mv output/source-build/elcinema.fallback.raw.xml output/source-build/elcinema.raw.xml
    fi

    # Request only ElCinema IDs whose authentic 48h guide ends prematurely.
    # The retry can EXTEND the feed but cannot overwrite a valid first-pass slot.
    python tools/elcinema_short_horizon_retry.py build \
      --catalogue output/source-build/elcinema.channels.xml \
      --fallback-catalogue output/source-build/elcinema_fallback.channels.xml \
      --raw output/source-build/elcinema.raw.xml \
      --min-hours 40 --max-ids 45 \
      --output output/source-build/elcinema.short.channels.xml \
      --report reports/elcinema-short-retry-selection.json || true
    if grep -q '<channel ' output/source-build/elcinema.short.channels.xml 2>/dev/null; then
      grab_upstream elcinema-short "$ROOT/output/source-build/elcinema.short.channels.xml" \
        "$ROOT/output/source-build/elcinema.short.raw.xml" "1000" || true
      if has_programmes output/source-build/elcinema.short.raw.xml; then
        python tools/elcinema_short_horizon_retry.py extend \
          --primary output/source-build/elcinema.raw.xml \
          --retry output/source-build/elcinema.short.raw.xml \
          --output output/source-build/elcinema.extended.raw.xml \
          --report reports/elcinema-short-retry-result.json || true
        if [ -s output/source-build/elcinema.extended.raw.xml ]; then
          mv output/source-build/elcinema.extended.raw.xml output/source-build/elcinema.raw.xml
        fi
      fi
    fi

    grab_upstream elcinema-en "$ROOT/output/source-build/elcinema_hybrid_en.channels.xml" "$ROOT/output/source-build/elcinema.hybrid.en.raw.xml" "1000" || true
    if has_programmes output/source-build/elcinema.raw.xml && has_programmes output/source-build/elcinema.hybrid.en.raw.xml; then
      python tools/elcinema_hybrid.py merge --arabic output/source-build/elcinema.raw.xml --english output/source-build/elcinema.hybrid.en.raw.xml --policy config/elcinema-language-policy.json --cache data/elcinema_translation_cache.json --output output/source-build/elcinema.hybrid.raw.xml --report reports/elcinema-hybrid.json || true
      [ -s output/source-build/elcinema.hybrid.raw.xml ] && mv output/source-build/elcinema.hybrid.raw.xml output/source-build/elcinema.raw.xml
    fi
    pack_source elcinema output/source-build/elcinema.raw.xml output/source-build/elcinema.channels.xml || true
    ;;

  morocco)
    if [ -s feeds/morocco.xml.gz ]; then
      python tools/morocco_epg.py --output-dir output/morocco --days 3 --previous feeds/morocco.xml.gz || true
    else
      python tools/morocco_epg.py --output-dir output/morocco --days 3 || true
    fi
    if [ -s output/morocco/morocco.xml.gz ]; then
      pack_source morocco output/morocco/morocco.xml.gz "" || true
    fi
    ;;


  tabie)
    python tools/tabie_source.py --days 3
    pack_source tabie output/source-build/tabie.raw.xml output/source-build/tabie.channels.xml || true
    ;;

  alkass)
    python tools/alkass_source.py
    pack_source alkass output/source-build/alkass.raw.xml output/source-build/alkass.channels.xml || true
    ;;

  tunisiatv)
    python tools/tunisiatv_source.py
    pack_source tunisiatv output/source-build/tunisiatv.raw.xml output/source-build/tunisiatv.channels.xml || true
    ;;

  rotana)
    python tools/rotana_source.py --output output/source-build/rotana.raw.xml --report reports/rotana-scrape.json --window-hours "$HOURS" || true
    [ -s output/source-build/rotana.raw.xml ] || empty_xml output/source-build/rotana.raw.xml
    # Only when the official Rotana programme list is empty for a matching
    # channel: borrow authentic STC programme rows if that exact STC ID is
    # SHA-pinned, recent, content VERIFIED, and has a confirmed identity.
    # Rotana's seven genuine guides always win; Clip may remain empty when
    # its STC counterpart only contains repeating generic live programming.
    if python tools/rotana_verified_stc_fallback.py \
        --input output/source-build/rotana.raw.xml \
        --output output/source-build/rotana.verified.raw.xml \
        --feed-dir feeds \
        --report reports/rotana-stc-fallback.json; then
      [ -s output/source-build/rotana.verified.raw.xml ] && \
          mv output/source-build/rotana.verified.raw.xml output/source-build/rotana.raw.xml
    fi
    pack_source rotana output/source-build/rotana.raw.xml "" || true
    ;;

  sport24)
    python tools/sport24_source.py --output output/source-build/sport24.raw.xml --report reports/sport24-scrape.json --window-hours "$HOURS" || true
    [ -s output/source-build/sport24.raw.xml ] || empty_xml output/source-build/sport24.raw.xml
    # Sport24 exposes only ~4h of genuine forward EPG. For THREE
    # exact-identity channels only, fill non-overlapping future gaps using
    # already published, SHA-verified official OSN/Dubai+ 48h guides.
    # Never replace a Sport24 programme or invent EPG for Thmanyah 1/3.
    if python tools/sport24_verified_donors.py \
        --input output/source-build/sport24.raw.xml \
        --output output/source-build/sport24.verified.raw.xml \
        --feed-dir feeds \
        --report reports/sport24-donors.json; then
      [ -s output/source-build/sport24.verified.raw.xml ] && \
          mv output/source-build/sport24.verified.raw.xml output/source-build/sport24.raw.xml
    fi
    pack_source sport24 output/source-build/sport24.raw.xml "" || true
    ;;

  dubaiplus)
    python tools/dubaiplus_api_source.py --output output/source-build/dubaiplus.raw.xml --report reports/dubaiplus-scrape.json --hours "$HOURS" || true
    [ -s output/source-build/dubaiplus.raw.xml ] || empty_xml output/source-build/dubaiplus.raw.xml
    # Do not replace Dubai+ native schedules. Only Dubai TV's real programme
    # gaps may use an exact-identity, audited, SHA-verified OSN 48h guide.
    # Missing authentic descriptions and the other source gaps stay visible.
    if python tools/dubaiplus_verified_osn_gap.py \
        --input output/source-build/dubaiplus.raw.xml \
        --output output/source-build/dubaiplus.verified.raw.xml \
        --feed-dir feeds \
        --report reports/dubaiplus-osn-gaps.json; then
      [ -s output/source-build/dubaiplus.verified.raw.xml ] && \
          mv output/source-build/dubaiplus.verified.raw.xml output/source-build/dubaiplus.raw.xml
    fi
    pack_source dubaiplus output/source-build/dubaiplus.raw.xml "" || true
    ;;

  aljazeera)
    python tools/aljazeera_source.py --output output/source-build/aljazeera.raw.xml --report reports/aljazeera-scrape.json --hours "$HOURS" || true
    [ -s output/source-build/aljazeera.raw.xml ] || empty_xml output/source-build/aljazeera.raw.xml
    pack_source aljazeera output/source-build/aljazeera.raw.xml "" || true
    ;;

  stctv)
    # Keep the COMPLETE STC catalogue: current public provider exposes 149
    # channels (including STARZPLAY Sports 1-3), not just three sports IDs.
    # The adapter uses batch-per-UTC-day endpoints (~5 HTTP requests) and
    # preserves original STC rows. Per-ID content quality is audited AFTER
    # the accepted published bytes are SHA-pinned below.
    python tools/stctv_source.py --output output/source-build/stctv.raw.xml --report reports/stctv-scrape.json --id-index feeds/mena.txt --window-hours "$HOURS" --delay 0.2 --max-channels 0 --profile all || true
    [ -s output/source-build/stctv.raw.xml ] || empty_xml output/source-build/stctv.raw.xml
    pack_source stctv output/source-build/stctv.raw.xml "" || true
    ;;

  starzplay)
    for lang in ar en; do
      # Primary: official STARZPLAY public EPG API. It discovers the complete
      # current TV catalogue dynamically and preserves channels with 0 EPG.
      python tools/starzplay_api_source.py \
        --lang "$lang" \
        --output "output/source-build/starzplay.$lang.raw.xml" \
        --report "reports/starzplay-$lang-api-scrape.json" \
        --id-index feeds/mena.txt \
        --countries AE,SA,KW,QA,BH,OM \
        --hours "$HOURS" || true

      # Secondary: public rendered web metadata, only if the API produced no
      # programmes. This is intentionally not allowed to replace a healthy API.
      if ! has_programmes "output/source-build/starzplay.$lang.raw.xml"; then
        python tools/starzplay_source.py \
          --lang "$lang" \
          --output "output/source-build/starzplay.$lang.web.xml" \
          --report "reports/starzplay-$lang-scrape.json" \
          --id-index feeds/mena.txt \
          --country AE \
          --city Dubai \
          --hours "$HOURS" || true
        if has_programmes "output/source-build/starzplay.$lang.web.xml"; then
          mv "output/source-build/starzplay.$lang.web.xml" "output/source-build/starzplay.$lang.raw.xml"
        fi
      fi
      [ -s "output/source-build/starzplay.$lang.raw.xml" ] || empty_xml "output/source-build/starzplay.$lang.raw.xml"
    done
    if has_programmes output/source-build/starzplay.ar.raw.xml; then
      cp output/source-build/starzplay.ar.raw.xml output/source-build/starzplay.raw.xml
      if has_programmes output/source-build/starzplay.en.raw.xml; then
        python tools/starzplay_hybrid.py --arabic output/source-build/starzplay.ar.raw.xml --english output/source-build/starzplay.en.raw.xml --policy config/starzplay-language-policy.json --output output/source-build/starzplay.hybrid.raw.xml --report reports/starzplay-hybrid.json || true
        [ -s output/source-build/starzplay.hybrid.raw.xml ] && mv output/source-build/starzplay.hybrid.raw.xml output/source-build/starzplay.raw.xml
      fi
      pack_source starzplay output/source-build/starzplay.raw.xml "" || true
      printf '%s\n' '{"mode":"direct-starzplay","fallback":false,"scope":"GCC union AE,SA,KW,QA,BH,OM"}' > reports/starzplay-source-mode.json
    else
      echo "STARZPLAY direct endpoint unavailable; keeping the official STARZPLAY live catalogue and using STC metadata where schedules overlap."
      python tools/stctv_source.py \
        --output output/source-build/starzplay.raw.xml \
        --report reports/starzplay-fallback-stctv.json \
        --id-index feeds/mena.txt \
        --window-hours "$HOURS" \
        --delay 0.2 \
        --max-channels 0 \
        --profile starzplay-live || true
      if has_programmes output/source-build/starzplay.raw.xml; then
        pack_source starzplay output/source-build/starzplay.raw.xml "" || true
        printf '%s\n' '{"mode":"starzplay-live-catalogue+stctv-epg-fallback","fallback":true,"scope":"official STARZPLAY live catalogue; STC schedules used only for matching channels"}' > reports/starzplay-source-mode.json
      else
        echo "STARZPLAY fallback also unavailable; LKG will be preserved."
      fi
    fi
    ;;

  *)
    echo "Unsupported source: $SOURCE" >&2
    exit 2
    ;;
esac

# The publisher decides whether to accept this candidate or keep the last
# known good (LKG) publication. NEVER commit reports about rejected bytes.
python tools/publish_lkg.py --source "$SOURCE" --report "reports/$SOURCE-publish-lkg.json"

# Only an accepted, byte-for-byte verified published source may create a
# new 48h/quality cohort. KEEP_LKG leaves prior reports unchanged.
cohort="$(python tools/report_cohort_gate.py --source "$SOURCE" \
  --decision "reports/$SOURCE-publish-lkg.json" --feeds-dir feeds)" || exit 2
if [ "$cohort" = "REBUILD" ]; then
  python tools/validate_48h.py "feeds/$SOURCE.xml.gz" --hours "$HOURS" \
    --max-gap-minutes 90 --report "reports/$SOURCE-48h-audit.json" || true
  python tools/audit_source_quality.py --input "feeds/$SOURCE.xml.gz" \
    --source "$SOURCE" --report "reports/$SOURCE-quality.json"
  if [ "$SOURCE" = "osn" ]; then
    # Publish a truthful exact-ID readiness index only for an accepted feed.
    # No 0-EPG OSN ID may be marked as a verified Best Match. Preserve all
    # catalogue IDs for manual correction; never create fake programme slots.
    python tools/osn_per_id_health.py \
      --feed feeds/osn.xml.gz \
      --metadata feeds/osn.json \
      --output reports/osn-per-id-health.json
  fi
  if [ "$SOURCE" = "elcinema" ]; then
    # Exact published-feed SHA pinned, 36h green cutoff per ID.
    # Short but genuine IDs stay visible for manual review; a channel-name
    # schedule may never appear as verified EPG. Does not touch XMLTV.
    python tools/elcinema_per_id_readiness.py \
      --feed feeds/elcinema.xml.gz \
      --metadata feeds/elcinema.json \
      --output reports/elcinema-per-id-readiness.json
  fi
  if [ "$SOURCE" = "stctv" ]; then
    # The 48h time audit alone cannot distinguish true programmes from
    # upstream channel-name loops. SHA-verified exact IDs stay published,
    # but suspicious / repetitive rows are REVIEW, never Best Match green.
    # Receiver mappings and STC Sports 1-3 identity remain unchanged.
    python tools/stctv_per_id_readiness.py \
      --feed feeds/stctv.xml.gz \
      --metadata feeds/stctv.json \
      --output reports/stctv-per-id-readiness.json
  fi
elif [ "$cohort" = "KEEP" ]; then
  echo "KEEP_LKG source=$SOURCE: retain previous 48h and quality reports"
else
  echo "Unsafe publication cohort decision: $cohort" >&2
  exit 2
fi
echo "SOURCE_REFRESH_DONE source=$SOURCE hours=$HOURS"

# Current-health is intentionally a fresh observation of the accepted bytes,
# even on KEEP_LKG; it does not relabel old quality reports as new publication.
python tools/source_health_snapshot.py --source "$SOURCE" \
  --output "reports/$SOURCE-current-health.json" || exit 2
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  printf '\n### Published source health (%s, GMT+0)\n\n' "$SOURCE" >> "$GITHUB_STEP_SUMMARY"
  printf 'Scrape success and published guide availability are separate.\n\n```json\n' >> "$GITHUB_STEP_SUMMARY"
  cat "reports/$SOURCE-current-health.json" >> "$GITHUB_STEP_SUMMARY"
  printf '\n```\n' >> "$GITHUB_STEP_SUMMARY"
fi
