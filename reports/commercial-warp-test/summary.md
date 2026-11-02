# Temporary WARP commercial-source diagnostic

One baseline request and one WARP request per public entry point; no login/DRM/auth bypass.

| Source | Before | WARP | Bytes before | Bytes WARP |
|---|---:|---:|---:|---:|
| stc | 200 | 200 | 20695 | 20695 |
| gobx | 403 | 403 | 5627 | 5627 |
| starzplay | 403 | 200 | 919 | 2054302 |
| dubaiplus | 200 | 200 | 7193 | 7193 |
| shahid_web | 200 | 200 | 545251 | 545027 |
| shahid_api | 400 | 400 | 157 | 157 |

## Parser checks (2-hour window)

- starzplay-ar: HTTP=200 channels=0 programmes=0 error=
- starzplay-en: HTTP=200 channels=0 programmes=0 error=
- dubaiplus: HTTP=200 channels=9 programmes=38 error=
